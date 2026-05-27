// =============================================================================
// ui/screens/screen_standby.cpp — Standby screen with weather
// =============================================================================
// Six weather icons rendered from dots + cloud helpers, matching the
// dot-vocabulary used elsewhere in the UI. Icons are drawn in a custom-
// draw layer centered on (233, 240). Geometry mirrors the icon library
// from the v3 design preview.
//
// All numeric layout in this file matches the values committed in
// PLAN.md and beatbird-ui-preview.html; if you tweak something here,
// also bump the mockup so they don't drift.
// =============================================================================

#include "screens/screen_standby.h"
#include "screens/screen_settings.h"
#include "screens/split_flap.h"
#include "proto.h"
#include "state.h"
#include "theme.h"
#include "touch_dirs.h"

#include <lvgl.h>
#include <math.h>
#include <stdio.h>

namespace ScreenStandby {

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// Icon-internal scale. All hardcoded offsets/radii in the icon helpers
// were tuned at 1.0 (matching the 100×70 obj from the mockup). With the
// rest of the standby labels bumped one font tier up, the icon felt
// small in proportion — scaling by 1.3× restores the visual weight.
// Bump ICON_OBJ_W/H if you raise this further, otherwise drawings clip.
static constexpr int ICON_SCALE_NUM = 13;
static constexpr int ICON_SCALE_DEN = 10;
static constexpr int ICON_OBJ_W     = 140;
static constexpr int ICON_OBJ_H     = 100;
static inline int    s(int n)       { return (n * ICON_SCALE_NUM) / ICON_SCALE_DEN; }
static inline float  sf(float n)    { return n * (float)ICON_SCALE_NUM / (float)ICON_SCALE_DEN; }

// ─── LVGL objects ───────────────────────────────────────────────────────────

static lv_obj_t *scr           = nullptr;
static lv_obj_t *scint_layer   = nullptr;   // background scintillation dots (ambient)
static lv_obj_t *lbl_clock     = nullptr;
static lv_obj_t *icon_obj      = nullptr;   // custom-draw container @ (233, 240)
static lv_obj_t *lbl_temp      = nullptr;
static lv_obj_t *lbl_highlow   = nullptr;
static lv_obj_t *lbl_condition = nullptr;
static lv_obj_t *lbl_flap      = nullptr;   // airport-board-style idle text
static lv_obj_t *heartbeat     = nullptr;
// BT-pairing QR widget + its caption. Created hidden; shown only while
// State::sys.bt_pairing is true and a URL has been received from the
// bridge. When shown, the clock + weather block are hidden so the QR
// has the full center of the screen.
static lv_obj_t *qr_code       = nullptr;
static lv_obj_t *qr_caption    = nullptr;
static String    qr_url_cached = "";
static bool      qr_url_applied= false;
static bool      qr_was_pairing= false;

// ─── Scintillation: ambient dot field ──────────────────────────────────────
// A handful of low-opacity accent dots scattered across the round display,
// each modulating its alpha with an independent sine. Pure cosmetic
// "this thing is alive even when nothing's happening" cue — peaks well
// below the text colour so it doesn't compete for attention with the
// clock or weather block.
struct Scintilla {
    int16_t  x, y;
    int8_t   r;       // dot radius in px
    uint16_t phase_ms;
    uint16_t period_ms;
};
static constexpr int SCINT_COUNT      = 11;
static constexpr int SCINT_PEAK_OPA   = 60;   // 0..255, well below text @ 255
static Scintilla scint[SCINT_COUNT];
static bool scint_seeded = false;

static lv_anim_t anim_heartbeat;
static bool      created                = false;
static uint8_t   last_icon_rendered     = 255;   // force first paint
static String    last_clock_rendered    = "";
// Tap vs swipe distinguisher for the standby screen's release handler.
// Capacitive touch is single-finger so file-scope is fine.
static int       press_sx               = 0;
static int       press_sy               = 0;

// Fixed pixel width of the flap label. Used both when we configure the
// label in create() and when set_flap_text decides whether the next
// message needs the marquee. Hardcoded — calling lv_obj_get_width() on
// a freshly created label returns 0 until LVGL's layout has run, and
// on the very first standby transition that race made every short
// message look like 'wider than the label → scroll → align LEFT'.
static constexpr int FLAP_LABEL_WIDTH    = 380;
// Cached flap text — set by the Pi-side STBY: line. Cached so a message
// arriving before create() runs gets applied on the next create() pass.
static String    pending_flap_text      = "ON STANDBY";
static bool      last_valid_rendered    = false;

// ─── Dot drawing helper ─────────────────────────────────────────────────────

static void draw_dot(lv_layer_t *layer,
                     int cx, int cy, int r,
                     lv_color_t color, lv_opa_t opa)
{
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

static void draw_rect(lv_layer_t *layer,
                      int x1, int y1, int x2, int y2,
                      lv_color_t color, lv_opa_t opa, int radius)
{
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = color;
    dsc.bg_opa   = opa;
    dsc.radius   = radius;
    lv_area_t a = { x1, y1, x2, y2 };
    lv_draw_rect(layer, &dsc, &a);
}

// ─── Scintillation seed + draw ─────────────────────────────────────────────

static void seed_scintillation() {
    // Hand-picked positions distributed around the round display, biased
    // toward the layout gaps (above the clock, beside the icon, between
    // the high/low and flap rows) so the dots feel intentional rather
    // than scattered noise. Each gets a period in 3-6 s and a phase
    // offset so the field doesn't pulse in unison.
    static const int16_t POS[SCINT_COUNT][2] = {
        { 110,  90}, { 360,  85}, { 220,  30},
        { 410, 200}, {  60, 210},
        { 380, 330}, {  85, 350},
        { 195, 430}, { 330, 445},
        { 145, 165}, { 320, 250},
    };
    for (int i = 0; i < SCINT_COUNT; i++) {
        scint[i].x         = POS[i][0];
        scint[i].y         = POS[i][1];
        scint[i].r         = (int8_t)(2 + (i & 1));        // 2 or 3 px
        scint[i].phase_ms  = (uint16_t)((i * 547u) % 6000);
        scint[i].period_ms = (uint16_t)(3000 + ((i * 911u) % 3000));
    }
    scint_seeded = true;
}

static void scint_draw_cb(lv_event_t *e) {
    if (!scint_seeded) seed_scintillation();
    lv_layer_t *layer = lv_event_get_layer(e);
    const uint32_t now = millis();
    for (int i = 0; i < SCINT_COUNT; i++) {
        // sin in 0..1, period independent per dot.
        float phase = (float)((now + scint[i].phase_ms) % scint[i].period_ms)
                      / (float)scint[i].period_ms;
        float s = 0.5f + 0.5f * sinf(phase * 6.2832f);
        lv_opa_t opa = (lv_opa_t)((float)SCINT_PEAK_OPA * s);
        if (opa < 4) continue;
        draw_dot(layer, scint[i].x, scint[i].y, scint[i].r,
                 Theme::accent, opa);
    }
}

// ─── Cloud helper (5 puffs + flat base) ─────────────────────────────────────
// Centered on (cx+ox, cy+oy) — same shape as the mockup's drawCloud().

static void draw_cloud(lv_layer_t *layer,
                       int cx, int cy, int ox, int oy,
                       lv_color_t color, lv_opa_t opa)
{
    const int px = cx + s(ox);
    const int py = cy + s(oy);
    draw_dot(layer, px + s(-14), py + s(-1), s(7), color, opa);
    draw_dot(layer, px + s(- 5), py + s(-6), s(8), color, opa);
    draw_dot(layer, px + s(  6), py + s(-5), s(8), color, opa);
    draw_dot(layer, px + s( 14), py + s( 1), s(7), color, opa);
    draw_rect(layer, px + s(-17), py + s(1), px + s(17), py + s(9), color, opa, s(4));
}

// ─── Icon variants ──────────────────────────────────────────────────────────
// All icons draw at the icon_obj coords. (cx, cy) is the obj origin.
// Every literal pixel value below is wrapped in s() so the global icon
// scale factor (ICON_SCALE_NUM/DEN at the top of the file) applies uniformly.
//
// Time-based animations use millis() and ScreenStandby::update() invalidates
// the icon at ~20 fps to drive them. All animations are subtle — this is
// ambient eye-catch, not a screensaver.

static void icon_clear(lv_layer_t *l, int cx, int cy)
{
    const float t = (float)millis() * 0.001f;        // seconds
    draw_dot(l, cx, cy, s(7), Theme::accent, LV_OPA_COVER);
    // Rays pulse opacity with phase-shift around the sun (wave running
    // around the disc). ±0.25 amplitude on opacity 0.85 base.
    for (int i = 0; i < 8; i++) {
        float a = i * 45.0f * (float)M_PI / 180.0f;
        float pulse = sinf(t * 2.0f + (float)i * 0.6f);    // -1..+1
        lv_opa_t opa = (lv_opa_t)(190 + (int)(40 * pulse));
        int x = cx + (int)roundf(cosf(a) * sf(22.0f));
        int y = cy + (int)roundf(sinf(a) * sf(22.0f));
        draw_dot(l, x, y, s(3), Theme::accent, opa);
    }
}

static void icon_partly(lv_layer_t *l, int cx, int cy)
{
    const float t = (float)millis() * 0.001f;
    // Sun upper-left — pulsing rays, same algorithm as icon_clear.
    const int sx = cx + s(-16), sy = cy + s(-10);
    draw_dot(l, sx, sy, s(6), Theme::accent, LV_OPA_COVER);
    for (int i = 0; i < 8; i++) {
        float a = i * 45.0f * (float)M_PI / 180.0f;
        float pulse = sinf(t * 2.0f + (float)i * 0.6f);
        lv_opa_t opa = (lv_opa_t)(150 + (int)(40 * pulse));
        int x = sx + (int)roundf(cosf(a) * sf(16.0f));
        int y = sy + (int)roundf(sinf(a) * sf(16.0f));
        draw_dot(l, x, y, s(2), Theme::accent, opa);
    }
    // Cloud lower-right with very gentle horizontal drift.
    int drift = (int)(sf(2.0f) * sinf(t * 0.4f));
    draw_cloud(l, cx + drift, cy, 6, 8, Theme::accent, (lv_opa_t)230);
}

static void icon_cloudy(lv_layer_t *l, int cx, int cy)
{
    const float t = (float)millis() * 0.001f;
    // Two clouds drift in opposite directions with different speeds,
    // gives the icon a quiet "weather is happening" feel without being
    // a distraction.
    int drift_a = (int)(sf(3.0f) * sinf(t * 0.35f));
    int drift_b = (int)(sf(4.0f) * sinf(t * 0.25f + 1.5f));
    draw_cloud(l, cx + drift_a, cy,  0,   0, Theme::accent, LV_OPA_COVER);
    draw_cloud(l, cx + drift_b, cy, -22, -10, Theme::accent, (lv_opa_t)115);
}

static void icon_rain(lv_layer_t *l, int cx, int cy)
{
    const float t = (float)millis() * 0.001f;
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Three falling drops at staggered phases. Each drop falls from y=12
    // to y=26 (s-scaled) over ~1.4 s, then teleports back to the start.
    // Phase offset 0.4 between drops gives the visual cascade.
    const float DROP_PERIOD = 1.4f;
    const int   x_off[] = { -10, 0, 10 };
    const float phases[] = { 0.0f, 0.4f, 0.8f };
    for (int i = 0; i < 3; i++) {
        float phase = fmodf(t / DROP_PERIOD + phases[i], 1.0f);   // 0..1
        // y goes from 12 (top, near cloud) to 26 (bottom of icon zone)
        int   y_off = 12 + (int)(14.0f * phase);
        // Fade out at the very end of the fall so drops "evaporate"
        // rather than blink off.
        lv_opa_t opa = (lv_opa_t)(phase > 0.85f
                                  ? (uint8_t)(255 * (1.0f - phase) / 0.15f)
                                  : 255);
        draw_dot(l, cx + s(x_off[i]), cy + s(y_off), s(3), Theme::accent, opa);
    }
}

static void icon_snow(lv_layer_t *l, int cx, int cy)
{
    const float t = (float)millis() * 0.001f;
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Three flakes rotate slowly around their own centres — the 4
    // satellite dots orbit the central dot at ~0.5 rev/sec. Plus a
    // very gentle vertical bob to suggest they're floating.
    const int fx_base[] = { -12, 0,  12 };
    const int fy_base[] = {  18, 22, 18 };
    for (int i = 0; i < 3; i++) {
        // Rotation phase, offset per-flake so they don't sync
        float rot = t * 2.5f + (float)i * 1.2f;
        // Vertical bob — different period per flake
        int bob = (int)(sf(1.5f) * sinf(t * 0.8f + (float)i * 1.7f));
        int cx_f = cx + s(fx_base[i]);
        int cy_f = cy + s(fy_base[i]) + bob;
        draw_dot(l, cx_f, cy_f, s(2), Theme::accent, LV_OPA_COVER);
        // Four satellites — rotate the cardinal cross
        for (int k = 0; k < 4; k++) {
            float a = rot + k * (float)M_PI * 0.5f;
            int x = cx_f + (int)roundf(cosf(a) * sf(3.0f));
            int y = cy_f + (int)roundf(sinf(a) * sf(3.0f));
            draw_dot(l, x, y, s(1), Theme::accent, (lv_opa_t)178);
        }
    }
}

static void icon_thunder(lv_layer_t *l, int cx, int cy)
{
    const uint32_t now = millis();
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Lightning flashes briefly — ~120 ms on, ~3 s off. Plus a faint
    // "glow" residual so the bolt remains visible most of the time as
    // a dim shape (looks better than empty space + sudden flash).
    constexpr uint32_t CYCLE_MS = 3200;
    constexpr uint32_t FLASH_MS = 120;
    uint32_t cycle = now % CYCLE_MS;
    lv_opa_t bolt_opa;
    if (cycle < FLASH_MS) {
        // Bright flash, fades from full to baseline over the flash window
        float p = (float)cycle / (float)FLASH_MS;
        bolt_opa = (lv_opa_t)(255 - (int)(155 * p));   // 255 → 100
    } else {
        bolt_opa = 100;   // residual visibility
    }
    draw_dot(l, cx + s( 2), cy + s(12), s(3), Theme::accent, bolt_opa);
    draw_dot(l, cx + s(-3), cy + s(16), s(3), Theme::accent, bolt_opa);
    draw_dot(l, cx + s( 2), cy + s(20), s(3), Theme::accent, bolt_opa);
    draw_dot(l, cx + s(-3), cy + s(24), s(3), Theme::accent, bolt_opa);
}

// Fog renders as cloudy for v1 — placeholder until a dedicated fog icon
// gets designed (probably horizontal dot rows).
static void icon_fog(lv_layer_t *l, int cx, int cy)
{
    icon_cloudy(l, cx, cy);
}

// ─── Dispatch ───────────────────────────────────────────────────────────────

static void icon_draw_cb(lv_event_t *e)
{
    if (!State::weather.valid) return;

    lv_layer_t *layer = lv_event_get_layer(e);
    lv_obj_t   *obj   = (lv_obj_t *)lv_event_get_target(e);
    lv_area_t coords;
    lv_obj_get_coords(obj, &coords);
    // Center of the obj
    const int cx = (coords.x1 + coords.x2) / 2;
    const int cy = (coords.y1 + coords.y2) / 2;

    switch (State::weather.icon) {
        case State::WX_CLEAR:   icon_clear  (layer, cx, cy); break;
        case State::WX_PARTLY:  icon_partly (layer, cx, cy); break;
        case State::WX_CLOUDY:  icon_cloudy (layer, cx, cy); break;
        case State::WX_FOG:     icon_fog    (layer, cx, cy); break;
        case State::WX_RAIN:    icon_rain   (layer, cx, cy); break;
        case State::WX_SNOW:    icon_snow   (layer, cx, cy); break;
        case State::WX_THUNDER: icon_thunder(layer, cx, cy); break;
        default: break;
    }
}

static const char *condition_label_text(State::WeatherIcon icon)
{
    switch (icon) {
        case State::WX_CLEAR:   return "CLEAR";
        case State::WX_PARTLY:  return "PARTLY CLOUDY";
        case State::WX_CLOUDY:  return "CLOUDY";
        case State::WX_FOG:     return "FOG";
        case State::WX_RAIN:    return "RAIN";
        case State::WX_SNOW:    return "SNOW";
        case State::WX_THUNDER: return "THUNDERSTORM";
        default:                return "";
    }
}

// ─── Heartbeat pulse ────────────────────────────────────────────────────────

static void heartbeat_pulse_cb(void *var, int32_t v)
{
    lv_obj_set_style_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void start_heartbeat()
{
    lv_anim_init(&anim_heartbeat);
    lv_anim_set_var(&anim_heartbeat, heartbeat);
    lv_anim_set_exec_cb(&anim_heartbeat, heartbeat_pulse_cb);
    lv_anim_set_values(&anim_heartbeat, 100, 255);
    lv_anim_set_time(&anim_heartbeat, 1400);
    lv_anim_set_playback_time(&anim_heartbeat, 1400);
    lv_anim_set_repeat_count(&anim_heartbeat, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&anim_heartbeat, lv_anim_path_ease_in_out);
    lv_anim_start(&anim_heartbeat);
}

static void stop_heartbeat()
{
    lv_anim_del(heartbeat, heartbeat_pulse_cb);
    lv_obj_set_style_opa(heartbeat, LV_OPA_COVER, 0);
}

// ─── Construction ───────────────────────────────────────────────────────────

void create()
{
    if (created) return;
    created = true;

    // ── Screen container ────────────────────────────────────────────────────
    scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, Theme::Color::BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr, 0, 0);
    lv_obj_set_style_border_width(scr, 0, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    // ── Scintillation layer ────────────────────────────────────────────────
    // First child = bottom of the z-stack, so all subsequent widgets
    // (clock, weather, flap) render on top. Full-screen, transparent,
    // not clickable — purely a draw canvas. Invalidated by update().
    scint_layer = lv_obj_create(scr);
    lv_obj_remove_style_all(scint_layer);
    lv_obj_set_size(scint_layer, 466, 466);
    lv_obj_set_pos (scint_layer, 0, 0);
    lv_obj_set_style_bg_opa(scint_layer, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(scint_layer, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(scint_layer, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag  (scint_layer, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(scint_layer, scint_draw_cb, LV_EVENT_DRAW_MAIN, NULL);

    // Touch model on standby:
    //   - Tap → CMD:WAKE (bridge no-ops past _exit_standby; firmware
    //     switches to player when bridge pushes the next non-standby ST:)
    //   - Swipe-down → quick-settings panel (pair bluetooth etc.)
    // Capacitive touch is single-finger so file-scope press-start state
    // is fine. The decision happens on RELEASED so a downward drag can
    // be distinguished from a static tap.
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, [](lv_event_t * /*e*/) {
        lv_indev_t *indev = lv_indev_active();
        if (!indev) return;
        lv_point_t p; lv_indev_get_point(indev, &p);
        press_sx = p.x; press_sy = p.y;
    }, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(scr, [](lv_event_t * /*e*/) {
        lv_indev_t *indev = lv_indev_active();
        if (!indev) { Proto::send_command("WAKE"); return; }
        lv_point_t p; lv_indev_get_point(indev, &p);
        int dx = p.x - press_sx, dy = p.y - press_sy;
        int adx = (dx < 0 ? -dx : dx), ady = (dy < 0 ? -dy : dy);
        // Swipe-down — looser threshold than horizontal swipes elsewhere.
        // A real "swipe down" with finger drag has wobble; ady > adx (no
        // 1.3:1 ratio) gives the gesture room. Min 30 px so a static tap
        // with millimetre-jitter still resolves to WAKE. Direction
        // multiplier handles the per-case panel-mount difference.
        if (ady > 30 && ady > adx && dy * TOUCH_DIR_DOWN_IS_POS_DY > 0) {
            ScreenSettings::show();
            return;
        }
        Proto::send_command("WAKE");
    }, LV_EVENT_RELEASED, NULL);

    // Layout y-anchors (TOP_MID with y_offset = top of label). Everything
    // moved up + one font tier larger from the mockup spec — Departure Mono
    // at small sizes was unreadable from across the room.
    //   clock     y= 70  (44 px)
    //   icon      y=140..220 (centered at y=180)
    //   temp      y=255  (44 px — was 33)
    //   highlow   y=320  (22 px — was 11)
    //   condition y=365  (22 px — was 11)
    //   heartbeat y=420  (10 px — was  8)

    // ── Clock (top, 44 px) ──────────────────────────────────────────────────
    lbl_clock = lv_label_create(scr);
    lv_label_set_text(lbl_clock, "--:--");
    lv_obj_set_style_text_color       (lbl_clock, Theme::text_primary,        0);
    lv_obj_set_style_text_font        (lbl_clock, Theme::font_clock(),        0);
    lv_obj_set_style_text_letter_space(lbl_clock, Theme::LETTER_SPACE_DISPLAY,0);
    lv_obj_align(lbl_clock, LV_ALIGN_TOP_MID, 0, 70);

    // ── Weather icon container (centered on 233, 180) ───────────────────────
    icon_obj = lv_obj_create(scr);
    lv_obj_remove_style_all(icon_obj);
    lv_obj_set_size(icon_obj, ICON_OBJ_W, ICON_OBJ_H);
    lv_obj_set_pos(icon_obj, Theme::CENTER - ICON_OBJ_W / 2, 180 - ICON_OBJ_H / 2);
    lv_obj_set_style_bg_opa(icon_obj, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(icon_obj, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(icon_obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(icon_obj, icon_draw_cb, LV_EVENT_DRAW_MAIN, NULL);

    // ── Temperature (44 px, was 33) ─────────────────────────────────────────
    lbl_temp = lv_label_create(scr);
    lv_label_set_text(lbl_temp, "");
    lv_obj_set_style_text_color       (lbl_temp, Theme::text_primary,          0);
    lv_obj_set_style_text_font        (lbl_temp, Theme::font_clock(),          0);
    lv_obj_set_style_text_letter_space(lbl_temp, Theme::LETTER_SPACE_DISPLAY,  0);
    lv_obj_align(lbl_temp, LV_ALIGN_TOP_MID, 0, 255);

    // ── High / Low (22 px, was 11) ──────────────────────────────────────────
    lbl_highlow = lv_label_create(scr);
    lv_label_set_text(lbl_highlow, "");
    lv_obj_set_style_text_color       (lbl_highlow, Theme::text_secondary,     0);
    lv_obj_set_style_text_font        (lbl_highlow, Theme::font_display_md(),  0);
    lv_obj_set_style_text_letter_space(lbl_highlow, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_align(lbl_highlow, LV_ALIGN_TOP_MID, 0, 320);

    // ── Condition label (22 px, was 11) ─────────────────────────────────────
    lbl_condition = lv_label_create(scr);
    lv_label_set_text(lbl_condition, "");
    lv_obj_set_style_text_color       (lbl_condition, Theme::text_secondary,    0);
    lv_obj_set_style_text_opa         (lbl_condition, (lv_opa_t)128,            0);  // 50 % for darker tier
    lv_obj_set_style_text_font        (lbl_condition, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_condition, Theme::LETTER_SPACE_LABEL,0);
    lv_obj_align(lbl_condition, LV_ALIGN_TOP_MID, 0, 355);   // bumped up 10 to make room for flap

    // ── Idle flap text (airport board, accent colour) ───────────────────────
    // Rotates every ~45s via Pi's STBY: serial line. Replaces the old static
    // heartbeat as the "I'm still alive" cue — the periodic flap animation
    // is more expressive than a pulsing dot, and the messages give the
    // standby screen personality.
    //
    // Fixed width + SCROLL_CIRCULAR so long RSS headlines marquee instead
    // of clipping at the round display's edge. Short local strings
    // (≤ ~17 chars at font_display_md) fit without triggering the scroll.
    // SplitFlap saves the long-mode at flap start and restores it on the
    // final tick, so the marquee picks up cleanly once the flap settles.
    lbl_flap = lv_label_create(scr);
    lv_label_set_text(lbl_flap, pending_flap_text.c_str());
    lv_obj_set_style_text_color       (lbl_flap, Theme::accent,                 0);
    lv_obj_set_style_text_font        (lbl_flap, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(lbl_flap, Theme::LETTER_SPACE_LABEL,     0);
    lv_obj_set_style_text_align       (lbl_flap, LV_TEXT_ALIGN_CENTER,          0);
    lv_obj_set_width                  (lbl_flap, FLAP_LABEL_WIDTH);
    lv_label_set_long_mode            (lbl_flap, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_flap, LV_ALIGN_TOP_MID, 0, 400);

    // ── Heartbeat dot (smaller, near bottom edge) ───────────────────────────
    heartbeat = lv_obj_create(scr);
    lv_obj_remove_style_all(heartbeat);
    lv_obj_set_size(heartbeat, 6, 6);                          // shrunk from 10 — flap text is now the headliner
    lv_obj_set_style_bg_color(heartbeat, Theme::accent, 0);
    lv_obj_set_style_bg_opa(heartbeat, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(heartbeat, LV_RADIUS_CIRCLE, 0);
    lv_obj_align(heartbeat, LV_ALIGN_TOP_MID, 0, 445);
    lv_obj_clear_flag(heartbeat, LV_OBJ_FLAG_CLICKABLE);

    // ── BT pairing QR + caption (hidden until SYS:bt=1 + URL set) ───────────
    // Size picked for scannability: 320 px on a 466-px @ 326-PPI panel is
    // ~25 mm wide. For a typical 30-char URL the QR widget will pick
    // v2-v3 (25-29 modules), i.e. ~0.9 mm per module — comfortable scan
    // at 15-20 cm. The QR's own light_color is white so it works on the
    // black AMOLED background; phones expect dark-on-light orientation.
    qr_code = lv_qrcode_create(scr);
    lv_qrcode_set_size(qr_code, 320);
    lv_qrcode_set_dark_color (qr_code, lv_color_black());
    lv_qrcode_set_light_color(qr_code, lv_color_white());
    lv_obj_align(qr_code, LV_ALIGN_TOP_MID, 0, 50);
    lv_obj_add_flag(qr_code, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(qr_code, LV_OBJ_FLAG_CLICKABLE);

    qr_caption = lv_label_create(scr);
    lv_label_set_text(qr_caption, "");
    lv_obj_set_style_text_color(qr_caption, Theme::text_secondary, 0);
    lv_obj_set_style_text_font (qr_caption, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(qr_caption, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_set_style_text_align(qr_caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(qr_caption, LV_ALIGN_TOP_MID, 0, 378);
    lv_obj_add_flag(qr_caption, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(qr_caption, LV_OBJ_FLAG_CLICKABLE);

    // Apply any URL the bridge pushed before create() ran.
    if (qr_url_cached.length() > 0 && !qr_url_applied) {
        lv_qrcode_update(qr_code, qr_url_cached.c_str(), qr_url_cached.length());
        qr_url_applied = true;
    }
}

// Build a short hostname:port caption from the URL for the secondary
// label. Strips http:// and any trailing slash so a 30-char URL collapses
// to ~20 chars that fit at font_display_md without scrolling.
static String short_caption_from_url(const String &url)
{
    String s = url;
    int p = s.indexOf("://");
    if (p >= 0) s = s.substring(p + 3);
    while (s.length() > 0 && s.charAt(s.length() - 1) == '/') {
        s = s.substring(0, s.length() - 1);
    }
    return s;
}

void set_qr_url(const char *url)
{
    if (!url || !url[0]) return;
    qr_url_cached  = String(url);
    qr_url_applied = false;
    // If the screen tree has been built, apply now. Otherwise create()
    // will pick it up on first build via the qr_url_cached check above.
    if (qr_code) {
        lv_qrcode_update(qr_code, qr_url_cached.c_str(), qr_url_cached.length());
        qr_url_applied = true;
    }
    // Same for the caption — short hostname:port form.
    if (qr_caption) {
        String cap = short_caption_from_url(qr_url_cached);
        lv_label_set_text(qr_caption, cap.c_str());
    }
}

void set_flap_text(const char *text)
{
    if (!text || !text[0]) return;
    pending_flap_text = String(text);
    if (lbl_flap) {
        // For text wider than the label's visible width, skip the flap
        // and just set+scroll. The flap is fixed-width with long_mode
        // forced to CLIP for the duration — only the leftmost characters
        // would animate, then the marquee would restart abruptly on the
        // final tick. Looks much worse than just rolling the headline in.
        // (Player screen uses a two-phase disintegrate/assemble for this;
        // standby rotates every 45 s so it's not worth the orchestration.)
        const lv_font_t *font = lv_obj_get_style_text_font(lbl_flap, LV_PART_MAIN);
        int32_t lsp = lv_obj_get_style_text_letter_space(lbl_flap, LV_PART_MAIN);
        lv_point_t sz;
        lv_text_get_size(&sz, text, font, lsp, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
        // Hardcoded width instead of lv_obj_get_width — first-call
        // layout race produced get_width=0 and forced every short
        // message to LEFT/scroll mode for the first standby transition.
        bool will_scroll = sz.x > FLAP_LABEL_WIDTH;
        // SCROLL_CIRCULAR anchors text at the left edge of the label;
        // CENTER alignment keeps short messages pretty on the round
        // display. Switching the alignment in lockstep with the scroll
        // decision avoids the hand-off snap from centered-while-CLIP'd
        // to left-anchored marquee.
        lv_obj_set_style_text_align(lbl_flap,
            will_scroll ? LV_TEXT_ALIGN_LEFT : LV_TEXT_ALIGN_CENTER, 0);
        if (will_scroll) {
            // Make sure long-mode is the marquee one (might have been
            // forced to CLIP if a prior flap is still in flight; calling
            // SplitFlap::set_text with a new target would have torn that
            // flap down, but we're skipping SplitFlap, so do it manually).
            lv_label_set_long_mode(lbl_flap, LV_LABEL_LONG_SCROLL_CIRCULAR);
            lv_label_set_text(lbl_flap, text);
        } else {
            // Pre-clear the label so SplitFlap sees old_len = 0 and
            // doesn't pad the target with trailing spaces. With
            // CENTER alignment those trailing spaces drift the visible
            // text off to the left for the entire flap duration —
            // particularly visible on first-time standby transitions
            // and after a PAIRING/scrolling text being replaced with
            // a short IDLE_MESSAGE. Cost: short flap chars 'appear
            // from nothing' instead of 'transform from old chars', but
            // for 45 s message rotations the difference is invisible.
            lv_label_set_text(lbl_flap, "");
            SplitFlap::set_text(lbl_flap, text);
        }
    }
    // If create() hasn't run yet, pending_flap_text is the seed used in
    // lv_label_set_text at creation — no animation that first time, but
    // the text is right.
}

void show()
{
    if (!created) create();
    // Fade-in transition instead of hard cut — both screens share the same
    // black background, so a 400 ms cross-fade reads as a soft segue
    // rather than a flash-change. auto_del=false keeps the player screen
    // alive (we'll lv_screen_load() it back when audio resumes).
    lv_screen_load_anim(scr, LV_SCR_LOAD_ANIM_FADE_IN, 400, 0, false);
    State::app.active_screen = State::SCR_PLAYER;   // standby is a player sub-state
    start_heartbeat();
    // Re-apply palette tokens — they may have changed via PAL: while the
    // standby screen was unloaded, and the Dirty::ACCENT bit may already
    // have been consumed by ScreenPlayer's update().
    lv_obj_set_style_text_color(lbl_clock,     Theme::text_primary,   0);
    lv_obj_set_style_text_color(lbl_temp,      Theme::text_primary,   0);
    lv_obj_set_style_text_color(lbl_highlow,   Theme::text_secondary, 0);
    lv_obj_set_style_text_color(lbl_condition, Theme::text_secondary, 0);
    lv_obj_set_style_bg_color  (heartbeat,     Theme::accent,         0);
    last_icon_rendered  = 255;
    last_clock_rendered = "";
    last_valid_rendered = !State::weather.valid;     // force first render
    State::mark_dirty(State::Dirty::ALL);
}

lv_obj_t *root()
{
    if (!created) create();
    return scr;
}

bool is_visible()
{
    return created && lv_screen_active() == scr;
}

// ─── Per-frame update ───────────────────────────────────────────────────────

void update()
{
    if (!created || !is_visible()) return;

    // BT pairing mode: swap clock+weather block for the QR. Only fires
    // on the transition edge so we don't churn flags every frame. The
    // QR caption is shown alongside; the flap text below it (driven
    // separately by STBY:) still shows "PAIRING <name>".
    const bool is_pairing = State::sys.bt_pairing && qr_url_applied;
    if (is_pairing != qr_was_pairing) {
        auto SHOW = [](lv_obj_t *o) { if (o) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN); };
        auto HIDE = [](lv_obj_t *o) { if (o) lv_obj_add_flag  (o, LV_OBJ_FLAG_HIDDEN); };
        if (is_pairing) {
            HIDE(lbl_clock);
            HIDE(icon_obj);
            HIDE(lbl_temp);
            HIDE(lbl_highlow);
            HIDE(lbl_condition);
            SHOW(qr_code);
            SHOW(qr_caption);
        } else {
            SHOW(lbl_clock);
            SHOW(icon_obj);
            SHOW(lbl_temp);
            SHOW(lbl_highlow);
            SHOW(lbl_condition);
            HIDE(qr_code);
            HIDE(qr_caption);
        }
        qr_was_pairing = is_pairing;
    }

    // Drive the icon animations at ~20 fps. The weather icons all use
    // millis() in their draw callbacks (raindrops falling, sun rays
    // pulsing, lightning flashing) — those only update when the obj
    // gets invalidated, so we tick it here.
    static uint32_t last_anim_tick = 0;
    uint32_t now = millis();
    if (now - last_anim_tick >= 50) {
        last_anim_tick = now;
        if (State::weather.valid) lv_obj_invalidate(icon_obj);
        // Scintillation runs at the same cadence — 20 fps is plenty
        // for a sine-modulated alpha pulsing over multi-second periods.
        if (scint_layer) lv_obj_invalidate(scint_layer);
    }

    // ACCENT/palette refresh — runtime palette tokens are pushed by the
    // bridge after connect (PAL:a=…|p=…|s=…). The colours assigned at
    // create() are snapshots; if they later change, re-apply them and
    // invalidate the icon (which reads Theme::accent live in its draw_cb).
    if (State::is_dirty(State::Dirty::ACCENT)) {
        lv_obj_set_style_text_color(lbl_clock,     Theme::text_primary,   0);
        lv_obj_set_style_text_color(lbl_temp,      Theme::text_primary,   0);
        lv_obj_set_style_text_color(lbl_highlow,   Theme::text_secondary, 0);
        lv_obj_set_style_text_color(lbl_condition, Theme::text_secondary, 0);
        lv_obj_set_style_bg_color  (heartbeat,     Theme::accent,         0);
        lv_obj_invalidate(icon_obj);
        State::clear_dirty(State::Dirty::ACCENT);
    }

    // Clock
    if (State::app.clockStr != last_clock_rendered) {
        lv_label_set_text(lbl_clock, State::app.clockStr.c_str());
        last_clock_rendered = State::app.clockStr;
    }

    // Weather block — only repaint when icon, validity, or any field changes
    bool weather_changed =
        (State::weather.icon != last_icon_rendered) ||
        (State::weather.valid != last_valid_rendered);

    static int last_temp = INT32_MIN, last_hi = INT32_MIN, last_lo = INT32_MIN;
    if (State::weather.temp_c != last_temp ||
        State::weather.high_c != last_hi   ||
        State::weather.low_c  != last_lo) {
        weather_changed = true;
        last_temp = State::weather.temp_c;
        last_hi   = State::weather.high_c;
        last_lo   = State::weather.low_c;
    }

    if (weather_changed) {
        if (State::weather.valid) {
            // Buf sized for the highlow line — "H -99°  ·  L -99°" is up to
            // 22 bytes once the °/· UTF-8 sequences are counted. buf[16]
            // truncated mid-string (lost the trailing "1°").
            char buf[32];
            snprintf(buf, sizeof(buf), "%d\xC2\xB0", State::weather.temp_c);
            lv_label_set_text(lbl_temp, buf);

            snprintf(buf, sizeof(buf), "H %d\xC2\xB0  \xC2\xB7  L %d\xC2\xB0",
                     State::weather.high_c, State::weather.low_c);
            lv_label_set_text(lbl_highlow, buf);

            lv_label_set_text(lbl_condition,
                              condition_label_text(State::weather.icon));

            lv_obj_clear_flag(lbl_temp,      LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(lbl_highlow,   LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(lbl_condition, LV_OBJ_FLAG_HIDDEN);
            lv_obj_clear_flag(icon_obj,      LV_OBJ_FLAG_HIDDEN);
        } else {
            // Weather data unavailable — provider down or first poll
            // pending. Surface the state instead of silently hiding the
            // whole block so the user knows it's a known condition, not
            // a render glitch. Temp slot stays empty (its 44 px font is
            // distracting if used for a dash), highlow is hidden, the
            // condition line carries the message in the same secondary
            // text style normally used for "PARTLY CLOUDY" etc.
            lv_obj_add_flag  (lbl_temp,      LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag  (lbl_highlow,   LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag  (icon_obj,      LV_OBJ_FLAG_HIDDEN);
            lv_label_set_text(lbl_condition, "WETTER NICHT VERFUEGBAR");
            lv_obj_clear_flag(lbl_condition, LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_invalidate(icon_obj);
        last_icon_rendered  = State::weather.icon;
        last_valid_rendered = State::weather.valid;
    }
}

}  // namespace ScreenStandby
