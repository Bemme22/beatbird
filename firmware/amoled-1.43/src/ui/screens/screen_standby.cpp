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
#include "proto.h"
#include "state.h"
#include "theme.h"

#include <lvgl.h>
#include <math.h>
#include <stdio.h>

namespace ScreenStandby {

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ─── LVGL objects ───────────────────────────────────────────────────────────

static lv_obj_t *scr           = nullptr;
static lv_obj_t *lbl_clock     = nullptr;
static lv_obj_t *icon_obj      = nullptr;   // custom-draw container @ (233, 240)
static lv_obj_t *lbl_temp      = nullptr;
static lv_obj_t *lbl_highlow   = nullptr;
static lv_obj_t *lbl_condition = nullptr;
static lv_obj_t *heartbeat     = nullptr;

static lv_anim_t anim_heartbeat;
static bool      created                = false;
static uint8_t   last_icon_rendered     = 255;   // force first paint
static String    last_clock_rendered    = "";
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

// ─── Cloud helper (5 puffs + flat base) ─────────────────────────────────────
// Centered on (cx+ox, cy+oy) — same shape as the mockup's drawCloud().

static void draw_cloud(lv_layer_t *layer,
                       int cx, int cy, int ox, int oy,
                       lv_color_t color, lv_opa_t opa)
{
    const int px = cx + ox;
    const int py = cy + oy;
    draw_dot(layer, px - 14, py - 1, 7, color, opa);
    draw_dot(layer, px -  5, py - 6, 8, color, opa);
    draw_dot(layer, px +  6, py - 5, 8, color, opa);
    draw_dot(layer, px + 14, py + 1, 7, color, opa);
    draw_rect(layer, px - 17, py + 1, px + 17, py + 9, color, opa, 4);
}

// ─── Icon variants ──────────────────────────────────────────────────────────
// All icons draw at the icon_obj coords. (cx, cy) is the obj origin.

static void icon_clear(lv_layer_t *l, int cx, int cy)
{
    draw_dot(l, cx, cy, 7, Theme::accent, LV_OPA_COVER);
    for (int i = 0; i < 8; i++) {
        float a = i * 45.0f * (float)M_PI / 180.0f;
        int x = cx + (int)roundf(cosf(a) * 22.0f);
        int y = cy + (int)roundf(sinf(a) * 22.0f);
        draw_dot(l, x, y, 3, Theme::accent, (lv_opa_t)217);   // 0.85 * 255
    }
}

static void icon_partly(lv_layer_t *l, int cx, int cy)
{
    // Sun upper-left
    const int sx = cx - 16, sy = cy - 10;
    draw_dot(l, sx, sy, 6, Theme::accent, LV_OPA_COVER);
    for (int i = 0; i < 8; i++) {
        float a = i * 45.0f * (float)M_PI / 180.0f;
        int x = sx + (int)roundf(cosf(a) * 16.0f);
        int y = sy + (int)roundf(sinf(a) * 16.0f);
        draw_dot(l, x, y, 2, Theme::accent, (lv_opa_t)178);   // 0.7
    }
    // Cloud lower-right
    draw_cloud(l, cx, cy, 6, 8, Theme::accent, (lv_opa_t)230);   // 0.9
}

static void icon_cloudy(lv_layer_t *l, int cx, int cy)
{
    draw_cloud(l, cx, cy,  0,   0, Theme::accent, LV_OPA_COVER);
    draw_cloud(l, cx, cy, -22, -10, Theme::accent, (lv_opa_t)115);   // 0.45
}

static void icon_rain(lv_layer_t *l, int cx, int cy)
{
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Three falling raindrops (static in v1; animation deferred)
    draw_dot(l, cx - 10, cy + 18, 3, Theme::accent, LV_OPA_COVER);
    draw_dot(l, cx,      cy + 18, 3, Theme::accent, LV_OPA_COVER);
    draw_dot(l, cx + 10, cy + 18, 3, Theme::accent, LV_OPA_COVER);
}

static void icon_snow(lv_layer_t *l, int cx, int cy)
{
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Three asterisk-style flakes: center + 4 cardinal smaller dots
    const int fx[] = { cx - 12, cx,      cx + 12 };
    const int fy[] = { cy + 18, cy + 22, cy + 18 };
    for (int i = 0; i < 3; i++) {
        draw_dot(l, fx[i],     fy[i],     2, Theme::accent, LV_OPA_COVER);
        draw_dot(l, fx[i] + 3, fy[i],     1, Theme::accent, (lv_opa_t)178);
        draw_dot(l, fx[i] - 3, fy[i],     1, Theme::accent, (lv_opa_t)178);
        draw_dot(l, fx[i],     fy[i] + 3, 1, Theme::accent, (lv_opa_t)178);
        draw_dot(l, fx[i],     fy[i] - 3, 1, Theme::accent, (lv_opa_t)178);
    }
}

static void icon_thunder(lv_layer_t *l, int cx, int cy)
{
    draw_cloud(l, cx, cy, 0, -8, Theme::accent, LV_OPA_COVER);
    // Lightning bolt as dot zigzag
    draw_dot(l, cx + 2, cy + 12, 3, Theme::accent, LV_OPA_COVER);
    draw_dot(l, cx - 3, cy + 16, 3, Theme::accent, LV_OPA_COVER);
    draw_dot(l, cx + 2, cy + 20, 3, Theme::accent, LV_OPA_COVER);
    draw_dot(l, cx - 3, cy + 24, 3, Theme::accent, LV_OPA_COVER);
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
    // Tap-to-wake: any release on the standby screen sends CMD:WAKE, which
    // the bridge interprets as a no-op past _exit_standby(). The screen
    // switch back to the player happens once the bridge pushes a ST: line
    // with the non-standby state (handled in screen_player.cpp::update()).
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, [](lv_event_t *) {
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
    lv_obj_set_style_text_color       (lbl_clock, Theme::accent,              0);
    lv_obj_set_style_text_font        (lbl_clock, Theme::font_clock(),        0);
    lv_obj_set_style_text_letter_space(lbl_clock, Theme::LETTER_SPACE_DISPLAY,0);
    lv_obj_align(lbl_clock, LV_ALIGN_TOP_MID, 0, 70);

    // ── Weather icon container (centered on 233, 180) ───────────────────────
    icon_obj = lv_obj_create(scr);
    lv_obj_remove_style_all(icon_obj);
    lv_obj_set_size(icon_obj, 100, 80);
    lv_obj_set_pos(icon_obj, Theme::CENTER - 50, 180 - 40);
    lv_obj_set_style_bg_opa(icon_obj, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(icon_obj, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(icon_obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(icon_obj, icon_draw_cb, LV_EVENT_DRAW_MAIN, NULL);

    // ── Temperature (44 px, was 33) ─────────────────────────────────────────
    lbl_temp = lv_label_create(scr);
    lv_label_set_text(lbl_temp, "");
    lv_obj_set_style_text_color       (lbl_temp, Theme::accent,                0);
    lv_obj_set_style_text_font        (lbl_temp, Theme::font_clock(),          0);
    lv_obj_set_style_text_letter_space(lbl_temp, Theme::LETTER_SPACE_DISPLAY,  0);
    lv_obj_align(lbl_temp, LV_ALIGN_TOP_MID, 0, 255);

    // ── High / Low (22 px, was 11) ──────────────────────────────────────────
    lbl_highlow = lv_label_create(scr);
    lv_label_set_text(lbl_highlow, "");
    lv_obj_set_style_text_color       (lbl_highlow, Theme::Color::TEXT_DIM,    0);
    lv_obj_set_style_text_font        (lbl_highlow, Theme::font_display_md(),  0);
    lv_obj_set_style_text_letter_space(lbl_highlow, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_align(lbl_highlow, LV_ALIGN_TOP_MID, 0, 320);

    // ── Condition label (22 px, was 11) ─────────────────────────────────────
    lbl_condition = lv_label_create(scr);
    lv_label_set_text(lbl_condition, "");
    lv_obj_set_style_text_color       (lbl_condition, Theme::Color::TEXT_FAINT, 0);
    lv_obj_set_style_text_font        (lbl_condition, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_condition, Theme::LETTER_SPACE_LABEL,0);
    lv_obj_align(lbl_condition, LV_ALIGN_TOP_MID, 0, 365);

    // ── Heartbeat dot ───────────────────────────────────────────────────────
    heartbeat = lv_obj_create(scr);
    lv_obj_remove_style_all(heartbeat);
    lv_obj_set_size(heartbeat, 10, 10);
    lv_obj_set_style_bg_color(heartbeat, Theme::accent, 0);
    lv_obj_set_style_bg_opa(heartbeat, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(heartbeat, LV_RADIUS_CIRCLE, 0);
    lv_obj_align(heartbeat, LV_ALIGN_TOP_MID, 0, 420);
    lv_obj_clear_flag(heartbeat, LV_OBJ_FLAG_CLICKABLE);
}

void show()
{
    if (!created) create();
    lv_screen_load(scr);
    State::app.active_screen = State::SCR_PLAYER;   // standby is a player sub-state
    start_heartbeat();
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
            // Graceful degrade — no WX: received yet
            lv_obj_add_flag(lbl_temp,      LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(lbl_highlow,   LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(lbl_condition, LV_OBJ_FLAG_HIDDEN);
            lv_obj_add_flag(icon_obj,      LV_OBJ_FLAG_HIDDEN);
        }
        lv_obj_invalidate(icon_obj);
        last_icon_rendered  = State::weather.icon;
        last_valid_rendered = State::weather.valid;
    }
}

}  // namespace ScreenStandby
