// =============================================================================
// ui/screens/screen_player.cpp — Active player + standby layouts
// =============================================================================
// Gesture model (v4.2):
//
//   All touch decisions are made on release in on_released(). LVGL's own
//   SHORT_CLICKED / GESTURE events are NOT wired — they were a second,
//   partially-overlapping detector that produced inconsistent results.
//
//   On release, in this order:
//     1. rotary_consumed (any volume change happened during this touch)
//          → nothing — rotation handled the input
//     2. |dx| > SWIPE_MIN_PX  AND  |dx| > SWIPE_RATIO * |dy|
//          → NEXT (leftward) / PREV (rightward)
//     3. otherwise
//          → PLAYPAUSE
//
//   Rotary mode is gated on press-down location:
//     • press in outer ring (r > ROTARY_INNER_R) → rotary active
//     • press in centre                          → swipe / tap only
//
//   Every action fires an action-icon toast — a 100×100 custom-drawn glyph
//   in accent colour, fade-in / hold / fade-out. Drawn geometrically (no
//   font dependency) so it works regardless of which font is loaded.
//
// Title/artist scroll speed is configured globally via LV_LABEL_DEF_SCROLL_SPEED
// in include/lv_conf.h (px/sec, lower = slower).
// =============================================================================

#include "screens/screen_player.h"
#include "screens/center_stage.h"
#include "screens/screen_standby.h"
#include "state.h"
#include "theme.h"
#include "proto.h"

#include <Arduino.h>
#include <lvgl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

namespace ScreenPlayer {

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ─── LVGL objects ───────────────────────────────────────────────────────────

static lv_obj_t *scr           = nullptr;

// Custom-draw layers (back→front)
static lv_obj_t *vol_layer     = nullptr;   // 24-dot vol ring (lit dots wobble with energy)
static lv_obj_t *prog_layer    = nullptr;   // 60-dot progress stipple

// Player widgets
static lv_obj_t *source_marker = nullptr;
static lv_obj_t *lbl_source    = nullptr;
static lv_obj_t *lbl_title     = nullptr;
static lv_obj_t *lbl_artist    = nullptr;
static lv_obj_t *state_icon    = nullptr;
// lbl_volume removed — CenterStage shows MUTE when volume == 0

static bool     created            = false;
static bool     in_standby         = false;
static bool     in_shutdown        = false;
static uint32_t last_energy_render = 0;
// Low-passed app.energy in [0..1]. Updates from State::app.energy at the
// 60 Hz repaint tick; vol-wobble and source-marker pulse both read from
// this so they share inertia and stay phase-aligned.
static float    energy_smoothed    = 0.0f;

// Precomputed dot positions
static int   vol_x[24],    vol_y[24];
static int   prog_x[60],   prog_y[60];

// Title/artist text-opacity tween state. Snap-changing opa when CenterStage
// toggles looked janky; the labels now tween to their target over a fixed
// duration via lv_anim. last_*_opa is the most recently committed target —
// we only kick off a new anim when it actually changes.
static constexpr uint32_t STAGE_FADE_MS = 220;
static lv_opa_t  last_title_opa  = LV_OPA_COVER;
static lv_opa_t  last_artist_opa = LV_OPA_COVER;
static lv_anim_t anim_title_opa;
static lv_anim_t anim_artist_opa;

static void title_opa_cb (void *var, int32_t v) { lv_obj_set_style_text_opa((lv_obj_t *)var, (lv_opa_t)v, 0); }
static void artist_opa_cb(void *var, int32_t v) { lv_obj_set_style_text_opa((lv_obj_t *)var, (lv_opa_t)v, 0); }

static void start_text_opa_anim(lv_obj_t *obj, lv_anim_t *anim,
                                lv_anim_exec_xcb_t cb,
                                lv_opa_t from, lv_opa_t to)
{
    if (!obj) return;
    lv_anim_del(obj, cb);
    lv_anim_init(anim);
    lv_anim_set_var(anim, obj);
    lv_anim_set_exec_cb(anim, cb);
    lv_anim_set_values(anim, from, to);
    lv_anim_set_time(anim, STAGE_FADE_MS);
    lv_anim_set_path_cb(anim, lv_anim_path_ease_out);
    lv_anim_start(anim);
}

// ─── Rotary volume state ────────────────────────────────────────────────────

static constexpr int   ROTARY_INNER_R       = 140;
static constexpr int   ROTARY_KILL_R        =  80;
static constexpr int   ROTARY_INNER_R_SQ    = ROTARY_INNER_R * ROTARY_INNER_R;
static constexpr int   ROTARY_KILL_R_SQ     = ROTARY_KILL_R  * ROTARY_KILL_R;
static constexpr float ROTARY_DEG_PER_TICK  = 6.0f;
static constexpr float ROTARY_MIN_DEG       = 8.0f;
static constexpr float ROTARY_DELTA_CAP_RAD = 30.0f * (float)M_PI / 180.0f;

static bool  rotary_active          = false;
static bool  rotary_consumed        = false;
static int   rotary_start_vol       = 0;
static float rotary_last_angle_rad  = 0.0f;
static float rotary_accumulated_deg = 0.0f;

// ─── Swipe / tap tracking ───────────────────────────────────────────────────

static constexpr int SWIPE_MIN_PX   = 40;
static constexpr int SWIPE_RATIO_N  = 13;   // → 1.3:1 dx/dy
static constexpr int SWIPE_RATIO_D  = 10;

static int press_start_x = 0, press_start_y = 0;
static int press_last_x  = 0, press_last_y  = 0;

// ─── Helpers ────────────────────────────────────────────────────────────────

static lv_color_t source_color(State::Source s) {
    switch (s) {
        case State::SRC_SPOTIFY:   return Theme::Color::SRC_SPOTIFY;
        case State::SRC_BLUETOOTH: return Theme::Color::SRC_BT;
        case State::SRC_TOSLINK:   return Theme::Color::SRC_TOSLINK;
        case State::SRC_SNAPCAST:  return Theme::Color::SRC_SNAPCAST;
        default:                   return Theme::Color::SRC_NONE;
    }
}

static const char *source_label_text(State::Source s) {
    switch (s) {
        case State::SRC_SPOTIFY:   return "SPOTIFY";
        case State::SRC_BLUETOOTH: return "BLUETOOTH";
        case State::SRC_TOSLINK:   return "TV";
        case State::SRC_SNAPCAST:  return "MULTIROOM";
        default:                   return "";
    }
}

// Per-speaker rotation offset for the three rings (vol/prog/energy).
// Default 0 → matches Zipp Mini 2 with MADCTL=0xA0 panel rotation.
// On speakers without MADCTL rotation (e.g. Beat #1 with DISPLAY_ROTATE_NATIVE),
// set this to 90 so the ring gap lands on the user's right (matches Zipp's
// visual). Text labels are NOT shifted by this — only the polar widgets.
#ifndef UI_RING_OFFSET_DEG
#define UI_RING_OFFSET_DEG 0
#endif

static void precompute_geometry() {
    constexpr float offset = (float)UI_RING_OFFSET_DEG * (float)M_PI / 180.0f;
    for (int i = 0; i < 24; i++) {
        float a = -(float)M_PI / 2.0f + (i / 24.0f) * 2.0f * (float)M_PI + offset;
        vol_x[i] = Theme::CENTER + (int)roundf(cosf(a) * Theme::VOL_RING_R);
        vol_y[i] = Theme::CENTER + (int)roundf(sinf(a) * Theme::VOL_RING_R);
    }
    for (int i = 0; i < 60; i++) {
        float a_deg = (float)Theme::PROG_ARC_START_DEG +
                      (i / 59.0f) * (float)Theme::PROG_ARC_SWEEP_DEG +
                      (float)UI_RING_OFFSET_DEG;
        float a = a_deg * (float)M_PI / 180.0f;
        prog_x[i] = Theme::CENTER + (int)roundf(cosf(a) * Theme::PROG_RING_R);
        prog_y[i] = Theme::CENTER + (int)roundf(sinf(a) * Theme::PROG_RING_R);
    }
}

// Tune a label's LV_LABEL_LONG_SCROLL_CIRCULAR animation so the text moves at
// a fixed px/sec regardless of length. LVGL 9.5 only exposes `anim_time`
// (the whole-cycle duration) — short titles end up creeping while long ones
// rush past with the same number. We measure the rendered text width with
// lv_text_get_size and compute `duration_ms = width / px_per_sec * 1000`.
// Floor at 2000 ms so the scroll doesn't tick fast on short text either.
static void set_scroll_speed_pxs(lv_obj_t *lbl, int px_per_sec) {
    if (!lbl || px_per_sec <= 0) return;
    const char *txt = lv_label_get_text(lbl);
    if (!txt || !txt[0]) return;
    const lv_font_t *font = lv_obj_get_style_text_font(lbl, LV_PART_MAIN);
    int32_t ls = lv_obj_get_style_text_letter_space(lbl, LV_PART_MAIN);
    lv_point_t sz;
    lv_text_get_size(&sz, txt, font, ls, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    if (sz.x < 1) return;
    uint32_t dur = (uint32_t)(((int64_t)sz.x * 1000) / px_per_sec);
    if (dur < 2000) dur = 2000;
    lv_obj_set_style_anim_time(lbl, dur, LV_PART_MAIN);
}

static void draw_dot(lv_layer_t *layer, int cx, int cy, int r,
                     lv_color_t color, lv_opa_t opa) {
    if (opa == LV_OPA_TRANSP || r <= 0) return;
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = color;
    dsc.bg_opa   = opa;
    dsc.radius   = LV_RADIUS_CIRCLE;
    lv_area_t a;
    a.x1 = cx - r;  a.y1 = cy - r;
    a.x2 = cx + r;  a.y2 = cy + r;
    lv_draw_rect(layer, &dsc, &a);
}

static inline int sq_dist_to_center(int x, int y) {
    int dx = x - Theme::CENTER;
    int dy = y - Theme::CENTER;
    return dx * dx + dy * dy;
}

// Stretch the bridge's RMS-derived energy_smoothed (0..1) into a more
// visually dynamic range. Real music RMS sits ~0.55..0.93, so wobble
// amplitude proportional to raw energy looks flat — quiet and loud both
// modulate by similar percentages. Remap (raw - 0.35) * 2.5 puts quiet
// material around 0.5 and loud peaks at 1.2 (touch of overdrive), which
// the wobble/pulse formulas multiply into much more visible swings.
static inline float energy_dyn(float raw) {
    float e = (raw - 0.35f) * 2.5f;
    if (e < 0.0f) e = 0.0f;
    if (e > 1.2f) e = 1.2f;
    return e;
}

// ─── Draw callbacks ─────────────────────────────────────────────────────────

static void vol_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    int vol = State::app.volume;
    int lit = (vol * 24 + 50) / 100;
    // Phase-2 energy modulation. The dynamic-range remap (energy_dyn) is
    // critical: raw RMS sits in 0.55..0.93 during music, which on its own
    // makes the wobble look identical on quiet and loud passages. After
    // remap, quiet → ~0.5, loud → ~1.2, and amplitude 0.65 expands that
    // into a ±33 % (quiet) to ±78 % (loud) radius swing — clearly visible.
    // Sine freq 0.005 ≈ 1.25 s cycle; per-dot phase offset i*0.5 makes
    // the wobble travel around the ring instead of pulsing in sync.
    const float E = energy_dyn(energy_smoothed);
    const float t = (float)millis();
    for (int i = 0; i < 24; i++) {
        if (i == 0) continue;
        bool is_lit = (i < lit);
        if (is_lit) {
            float wob = 1.0f + E * 0.65f * sinf(t * 0.005f + (float)i * 0.5f);
            int   r   = (int)roundf((float)Theme::VOL_DOT_R * wob);
            if (r < 1) r = 1;
            lv_opa_t o = (lv_opa_t)(217 + (int)(E * 38.0f));   // 0.85..1.00
            if (o > 255) o = 255;
            draw_dot(layer, vol_x[i], vol_y[i], r, Theme::accent, o);
        } else {
            draw_dot(layer, vol_x[i], vol_y[i],
                     Theme::VOL_DOT_R_DIM, Theme::accent_dim, (lv_opa_t)160);
        }
    }
}

static void prog_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    int prog_pct = 0;
    if (State::app.dur_ms > 1) {
        uint32_t pos = State::app.pos_ms;
        if (pos > State::app.dur_ms) pos = State::app.dur_ms;
        prog_pct = (int)((uint64_t)pos * 100u / State::app.dur_ms);
    }
    int lit = (prog_pct * 60 + 50) / 100;
    for (int i = 0; i < 60; i++) {
        bool is_lit  = (i < lit);
        lv_color_t c = is_lit ? Theme::accent : Theme::Color::TEXT_FAINT;
        lv_opa_t   o = is_lit ? LV_OPA_COVER : (lv_opa_t)120;
        draw_dot(layer, prog_x[i], prog_y[i], 2, c, o);
    }
}

static void state_icon_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_obj_t  *obj    = (lv_obj_t *)lv_event_get_target(e);
    lv_area_t coords;
    lv_obj_get_coords(obj, &coords);

    const int x = coords.x1;
    const int y = coords.y1;

    State::PlayState st = State::app.state;
    if (st == State::PLAY_STOPPED || st == State::PLAY_STANDBY) {
        if (st == State::PLAY_STANDBY) return;
        lv_draw_rect_dsc_t rdsc;
        lv_draw_rect_dsc_init(&rdsc);
        rdsc.bg_color = Theme::accent_dim;
        rdsc.bg_opa   = LV_OPA_COVER;
        rdsc.radius   = 0;
        lv_area_t sq = { x + 6, y + 6, x + 17, y + 17 };
        lv_draw_rect(layer, &rdsc, &sq);
        return;
    }

    if (st == State::PLAY_PAUSED) {
        lv_draw_rect_dsc_t rdsc;
        lv_draw_rect_dsc_init(&rdsc);
        rdsc.bg_color = Theme::accent_dim;
        rdsc.bg_opa   = LV_OPA_COVER;
        rdsc.radius   = 0;
        lv_area_t bar1 = { x + 5,  y + 4, x + 9,  y + 19 };
        lv_area_t bar2 = { x + 14, y + 4, x + 18, y + 19 };
        lv_draw_rect(layer, &rdsc, &bar1);
        lv_draw_rect(layer, &rdsc, &bar2);
        return;
    }

    if (st == State::PLAY_PLAYING) {
        lv_draw_triangle_dsc_t tdsc;
        lv_draw_triangle_dsc_init(&tdsc);
        tdsc.color = Theme::accent_dim;
        tdsc.opa   = LV_OPA_COVER;
        tdsc.p[0].x = x + 6;   tdsc.p[0].y = y + 4;
        tdsc.p[1].x = x + 6;   tdsc.p[1].y = y + 19;
        tdsc.p[2].x = x + 19;  tdsc.p[2].y = y + 11;
        lv_draw_triangle(layer, &tdsc);
        return;
    }
}

// ─── Standby pulse ──────────────────────────────────────────────────────────

// ─── Touch handlers (unified press / move / release) ────────────────────────

static void on_pressed(lv_event_t *e) {
    rotary_active          = false;
    rotary_consumed        = false;
    rotary_accumulated_deg = 0.0f;

    if (in_standby || in_shutdown) return;

    lv_indev_t *indev = lv_indev_get_act();
    if (!indev) return;
    lv_point_t p;
    lv_indev_get_point(indev, &p);

    press_start_x = press_last_x = p.x;
    press_start_y = press_last_y = p.y;

    int d2 = sq_dist_to_center(p.x, p.y);
    if (d2 >= ROTARY_INNER_R_SQ) {
        rotary_active         = true;
        rotary_start_vol      = State::app.volume;
        rotary_last_angle_rad = atan2f((float)(p.y - Theme::CENTER),
                                       (float)(p.x - Theme::CENTER));
    }
}

static void on_pressing(lv_event_t *e) {
    if (in_standby || in_shutdown) return;

    lv_indev_t *indev = lv_indev_get_act();
    if (!indev) return;
    lv_point_t p;
    lv_indev_get_point(indev, &p);

    press_last_x = p.x;
    press_last_y = p.y;

    if (!rotary_active) return;

    int d2 = sq_dist_to_center(p.x, p.y);
    if (d2 < ROTARY_KILL_R_SQ) {
        rotary_active = false;
        return;
    }

    float cur_angle = atan2f((float)(p.y - Theme::CENTER),
                             (float)(p.x - Theme::CENTER));
    float delta = cur_angle - rotary_last_angle_rad;
    if (delta >  (float)M_PI) delta -= 2.0f * (float)M_PI;
    if (delta < -(float)M_PI) delta += 2.0f * (float)M_PI;
    rotary_last_angle_rad = cur_angle;

    if (fabsf(delta) > ROTARY_DELTA_CAP_RAD) return;

    rotary_accumulated_deg += delta * (180.0f / (float)M_PI);

    int ticks   = (int)(rotary_accumulated_deg / ROTARY_DEG_PER_TICK);
    int new_vol = rotary_start_vol + ticks;
    if (new_vol < 0)   new_vol = 0;
    if (new_vol > 100) new_vol = 100;

    if (new_vol != State::app.volume) {
        State::set_volume(new_vol);
        Proto::send_volume(new_vol);
        rotary_consumed = true;
    }
    if (fabsf(rotary_accumulated_deg) > ROTARY_MIN_DEG) {
        rotary_consumed = true;
    }
}

static void on_released(lv_event_t *e) {
    rotary_active = false;
    if (in_shutdown) return;

    // Tap-to-wake from standby. Any release wakes — the bridge calls
    // _exit_standby() on any CMD: so WAKE is a no-op past that point.
    if (in_standby) {
        Proto::send_command("WAKE");
        return;
    }

    if (rotary_consumed) return;

    int dx  = press_last_x - press_start_x;
    int dy  = press_last_y - press_start_y;
    int adx = abs(dx);
    int ady = abs(dy);

    if (adx > SWIPE_MIN_PX && adx * SWIPE_RATIO_D > ady * SWIPE_RATIO_N) {
        if (dx < 0) {
            Proto::send_command("NEXT");
            CenterStage::show_toast("SKIP >", 1200);
        } else {
            Proto::send_command("PREV");
            CenterStage::show_toast("< SKIP", 1200);
        }
        return;
    }

    // PLAYPAUSE: no toast — CenterStage already shows PAUSE persistently when
    // the state flips to paused, and the vol-wobble resuming says "playing".
    Proto::send_command("PLAYPAUSE");
}

// ─── Mode switching ─────────────────────────────────────────────────────────

static void show_player_mode() {
    if (!in_standby && !in_shutdown && lbl_title && !lv_obj_has_flag(lbl_title, LV_OBJ_FLAG_HIDDEN))
        return;
    bool was_shutdown = in_shutdown;
    bool was_standby  = in_standby;
    in_standby  = false;
    in_shutdown = false;
    auto S = [](lv_obj_t *o) { if (o) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN); };
    auto H = [](lv_obj_t *o) { if (o) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); };
    S(vol_layer); S(prog_layer);
    S(source_marker); S(lbl_source);
    S(lbl_title); S(lbl_artist);
    H(state_icon);                 // permanently hidden — CenterStage shows PAUSE
    // Switch back from the standby screen to our own (ScreenPlayer) scr.
    if (was_standby) {
        lv_screen_load(scr);
    }
    // Restore the title's normal player offset (shutdown mode centered it).
    if (was_shutdown && lbl_title) {
        lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, Theme::TITLE_Y_OFFSET);
    }
}

static void show_standby_mode() {
    if (in_standby || in_shutdown) return;
    in_standby = true;
    // Clear whatever CenterStage was last showing (e.g. PAUSE) so the new
    // screen owns the centre.
    CenterStage::invalidate();
    // Hand over to the dedicated standby screen — it renders clock + weather
    // + heartbeat from State::weather. Its own touch handler sends WAKE.
    ScreenStandby::show();
}

// Shutdown screen: shown both during the long-press warn ("Halten zum
// Ausschalten") and the confirmed shutdown ("Ausschalten..."). The actual
// text comes from the TI: field — the Pi sends a different string for each
// phase. We just hide all player chrome and show lbl_title centered on its
// own. Touch is also suppressed (see in_shutdown guards in on_pressed etc.).
static void show_shutdown_mode() {
    if (in_shutdown) return;
    bool was_standby = in_standby;
    in_shutdown = true;
    in_standby  = false;
    auto H = [](lv_obj_t *o) { if (o) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); };
    auto S = [](lv_obj_t *o) { if (o) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN); };
    H(vol_layer); H(prog_layer);
    H(source_marker); H(lbl_source);
    H(lbl_artist); H(state_icon);
    CenterStage::invalidate();
    // Coming from standby — bring our own scr back so the shutdown text shows.
    if (was_standby) {
        lv_screen_load(scr);
    }
    // Re-center the title for the shutdown message (player layout offsets it).
    if (lbl_title) {
        lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, 0);
        S(lbl_title);
    }
}

// ─── Construction ───────────────────────────────────────────────────────────

void create() {
    if (created) return;
    created = true;
    precompute_geometry();

    // ── Screen container ────────────────────────────────────────────────────
    scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, Theme::Color::BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr, 0, 0);
    lv_obj_set_style_border_width(scr, 0, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, on_pressed,  LV_EVENT_PRESSED,  NULL);
    lv_obj_add_event_cb(scr, on_pressing, LV_EVENT_PRESSING, NULL);
    lv_obj_add_event_cb(scr, on_released, LV_EVENT_RELEASED, NULL);

    // ── Full-screen custom-draw layers (back→front) ─────────────────────────
    auto make_layer = [&](void (*cb)(lv_event_t *)) -> lv_obj_t * {
        lv_obj_t *o = lv_obj_create(scr);
        lv_obj_remove_style_all(o);
        lv_obj_set_size(o, Theme::CENTER * 2, Theme::CENTER * 2);
        lv_obj_set_pos(o, 0, 0);
        lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, 0);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(o, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(o, cb, LV_EVENT_DRAW_MAIN, NULL);
        return o;
    };
    vol_layer    = make_layer(vol_draw_cb);
    prog_layer   = make_layer(prog_draw_cb);

    // ── Source marker ───────────────────────────────────────────────────────
    source_marker = lv_obj_create(scr);
    lv_obj_remove_style_all(source_marker);
    lv_obj_set_size(source_marker,
                    Theme::SOURCE_MARKER_SIZE, Theme::SOURCE_MARKER_SIZE);
    lv_obj_set_pos(source_marker,
                   Theme::CENTER - Theme::SOURCE_MARKER_SIZE / 2,
                   Theme::SOURCE_MARKER_Y);
    lv_obj_set_style_bg_color(source_marker, Theme::Color::SRC_NONE, 0);
    lv_obj_set_style_bg_opa(source_marker, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(source_marker, 0, 0);
    // Pivot to centre so the energy-pulse transform_scale grows/shrinks the
    // marker around its midpoint instead of dragging the top-left corner.
    lv_obj_set_style_transform_pivot_x(source_marker, Theme::SOURCE_MARKER_SIZE / 2, 0);
    lv_obj_set_style_transform_pivot_y(source_marker, Theme::SOURCE_MARKER_SIZE / 2, 0);
    lv_obj_add_flag(source_marker, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(source_marker, LV_OBJ_FLAG_CLICKABLE);

    // ── Source label ────────────────────────────────────────────────────────
    lbl_source = lv_label_create(scr);
    lv_label_set_text(lbl_source, "");
    lv_obj_set_style_text_color(lbl_source, Theme::accent_dim, 0);
    lv_obj_set_style_text_font(lbl_source, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_source, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
    lv_obj_add_flag(lbl_source, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_source, LV_OBJ_FLAG_CLICKABLE);

    // ── Title ───────────────────────────────────────────────────────────────
    lbl_title = lv_label_create(scr);
    lv_label_set_text(lbl_title, "");
    lv_obj_set_style_text_color(lbl_title, Theme::text_primary, 0);
    lv_obj_set_style_text_font(lbl_title, Theme::font_clock(), 0);
    lv_obj_set_style_text_letter_space(lbl_title, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(lbl_title, 4, 0);
    lv_obj_set_width(lbl_title, 280);
    lv_label_set_long_mode(lbl_title, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, Theme::TITLE_Y_OFFSET);
    lv_obj_add_flag(lbl_title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_title, LV_OBJ_FLAG_CLICKABLE);

    // ── Artist ──────────────────────────────────────────────────────────────
    lbl_artist = lv_label_create(scr);
    lv_label_set_text(lbl_artist, "");
    lv_obj_set_style_text_color(lbl_artist, Theme::text_secondary, 0);
    lv_obj_set_style_text_font(lbl_artist, Theme::font_display_lg(), 0);
    lv_obj_set_style_text_letter_space(lbl_artist, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_artist, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_artist, 260);
    lv_label_set_long_mode(lbl_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_artist, LV_ALIGN_CENTER, 0, Theme::ARTIST_Y_OFFSET);
    lv_obj_add_flag(lbl_artist, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_artist, LV_OBJ_FLAG_CLICKABLE);

    // ── State icon ──────────────────────────────────────────────────────────
    state_icon = lv_obj_create(scr);
    lv_obj_remove_style_all(state_icon);
    lv_obj_set_size(state_icon, 24, 24);
    lv_obj_set_style_bg_opa(state_icon, LV_OPA_TRANSP, 0);
    lv_obj_align(state_icon, LV_ALIGN_CENTER, 0, Theme::STATE_ICON_Y);
    lv_obj_add_flag(state_icon, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(state_icon, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(state_icon, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(state_icon, state_icon_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    // state_icon stays HIDDEN at runtime — CenterStage handles the pause glyph

    // (lbl_volume removed — CenterStage shows MUTE when volume == 0)

    // Standby UI now lives on ScreenStandby (clock + weather + heartbeat).
    // The player screen no longer hosts standby widgets.

    // ── CenterStage — status announcement slot (priority chain) ─────────────
    // Created LAST so its text renders on top of all the ring layers.
    CenterStage::create(scr);
}

void show() {
    if (!created) create();
    lv_screen_load(scr);
    State::app.active_screen = State::SCR_PLAYER;
    State::mark_dirty(State::Dirty::ALL);
}

lv_obj_t *root() {
    if (!created) create();
    return scr;
}

bool is_visible() {
    return created && State::app.active_screen == State::SCR_PLAYER;
}

// ─── Per-frame update ───────────────────────────────────────────────────────

void update() {
    if (!created || !is_visible()) return;

    if (State::app.state == State::PLAY_SHUTDOWN_WARN ||
        State::app.state == State::PLAY_SHUTDOWN)         show_shutdown_mode();
    else if (State::app.state == State::PLAY_STANDBY)     show_standby_mode();
    else                                                   show_player_mode();

    if (State::is_dirty(State::Dirty::ACCENT)) {
        lv_obj_set_style_text_color(lbl_title,  Theme::text_primary,   0);
        lv_obj_set_style_text_color(lbl_artist, Theme::text_secondary, 0);
        lv_obj_set_style_text_color(lbl_source, Theme::accent_dim,     0);
        lv_obj_invalidate(state_icon);
        lv_obj_invalidate(vol_layer);
        lv_obj_invalidate(prog_layer);
        State::clear_dirty(State::Dirty::ACCENT);
    }

    // SYS line still carries wifi_rssi — CenterStage's WIFI WEAK trigger reads
    // State::sys.wifi_rssi directly, so we just clear the dirty bit here.
    State::clear_dirty(State::Dirty::SYSTEM);

    // Standby branch: the dedicated ScreenStandby owns its own render path
    // (clock + weather + heartbeat), so all the player-side dirty handlers
    // below would just thrash hidden widgets. Bail early.
    if (in_standby) return;

    if (State::is_dirty(State::Dirty::TITLE)) {
        const char *t = State::app.title.length() ? State::app.title.c_str() : "—";
        lv_label_set_text(lbl_title, t);
        set_scroll_speed_pxs(lbl_title, 30);
        State::clear_dirty(State::Dirty::TITLE);
    }
    if (State::is_dirty(State::Dirty::ARTIST)) {
        lv_label_set_text(lbl_artist, State::app.artist.c_str());
        set_scroll_speed_pxs(lbl_artist, 25);
        State::clear_dirty(State::Dirty::ARTIST);
    }
    if (State::is_dirty(State::Dirty::STATE)) {
        lv_obj_invalidate(state_icon);
        State::clear_dirty(State::Dirty::STATE);
    }
    if (State::is_dirty(State::Dirty::SOURCE)) {
        lv_obj_set_style_bg_color(source_marker, source_color(State::app.source), 0);
        lv_label_set_text(lbl_source, source_label_text(State::app.source));
        lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
        State::clear_dirty(State::Dirty::SOURCE);
    }
    if (State::is_dirty(State::Dirty::VOLUME)) {
        // Volume text widget gone — vol ring + CenterStage's MUTE cover it now
        lv_obj_invalidate(vol_layer);
        State::clear_dirty(State::Dirty::VOLUME);
    }
    if (State::is_dirty(State::Dirty::PROGRESS)) {
        lv_obj_invalidate(prog_layer);
        State::clear_dirty(State::Dirty::PROGRESS);
    }

    // Energy-driven repaint at ~60 Hz while playing. energy_smoothed
    // low-passes app.energy with alpha=0.12 so transients feel musical,
    // not strobe-y. When state != PLAYING, the target collapses to 0 and
    // the loop keeps running (cheaply) until the smoothed value decays
    // away — without this the source-marker would freeze at its last opacity
    // instead of fading back to its idle level on pause.
    const bool keep_animating =
        (State::app.state == State::PLAY_PLAYING) || (energy_smoothed > 0.01f);
    if (keep_animating) {
        uint32_t now = millis();
        if (now - last_energy_render >= 16) {
            last_energy_render = now;
            const float target = (State::app.state == State::PLAY_PLAYING)
                               ? State::app.energy : 0.0f;
            energy_smoothed += (target - energy_smoothed) * 0.12f;
            lv_obj_invalidate(vol_layer);
            // Source marker pulse — opacity AND size driven by the same
            // sin the vol dots use, with the dynamic-remapped energy as
            // the amplitude. Without the remap the marker pulses at a
            // near-constant depth regardless of how loud the music is.
            // Frequency matches the vol-ring (0.005) so the whole player
            // breathes together.
            if (source_marker && !in_standby && !in_shutdown) {
                const float E_dyn = energy_dyn(energy_smoothed);
                const float wob   = sinf((float)now * 0.005f);  // -1..+1
                const float p     = E_dyn * wob;                // -E_dyn..+E_dyn (≤ 1.2)
                int o_i = 200 + (int)(p * 55.0f);
                if (o_i < 0)   o_i = 0;
                if (o_i > 255) o_i = 255;
                lv_obj_set_style_bg_opa(source_marker, (lv_opa_t)o_i, 0);
                int scale = 256 + (int)(p * 40.0f);             // ±15 % at peak (was ±12 %)
                lv_obj_set_style_transform_scale(source_marker, scale, 0);
            }
        }
    }
    // Drop the legacy spectrum / energy dirty bits — they no longer drive
    // anything. ENERGY is consumed by the keep_animating loop above when
    // playing; SPECTRUM is a NOP since spectrum_bands has been disabled in
    // every active profile.
    State::clear_dirty(State::Dirty::ENERGY | State::Dirty::SPECTRUM);

    // CenterStage evaluates its own triggers from State. When active, the
    // title/artist labels visually recede so the stage announcement reads
    // unambiguously.
    CenterStage::update();
    if (lbl_title && lbl_artist) {
        const bool stage_on = CenterStage::is_active();
        const lv_opa_t title_target  = stage_on ? (lv_opa_t)64 : LV_OPA_COVER;
        const lv_opa_t artist_target = stage_on ? (lv_opa_t)51 : LV_OPA_COVER;
        if (title_target != last_title_opa) {
            start_text_opa_anim(lbl_title, &anim_title_opa, title_opa_cb,
                                last_title_opa, title_target);
            last_title_opa = title_target;
        }
        if (artist_target != last_artist_opa) {
            start_text_opa_anim(lbl_artist, &anim_artist_opa, artist_opa_cb,
                                last_artist_opa, artist_target);
            last_artist_opa = artist_target;
        }
    }
}

}  // namespace ScreenPlayer