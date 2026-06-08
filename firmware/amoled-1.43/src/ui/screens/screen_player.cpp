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
#include "screens/split_flap.h"
#include "touch_dirs.h"
#include "state.h"
#include "theme.h"
#include "proto.h"

#ifdef ARDUINO
  #include <Arduino.h>
#else
  #include "sim/arduino_shim.h"
#endif
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
static lv_obj_t *cover_img     = nullptr;   // album-art background, z-bottom
static lv_image_dsc_t cover_dsc = {};

// Two-phase transition state for title/artist. For long text that needs
// to scroll, a single-phase flap looks like a 1 s freeze of the rolling
// motion. Instead: phase 1 flaps current chars → spaces ("disintegrate"),
// phase 2 flaps spaces → new chars ("assemble"). Net effect: continuous
// visible motion across the transition with no static pause.
//
// Phase values: 0 = idle / single-phase done, 1 = disintegrating,
//               2 = assembling
static uint8_t title_phase   = 0;
static uint8_t artist_phase  = 0;
static String  title_pending;
static String  artist_pending;

// Custom-draw layers (back→front)
static lv_obj_t *vol_layer     = nullptr;   // 24-dot vol ring (lit dots wobble with energy)
static lv_obj_t *prog_layer    = nullptr;   // 60-dot progress stipple
static lv_obj_t *zone_overlay  = nullptr;   // touch-time outline at the rotary boundary

// Player widgets
static lv_obj_t *source_marker = nullptr;
static lv_obj_t *lbl_source    = nullptr;
static lv_obj_t *lbl_title     = nullptr;
static lv_obj_t *lbl_artist    = nullptr;
static lv_obj_t *lbl_time      = nullptr;   // MM:SS / MM:SS (Warm Funktional)
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
// One-shot "click" feedback for source switches — when the bridge flips
// SOURCE, we set this to (millis() + duration). The energy-pulse loop
// blends an additional scale boost in while now < this expiry.
static uint32_t source_pulse_until = 0;
static constexpr uint32_t SOURCE_PULSE_MS = 300;

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

static constexpr int   ROTARY_INNER_R       = 170;
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
static void set_scroll_speed_pxs(lv_obj_t *lbl, const char *txt, int px_per_sec) {
    if (!lbl || !txt || !txt[0] || px_per_sec <= 0) return;
    // Measure against the FINAL text, not lv_label_get_text(lbl) — when a
    // SplitFlap animation is running, the label currently holds a flap
    // frame, not the target text.
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

// ─── Energy pulse (lightweight) ─────────────────────────────────────────────
// The full-screen particle field that used to live here judders on the real
// ESP32-S3: a screen-sized `scint_layer` invalidated every frame forces LVGL to
// recomposite the whole 466² disc (cover JPEG + all ring layers) at 30–60 Hz.
// Removed. The energy pulse now lives ONLY in the source-marker breathing
// (small object → tiny invalidate, see update()), which is cheap and smooth.
// The cloud/particle prototype is preserved in git history on this branch;
// reviving it needs a bounded partial-invalidate, tuned on the SDL sim first.

// ─── Draw callbacks ─────────────────────────────────────────────────────────

// Warm Funktional: clean stroked ring (dim full circle + accent progress arc,
// clockwise from 12 o'clock, optional red position tip). Replaces the old
// dot-raster vol/progress rings.
static void draw_ring(lv_layer_t *layer, int radius, int width, float frac,
                      lv_color_t fg, lv_opa_t fg_opa,
                      lv_color_t bg, lv_opa_t bg_opa, bool tip, bool ccw) {
    lv_draw_arc_dsc_t d;
    lv_draw_arc_dsc_init(&d);
    d.center.x = Theme::CENTER;
    d.center.y = Theme::CENTER;
    d.radius   = radius;
    d.width    = width;
    d.rounded  = 1;
    // background — full dim ring
    d.color = bg; d.opa = bg_opa;
    d.start_angle = 0; d.end_angle = 360;
    lv_draw_arc(layer, &d);
    // foreground — fills from the top (270°). Progress runs clockwise, volume
    // counter-clockwise so the two concentric arcs are visually distinct.
    if (frac > 0.0025f) {
        int sweep = (int)(frac * 360.0f + 0.5f);
        if (sweep < 2)   sweep = 2;
        if (sweep > 360) sweep = 360;
        int s_ang, e_ang, tip_ang;
        if (!ccw) { s_ang = 270;         e_ang = 270 + sweep; tip_ang = 270 + sweep; }
        else      { s_ang = 270 - sweep; e_ang = 270;         tip_ang = 270 - sweep; }
        while (s_ang < 0) { s_ang += 360; e_ang += 360; }   // LVGL accepts >360
        d.color = fg; d.opa = fg_opa;
        d.start_angle = s_ang;
        d.end_angle   = e_ang;
        lv_draw_arc(layer, &d);
        if (tip) {
            float a  = (float)tip_ang * (float)M_PI / 180.0f;
            int   tx = Theme::CENTER + (int)(radius * cosf(a));
            int   ty = Theme::CENTER + (int)(radius * sinf(a));
            draw_dot(layer, tx, ty, width / 2 + 2, Theme::accent_alert, LV_OPA_COVER);
        }
    }
}

static void vol_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    float f = State::app.volume / 100.0f;
    // inner thin volume ring (no tip), counter-clockwise
    draw_ring(layer, 188, 4, f, Theme::accent, (lv_opa_t)220,
              Theme::accent_dim, (lv_opa_t)140, false, /*ccw=*/true);
}

static void prog_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    float f = 0.0f;
    if (State::app.dur_ms > 1) {
        uint32_t pos = State::app.pos_ms;
        if (pos > State::app.dur_ms) pos = State::app.dur_ms;
        f = (float)pos / (float)State::app.dur_ms;
    }
    // outer progress ring + red position tip, clockwise
    draw_ring(layer, 205, 5, f, Theme::accent, LV_OPA_COVER,
              Theme::Color::TEXT_FAINT, (lv_opa_t)150, true, /*ccw=*/false);
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

// ─── Zone overlay (touch-time hint at the rotary boundary) ─────────────────
// Fades in a thin circle outline at radius ROTARY_INNER_R while the
// finger is down so the user can see at a glance where the rotary
// zone (outside the ring) and the swipe/tap zone (inside) split.
// Cheap visual affordance — no logic change, just makes the existing
// split discoverable.

static constexpr lv_opa_t ZONE_OVERLAY_PEAK_OPA = 90;
static constexpr uint32_t ZONE_OVERLAY_FADE_IN_MS  = 150;
static constexpr uint32_t ZONE_OVERLAY_FADE_OUT_MS = 250;

static void zone_overlay_opa_cb(void *var, int32_t v) {
    lv_obj_set_style_border_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void zone_overlay_fade(int32_t to_opa, uint32_t duration_ms) {
    if (!zone_overlay) return;
    lv_anim_del(zone_overlay, zone_overlay_opa_cb);
    int32_t from_opa = (int32_t)lv_obj_get_style_border_opa(
        zone_overlay, LV_PART_MAIN);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, zone_overlay);
    lv_anim_set_exec_cb(&a, zone_overlay_opa_cb);
    lv_anim_set_values(&a, from_opa, to_opa);
    lv_anim_set_time(&a, duration_ms);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

// ─── Touch handlers (unified press / move / release) ────────────────────────

static void on_pressed(lv_event_t *e) {
    rotary_active          = false;
    rotary_consumed        = false;
    rotary_accumulated_deg = 0.0f;
    // Visual zone hint — fade in the boundary circle. Suppressed in
    // standby / shutdown so a tap-to-wake doesn't flash a circle the
    // user has no use for yet.
    if (!in_standby && !in_shutdown) {
        zone_overlay_fade(ZONE_OVERLAY_PEAK_OPA, ZONE_OVERLAY_FADE_IN_MS);
    }

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
        // Multiply the x argument by TOUCH_DIR_RIGHT_IS_POS_DX so the
        // rotary uses the same axis convention as skip — Zipp's panel
        // reports X mirrored, which would invert atan2's rotation
        // direction and make clockwise read as volume-down.
        rotary_last_angle_rad = atan2f(
            (float)(p.y - Theme::CENTER),
            (float)(p.x - Theme::CENTER) * TOUCH_DIR_RIGHT_IS_POS_DX);
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

    float cur_angle = atan2f(
        (float)(p.y - Theme::CENTER),
        (float)(p.x - Theme::CENTER) * TOUCH_DIR_RIGHT_IS_POS_DX);
    float delta = cur_angle - rotary_last_angle_rad;
    if (delta >  (float)M_PI) delta -= 2.0f * (float)M_PI;
    if (delta < -(float)M_PI) delta += 2.0f * (float)M_PI;
    rotary_last_angle_rad = cur_angle;

    if (fabsf(delta) > ROTARY_DELTA_CAP_RAD) return;

    rotary_accumulated_deg += delta * (180.0f / (float)M_PI);

    // Add ticks: with the touch-Y axis fix in place, CW visual rotation
    // produces positive angular delta in atan2's coord system. start +
    // positive = louder, which is the natural 'turn the knob clockwise
    // to make it louder' direction. (Earlier subtraction patch was based
    // on a misread of the prior state and ended up inverting Beat after
    // it had been working.)
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
    // Fade the zone hint back out, regardless of which branch handles
    // the release. Even on shutdown / standby releases the fade is
    // a no-op if the overlay wasn't shown (animation from 0 to 0).
    zone_overlay_fade(0, ZONE_OVERLAY_FADE_OUT_MS);
    if (in_shutdown) return;

    // Tap-to-wake from standby — any release wakes (bridge no-ops past exit).
    if (in_standby) {
        Proto::send_command("WAKE");
        return;
    }

    // "Glance + simple" interaction model (2026-06-08): swipe gestures removed.
    // A release is now exactly ONE of two things — a rotary-volume turn, or a
    // tap (play/pause). Dropping swipe-skip / swipe-settings removed the
    // tap-vs-swipe ambiguity that was eating clean taps; skip + settings live
    // on the phone / web UI instead. Rotary still wins if it consumed angle.
    if (rotary_consumed) return;

    // Echo lockout: a single physical tap can fire TWO on_released events
    // ~1 s apart on the Zipp's flaky touch IC (verified in the bridge log —
    // PLAYPAUSE-twice ~1 s apart). After we send a PLAYPAUSE, drop another one
    // for a short window so the duplicate never leaves the ESP — otherwise it
    // toggles playback straight back ("zeigt Pause, spielt weiter"). This is an
    // input debounce for a hardware echo at the SOURCE; the play-state *logic*
    // is confirmation-driven in the bridge. ~1.2 s is well below any real
    // re-tap intent and above the observed echo gap.
    static uint32_t play_lockout_until = 0;
    uint32_t now = millis();
    if (now < play_lockout_until) return;        // echo within lockout — drop it
    play_lockout_until = now + 1200;

    // Optimistic flip so CenterStage's PAUSE overlay reacts in the same frame
    // as the tap (the bridge round-trip is ~150-300 ms). The bridge's next
    // state push confirms or corrects this.
    if (State::app.state == State::PLAY_PLAYING) {
        State::set_play_state(State::PLAY_PAUSED);
    } else if (State::app.state == State::PLAY_PAUSED) {
        State::set_play_state(State::PLAY_PLAYING);
    }
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
    S(lbl_source);
    S(lbl_title); S(lbl_artist); S(lbl_time);
    H(source_marker);              // music-reactive marker parked (hidden)
    H(state_icon);                 // permanently hidden — CenterStage shows PAUSE
    // Switch back from the standby screen to our own (ScreenPlayer) scr.
    // Fade-in symmetric to ScreenStandby::show() so the standby→player
    // direction is just as smooth as the inverse.
    if (was_standby) {
        lv_screen_load_anim(scr, LV_SCR_LOAD_ANIM_FADE_IN, 400, 0, false);
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
    H(lbl_artist); H(lbl_time); H(state_icon);
    CenterStage::invalidate();
    // Coming from standby — bring our own scr back so the shutdown text shows.
    // Hard-cut here on purpose: shutdown is urgent and an animated fade
    // delays the user feedback.
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

    // ── Album-cover background (z-bottom) ───────────────────────────────────
    // Pi pre-processes the cover (blur + darken + vignette) and pushes the
    // JPEG bytes via the IMG: serial protocol. We hold a pointer into the
    // RX buffer + length; LVGL's tjpgd decoder takes care of the rest.
    // No source set yet — first IMG:end will populate it via Dirty::COVER.
    cover_img = lv_image_create(scr);
    lv_obj_set_size(cover_img, Theme::CENTER * 2, Theme::CENTER * 2);
    lv_obj_set_pos(cover_img, 0, 0);
    lv_obj_clear_flag(cover_img, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(cover_img, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(cover_img, LV_OBJ_FLAG_GESTURE_BUBBLE);
    // Hidden until a cover actually lands via Dirty::COVER. Even with no
    // source set, an empty full-screen lv_image makes LVGL trace draw
    // areas for it every frame — visible as background stutter on the
    // ESP32 when the feature is profile-disabled but the widget exists.
    lv_obj_add_flag(cover_img, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_to_index(cover_img, 0);

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
    // Warm Funktional: the music-reactive marker is parked (see roadmap). Hide
    // it so the player reads clean; the breathing loop in update() no-ops on it.
    lv_obj_add_flag(source_marker, LV_OBJ_FLAG_HIDDEN);

    // ── Source label ────────────────────────────────────────────────────────
    lbl_source = lv_label_create(scr);
    lv_label_set_text(lbl_source, "");
    lv_obj_set_style_text_color(lbl_source, Theme::text_secondary, 0);
    lv_obj_set_style_text_font(lbl_source, Theme::font_sm(), 0);
    lv_obj_set_style_text_letter_space(lbl_source, 3, 0);   // tracked
    lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
    lv_obj_add_flag(lbl_source, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_source, LV_OBJ_FLAG_CLICKABLE);

    // ── Title ───────────────────────────────────────────────────────────────
    lbl_title = lv_label_create(scr);
    lv_label_set_text(lbl_title, "");
    lv_obj_set_style_text_color(lbl_title, Theme::text_primary, 0);
    lv_obj_set_style_text_font(lbl_title, Theme::font_title(), 0);
    lv_obj_set_style_text_letter_space(lbl_title, 0, 0);
    lv_obj_set_style_text_align(lbl_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(lbl_title, 4, 0);
    lv_obj_set_width(lbl_title, 300);
    // SCROLL_CIRCULAR — left-to-right one-way scroll with the doubled-text
    // wrap. Tried SCROLL (back-and-forth) but the right-to-left return
    // sweep felt jarring on a single line of music title; users expect
    // the marquee to keep moving in one direction like a station board.
    lv_label_set_long_mode(lbl_title, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, Theme::TITLE_Y_OFFSET);
    lv_obj_add_flag(lbl_title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_title, LV_OBJ_FLAG_CLICKABLE);

    // ── Artist ──────────────────────────────────────────────────────────────
    lbl_artist = lv_label_create(scr);
    lv_label_set_text(lbl_artist, "");
    lv_obj_set_style_text_color(lbl_artist, Theme::text_secondary, 0);
    lv_obj_set_style_text_font(lbl_artist, Theme::font_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_artist, 0, 0);
    lv_obj_set_style_text_align(lbl_artist, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_artist, 280);
    lv_label_set_long_mode(lbl_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);   // see title comment
    lv_obj_align(lbl_artist, LV_ALIGN_CENTER, 0, Theme::ARTIST_Y_OFFSET);
    lv_obj_add_flag(lbl_artist, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_artist, LV_OBJ_FLAG_CLICKABLE);

    // ── Elapsed / duration (MM:SS / MM:SS) ──────────────────────────────────
    lbl_time = lv_label_create(scr);
    lv_label_set_text(lbl_time, "");
    lv_obj_set_style_text_color(lbl_time, Theme::text_secondary, 0);
    lv_obj_set_style_text_font(lbl_time, Theme::font_sm(), 0);
    lv_obj_set_style_text_letter_space(lbl_time, 1, 0);
    lv_obj_set_style_text_align(lbl_time, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(lbl_time, LV_ALIGN_CENTER, 0, 112);
    lv_obj_add_flag(lbl_time, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_time, LV_OBJ_FLAG_CLICKABLE);

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

    // ── Zone overlay — touch-time boundary hint ─────────────────────────────
    // Thin circle outline at radius ROTARY_INNER_R. Hidden by default
    // (border_opa = 0); on_pressed / on_released animate the opacity so
    // the user gets a momentary 'here's the volume-vs-swipe split' cue.
    // CLICKABLE cleared so it never absorbs touches.
    zone_overlay = lv_obj_create(scr);
    lv_obj_remove_style_all(zone_overlay);
    lv_obj_set_size(zone_overlay, 2 * ROTARY_INNER_R, 2 * ROTARY_INNER_R);
    lv_obj_center(zone_overlay);
    lv_obj_set_style_bg_opa       (zone_overlay, LV_OPA_TRANSP,        0);
    lv_obj_set_style_radius       (zone_overlay, LV_RADIUS_CIRCLE,     0);
    lv_obj_set_style_border_width (zone_overlay, 2,                    0);
    lv_obj_set_style_border_color (zone_overlay, Theme::accent_dim,    0);
    lv_obj_set_style_border_opa   (zone_overlay, LV_OPA_TRANSP,        0);
    lv_obj_clear_flag(zone_overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(zone_overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag  (zone_overlay, LV_OBJ_FLAG_GESTURE_BUBBLE);

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
        lv_obj_set_style_text_color(lbl_source, Theme::text_secondary, 0);
        lv_obj_set_style_text_color(lbl_time,   Theme::text_secondary, 0);
        lv_obj_invalidate(state_icon);
        lv_obj_invalidate(vol_layer);
        lv_obj_invalidate(prog_layer);
        State::clear_dirty(State::Dirty::ACCENT);
    }

    // SYS line still carries wifi_rssi — CenterStage's WIFI WEAK trigger reads
    // State::sys.wifi_rssi directly, so we just clear the dirty bit here.
    State::clear_dirty(State::Dirty::SYSTEM);

    // ── Album cover background swap ─────────────────────────────────────────
    // tjpgd's decoder_info for VARIABLE source copies w/h straight from the
    // dsc instead of parsing the JPEG SOF marker — so we MUST pre-fill
    // dimensions. The Pi resizes every cover to 466×466, so hard-coding
    // is fine; push width/height via IMG:start if that ever changes.
    if (State::is_dirty(State::Dirty::COVER) && cover_img) {
        const uint8_t *data = Proto::cover_data();
        size_t sz = Proto::cover_size();
        if (data && sz > 4) {
            cover_dsc.header.magic  = LV_IMAGE_HEADER_MAGIC;
            cover_dsc.header.cf     = LV_COLOR_FORMAT_RAW;
            cover_dsc.header.w      = 466;
            cover_dsc.header.h      = 466;
            cover_dsc.header.stride = 466 * 3;
            cover_dsc.data_size     = (uint32_t)sz;
            cover_dsc.data          = data;
            lv_image_set_src(cover_img, NULL);     // drop cache entry
            lv_image_set_src(cover_img, &cover_dsc);
            // First real cover — unhide the widget. Stays visible after,
            // every subsequent set_src just swaps the image.
            lv_obj_clear_flag(cover_img, LV_OBJ_FLAG_HIDDEN);
            lv_obj_invalidate(cover_img);
        }
        State::clear_dirty(State::Dirty::COVER);
    }

    // Standby branch: the dedicated ScreenStandby owns its own render path
    // (clock + weather + heartbeat), so all the player-side dirty handlers
    // below would just thrash hidden widgets. Bail early.
    if (in_standby) return;

    // ── Helper: does this text overflow the label width (i.e. would scroll)?
    auto needs_scroll = [](lv_obj_t *lbl, const char *txt) -> bool {
        if (!txt || !*txt) return false;
        lv_coord_t w = lv_obj_get_width(lbl);
        const lv_font_t *font = lv_obj_get_style_text_font(lbl, LV_PART_MAIN);
        int32_t ls = lv_obj_get_style_text_letter_space(lbl, LV_PART_MAIN);
        lv_point_t sz = {0, 0};
        lv_text_get_size(&sz, txt, font, ls, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        return sz.x > w;
    };

    if (State::is_dirty(State::Dirty::TITLE)) {
        // Player moved off the flip-char (that now lives only on standby).
        // The title/artist are plain solid labels for legibility.
        if (State::app.title.length()) {
            const char *t = State::app.title.c_str();
            set_scroll_speed_pxs(lbl_title, t, 30);
            // Align LEFT when the text will marquee (SCROLL_CIRCULAR anchors
            // at the left edge), else CENTER so short titles sit pretty.
            lv_obj_set_style_text_align(lbl_title,
                needs_scroll(lbl_title, t) ? LV_TEXT_ALIGN_LEFT : LV_TEXT_ALIGN_CENTER, 0);
            lv_label_set_text(lbl_title, t);
        } else {
            // No title → quiet placeholder "···" (three U+00B7 mid-dots).
            lv_label_set_text(lbl_title, "\xc2\xb7\xc2\xb7\xc2\xb7");
        }
        title_phase = 0; title_pending = "";
        State::clear_dirty(State::Dirty::TITLE);
    }
    if (State::is_dirty(State::Dirty::ARTIST)) {
        const char *a = State::app.artist.c_str();
        set_scroll_speed_pxs(lbl_artist, a, 25);
        lv_obj_set_style_text_align(lbl_artist,
            needs_scroll(lbl_artist, a) ? LV_TEXT_ALIGN_LEFT : LV_TEXT_ALIGN_CENTER, 0);
        lv_label_set_text(lbl_artist, a);
        artist_phase = 0; artist_pending = "";
        State::clear_dirty(State::Dirty::ARTIST);
    }

    // ── Drive the two-phase transitions ──────────────────────────────────
    // Once the disintegrate flap finishes (label now shows " "), kick the
    // assemble flap pointing at the saved new text. After the second
    // flap finishes there's nothing more to do — the saved long_mode
    // (SCROLL_CIRCULAR) gets restored inside SplitFlap on the final tick.
    if (title_phase == 1 && !SplitFlap::is_running(lbl_title)) {
        SplitFlap::set_text(lbl_title, title_pending.c_str());
        title_phase = 2;
    } else if (title_phase == 2 && !SplitFlap::is_running(lbl_title)) {
        title_phase = 0;
        title_pending = "";
    }
    if (artist_phase == 1 && !SplitFlap::is_running(lbl_artist)) {
        SplitFlap::set_text(lbl_artist, artist_pending.c_str());
        artist_phase = 2;
    } else if (artist_phase == 2 && !SplitFlap::is_running(lbl_artist)) {
        artist_phase = 0;
        artist_pending = "";
    }
    if (State::is_dirty(State::Dirty::STATE)) {
        lv_obj_invalidate(state_icon);
        State::clear_dirty(State::Dirty::STATE);
    }
    if (State::is_dirty(State::Dirty::SOURCE)) {
        lv_obj_set_style_bg_color(source_marker, source_color(State::app.source), 0);
        lv_label_set_text(lbl_source, source_label_text(State::app.source));
        lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
        // Trigger the one-shot click pulse — energy loop reads source_pulse_until
        // and blends an extra scale boost on top of the continuous energy
        // modulation until it expires.
        source_pulse_until = millis() + SOURCE_PULSE_MS;
        State::clear_dirty(State::Dirty::SOURCE);
    }
    if (State::is_dirty(State::Dirty::VOLUME)) {
        // Volume text widget gone — vol ring + CenterStage's MUTE cover it now.
        // One static redraw of the ring; no per-frame wave (that juddered).
        lv_obj_invalidate(vol_layer);
        State::clear_dirty(State::Dirty::VOLUME);
    }
    if (State::is_dirty(State::Dirty::PROGRESS)) {
        lv_obj_invalidate(prog_layer);
        // Elapsed / duration as MM:SS / MM:SS.
        if (lbl_time) {
            uint32_t ps = State::app.pos_ms / 1000;
            uint32_t ds = State::app.dur_ms / 1000;
            char tb[28];
            snprintf(tb, sizeof(tb), "%u:%02u / %u:%02u",
                     ps / 60, ps % 60, ds / 60, ds % 60);
            lv_label_set_text(lbl_time, tb);
        }
        State::clear_dirty(State::Dirty::PROGRESS);
    }

    // Source-marker "breathing" — the ONLY per-frame animation now. The marker
    // is a small object, so set_style_* invalidates just its (transformed) box —
    // cheap, unlike invalidating a screen-sized ring layer every frame (which is
    // what juddered the S3). ~30 Hz is plenty for a soft pulse.
    //
    // Asymmetric envelope on energy_smoothed: fast attack (0.45) on a louder
    // peak than currently rendered, slow release (0.08) on the way down —
    // peak-meter response (capture_peak from the LV: field) so transients punch.
    // When state != PLAYING the target collapses to 0 and the loop keeps running
    // cheaply until it decays, so the marker fades back to idle, not freezes.
    const bool keep_animating =
        (State::app.state == State::PLAY_PLAYING) ||
        (energy_smoothed > 0.01f) ||
        (millis() < source_pulse_until);     // and through a source-switch pulse
    if (keep_animating) {
        uint32_t now = millis();
        if (now - last_energy_render >= 33) {
            last_energy_render = now;
            const float target = (State::app.state == State::PLAY_PLAYING)
                               ? State::app.energy : 0.0f;
            const float alpha  = (target > energy_smoothed) ? 0.45f : 0.08f;
            energy_smoothed += (target - energy_smoothed) * alpha;
            if (source_marker && !in_standby && !in_shutdown) {
                const float E_dyn = energy_dyn(energy_smoothed);
                const float wob   = sinf((float)now * 0.005f);  // -1..+1
                const float p     = E_dyn * wob;                // -E_dyn..+E_dyn (≤ 1.2)
                int o_i = 200 + (int)(p * 55.0f);
                if (o_i < 0)   o_i = 0;
                if (o_i > 255) o_i = 255;
                lv_obj_set_style_bg_opa(source_marker, (lv_opa_t)o_i, 0);
                int scale = 256 + (int)(p * 40.0f);             // ±15 % at peak
                // One-shot source-switch pulse blended on top — ease-out ramp
                // from +50 % size back to baseline over 300 ms ("click" feedback
                // when MA / Spotify hands off the speaker).
                if (now < source_pulse_until) {
                    float remaining = (float)(source_pulse_until - now)
                                    / (float)SOURCE_PULSE_MS;     // 1.0 → 0.0
                    scale += (int)(remaining * 128.0f);           // +50 % at start
                }
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