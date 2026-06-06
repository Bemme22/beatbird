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
#include "screens/screen_settings.h"
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

static lv_obj_t *scr            = nullptr;
static lv_obj_t *halftone_layer = nullptr;  // album-art bg as Pi-downsampled dot field, z-bottom

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

// Halftone cover — the Pi-downsampled n×n RGB grid (HT: protocol) drawn as a
// dot field. STATIC: invalidated only on Dirty::COVER (a track change), never
// per frame — n×n ≈ 1500 dots once per song is cheap; per-frame would judder.
// Values tuned in docs/mockups/player-halftone.html.
static constexpr float HT_OPACITY     = 0.69f;   // global background presence
static constexpr float HT_SATURATION  = 1.27f;   // push toward the cover's own colours
static constexpr float HT_CENTRE_FADE = 0.55f;   // dim near the centre so the text reads
static constexpr int   HT_FADE_R      = 150;     // px radius of that centre freistellung
// Outer fade: the halftone is a central disc that fades out between OUTER_R and
// EDGE_R, leaving the vol ring (r=218) on near-black and the progress ring
// (r=192) in the fade tail — so both read clearly instead of competing with the
// dot field. Beyond EDGE_R nothing is drawn.
static constexpr float HT_OUTER_R     = 178.0f;
static constexpr float HT_EDGE_R      = 206.0f;

static void halftone_draw_cb(lv_event_t *e) {
    const uint8_t *grid = Proto::halftone_data();
    int n = Proto::halftone_n();
    if (!grid || n < 4) return;
    lv_layer_t *layer = lv_event_get_layer(e);
    const float cell = (float)(Theme::CENTER * 2) / (float)n;   // ≈ 12 px @ n=39
    const int   Rsq  = (int)(HT_EDGE_R * HT_EDGE_R);
    for (int gy = 0; gy < n; gy++) {
        for (int gx = 0; gx < n; gx++) {
            const uint8_t *p = grid + ((size_t)gy * n + gx) * 3;
            int r8 = p[0], g8 = p[1], b8 = p[2];
            float b = (r8 * 0.30f + g8 * 0.59f + b8 * 0.11f) / 255.0f;
            if (b < 0.05f) continue;
            int cx = (int)((gx + 0.5f) * cell);
            int cy = (int)((gy + 0.5f) * cell);
            int dx = cx - Theme::CENTER, dy = cy - Theme::CENTER;
            int d2 = dx * dx + dy * dy;
            if (d2 > Rsq) continue;                            // mask to the round disc
            // saturate toward the pixel colour
            float gr = (r8 + g8 + b8) / 3.0f;
            int rr = (int)(gr + (r8 - gr) * HT_SATURATION);
            int gg = (int)(gr + (g8 - gr) * HT_SATURATION);
            int bb = (int)(gr + (b8 - gr) * HT_SATURATION);
            rr = rr < 0 ? 0 : (rr > 255 ? 255 : rr);
            gg = gg < 0 ? 0 : (gg > 255 ? 255 : gg);
            bb = bb < 0 ? 0 : (bb > 255 ? 255 : bb);
            // dot radius grows with brightness; capped to the cell
            float rad = 0.5f + b * (cell * 0.42f);
            float rmax = cell * 0.5f;
            if (rad > rmax) rad = rmax;
            float dist = sqrtf((float)d2);
            // centre text-freistellung: fade dots out toward the middle
            float cfade = 1.0f - HT_CENTRE_FADE * (dist < HT_FADE_R ? (1.0f - dist / HT_FADE_R) : 0.0f);
            // outer fade: clear the ring band at the edge
            float ofade = (dist <= HT_OUTER_R) ? 1.0f
                        : (dist >= HT_EDGE_R)  ? 0.0f
                        : 1.0f - (dist - HT_OUTER_R) / (HT_EDGE_R - HT_OUTER_R);
            float a = HT_OPACITY * (0.25f + 0.85f * b) * cfade * ofade;
            int o = (int)(a * 255.0f);
            if (o < 3) continue;
            if (o > 255) o = 255;
            draw_dot(layer, cx, cy, (int)(rad + 0.5f), lv_color_make(rr, gg, bb), (lv_opa_t)o);
        }
    }
}

static void vol_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    int vol = State::app.volume;
    int lit = (vol * 24 + 50) / 100;
    // Static volume indicator — redrawn only on Dirty::VOLUME / Dirty::ACCENT,
    // never per-frame. Animating this screen-sized layer every frame (the old
    // energy wobble + volume wave) is exactly what juddered the S3; the energy
    // pulse now lives in the small source-marker instead.
    // Sits on the near-black outer band (halftone fades before r=206), so it
    // reads clearly again — lit 210, unlit 130.
    for (int i = 1; i < 24; i++) {              // dot 0 is the ring gap
        if (i < lit)
            draw_dot(layer, vol_x[i], vol_y[i], Theme::VOL_DOT_R,     Theme::accent,     (lv_opa_t)210);
        else
            draw_dot(layer, vol_x[i], vol_y[i], Theme::VOL_DOT_R_DIM, Theme::accent_dim, (lv_opa_t)130);
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
    // In the halftone's fade tail (r=192) — lit 210, unlit 110 so the progress
    // stipple stays readable.
    for (int i = 0; i < 60; i++) {
        bool is_lit  = (i < lit);
        lv_color_t c = is_lit ? Theme::accent : Theme::Color::TEXT_FAINT;
        lv_opa_t   o = is_lit ? (lv_opa_t)210 : (lv_opa_t)110;
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

    int dx  = press_last_x - press_start_x;
    int dy  = press_last_y - press_start_y;
    int adx = abs(dx);
    int ady = abs(dy);

    // Tap-to-wake from standby. Any non-swipe release wakes — the bridge
    // calls _exit_standby() on any CMD: so WAKE is a no-op past that point.
    if (in_standby) {
        Proto::send_command("WAKE");
        return;
    }

    // Rotary-volume wins over every other release gesture. If the user
    // touched the outer ring and either dragged enough angle to consume
    // a tick or just touched-and-released without movement, that's a
    // volume interaction — don't double-interpret it as a swipe-down to
    // settings. Earlier order had swipe-down evaluated first, which
    // meant a downward drag starting on the outer ring both changed
    // volume AND opened settings.
    if (rotary_consumed) return;

    // Swipe-down → quick-settings panel. Reachable only when the press
    // started inside the rotary inner radius (rotary_active stayed
    // false), which is the unambiguous "I want to navigate, not change
    // volume" zone. Threshold is looser than the horizontal NEXT/PREV
    // check: ady > adx is enough (no 1.3:1 ratio) because a downward
    // finger drag on a round display naturally has some horizontal
    // wobble. Min 30 px so a static tap doesn't trip it.
    if (ady > 30 && ady > adx && dy * TOUCH_DIR_DOWN_IS_POS_DY > 0) {
        ScreenSettings::show();
        return;
    }

    if (adx > SWIPE_MIN_PX && adx * SWIPE_RATIO_D > ady * SWIPE_RATIO_N) {
        // Swipe physically right → NEXT, physically left → PREV.
        // TOUCH_DIR_RIGHT_IS_POS_DX is +1 on Zipp and -1 on Beat (panel
        // mount differs by 180° per case), so the same source code maps
        // the user's intent consistently across both speakers.
        if (dx * TOUCH_DIR_RIGHT_IS_POS_DX > 0) {
            Proto::send_command("NEXT");
            CenterStage::show_toast("SKIP >", 1200);
        } else {
            Proto::send_command("PREV");
            CenterStage::show_toast("< SKIP", 1200);
        }
        return;
    }

    // PLAYPAUSE — flip the local play_state immediately so CenterStage's
    // "PAUSE" overlay appears/disappears in the same frame as the tap.
    // The bridge round-trip (Pi → go-librespot → state push) is ~150-
    // 300 ms; without the optimistic flip the display feels laggy even
    // when the tap was registered. The bridge's next state push will
    // either confirm our guess (no-op) or correct it.
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
    S(halftone_layer); S(vol_layer); S(prog_layer);
    S(source_marker); S(lbl_source);
    S(lbl_title); S(lbl_artist);
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
    H(halftone_layer); H(vol_layer); H(prog_layer);
    H(source_marker); H(lbl_source);
    H(lbl_artist); H(state_icon);
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

    // ── Album-cover background = Pi-downsampled halftone (z-bottom) ──────────
    // The Pi pre-processes the cover and ships it as a small n×n RGB grid via
    // the HT: serial protocol (no JPEG decode on the ESP). halftone_draw_cb
    // draws it as a dot field. Created below as the FIRST custom layer so it
    // sits z-bottom, behind the rings / labels / CenterStage.

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
    halftone_layer = make_layer(halftone_draw_cb);   // z-bottom (cover)
    vol_layer      = make_layer(vol_draw_cb);
    prog_layer     = make_layer(prog_draw_cb);

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
    lv_obj_set_style_text_font(lbl_artist, Theme::font_display_lg(), 0);
    lv_obj_set_style_text_letter_space(lbl_artist, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_artist, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_artist, 260);
    lv_label_set_long_mode(lbl_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);   // see title comment
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
        lv_obj_set_style_text_color(lbl_source, Theme::accent_dim,     0);
        lv_obj_invalidate(state_icon);
        lv_obj_invalidate(vol_layer);
        lv_obj_invalidate(prog_layer);
        State::clear_dirty(State::Dirty::ACCENT);
    }

    // SYS line still carries wifi_rssi — CenterStage's WIFI WEAK trigger reads
    // State::sys.wifi_rssi directly, so we just clear the dirty bit here.
    State::clear_dirty(State::Dirty::SYSTEM);

    // ── Album cover background (halftone) ───────────────────────────────────
    // A new HT: grid arrived (track change). Just re-run the static halftone
    // draw once; halftone_draw_cb reads Proto::halftone_data() itself.
    if (State::is_dirty(State::Dirty::COVER)) {
        if (halftone_layer) lv_obj_invalidate(halftone_layer);
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
        // Only repaint when a progress DOT actually changes (≈ every dur/60 s),
        // not on every position poll — prog_layer is screen-sized, so each
        // invalidate re-runs the halftone draw underneath it. Gating this keeps
        // the static halftone from being redrawn several times a second.
        static int last_prog_lit = -1;
        int pct = 0;
        if (State::app.dur_ms > 1) {
            uint32_t pos = State::app.pos_ms;
            if (pos > State::app.dur_ms) pos = State::app.dur_ms;
            pct = (int)((uint64_t)pos * 100u / State::app.dur_ms);
        }
        int lit = (pct * 60 + 50) / 100;
        if (lit != last_prog_lit) {
            last_prog_lit = lit;
            lv_obj_invalidate(prog_layer);
        }
        State::clear_dirty(State::Dirty::PROGRESS);
    }

    // NO per-frame animation on the player. The marker used to "breathe" with
    // the music at 30 Hz, but it sits ABOVE the halftone layer — its small
    // per-frame invalidate forces LVGL to re-run halftone_draw_cb (clipped, but
    // still looping every grid cell) 30×/s → judder. The halftone is a static
    // background; the player only repaints on dirty events (cover/volume/
    // progress/source). Energy/spectrum bits are dropped, unused.
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