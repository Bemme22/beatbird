// =============================================================================
// ui/screens/screen_settings.cpp — Quick-settings panel implementation
// =============================================================================
#include "screens/screen_settings.h"
#include "proto.h"
#include "state.h"
#include "theme.h"
#include "touch_dirs.h"

#ifdef ARDUINO
  #include <Arduino.h>
#else
  #include "sim/arduino_shim.h"
#endif
#include <lvgl.h>

namespace ScreenSettings {

// ─── State ──────────────────────────────────────────────────────────────────

static lv_obj_t *scr           = nullptr;
static lv_obj_t *prev_scr      = nullptr;   // remembered so close() can return to it
static lv_obj_t *qr_code       = nullptr;
static lv_obj_t *qr_caption    = nullptr;
static lv_obj_t *lbl_hint      = nullptr;   // small "swipe up to close" line
static bool      created       = false;
static uint32_t  opened_at_ms  = 0;
// QR URL cache. ScreenStandby has its own copy on its own widget; the
// protocol layer dispatches set_qr_url to both. We don't share a global
// buffer because the LVGL widgets live in separate trees and each owns
// its own render.
static String    qr_url_cached = "";
static bool      qr_url_applied= false;
// Press-start tracker for the swipe-up-to-close gesture. Same pattern
// as screen_standby; single-finger capacitive touch so file-scope is fine.
static int       press_sx      = 0;
static int       press_sy      = 0;

// Inactivity timeout. Long enough to read the screen, short enough that
// a forgotten-open panel doesn't sit there for hours blocking the
// standby clock.
static constexpr uint32_t AUTO_CLOSE_MS = 12000;

// ─── Build ──────────────────────────────────────────────────────────────────

static String short_caption_from_url(const String &url) {
    String s = url;
    int p = s.indexOf("://");
    if (p >= 0) s = s.substring(p + 3);
    while (s.length() > 0 && s.charAt(s.length() - 1) == '/') {
        s = s.substring(0, s.length() - 1);
    }
    return s;
}

static void on_panel_pressed(lv_event_t * /*e*/) {
    lv_indev_t *indev = lv_indev_active();
    if (!indev) return;
    lv_point_t p; lv_indev_get_point(indev, &p);
    press_sx = p.x; press_sy = p.y;
}

static void on_panel_released(lv_event_t * /*e*/) {
    // Swipe-up to dismiss. The previous lv_indev_get_vect approach
    // returned the delta between the last two input samples (not
    // press-to-release), which made the gesture all but impossible
    // to trigger. Track press start in our own state, same way
    // ScreenStandby does.
    lv_indev_t *indev = lv_indev_active();
    if (!indev) return;
    lv_point_t p; lv_indev_get_point(indev, &p);
    int dx = p.x - press_sx, dy = p.y - press_sy;
    int adx = (dx < 0 ? -dx : dx), ady = (dy < 0 ? -dy : dy);
    // Swipe-up — physically up. Direction multiplier flips per panel
    // mount (Beat vs Zipp) so the gesture feels the same on both.
    if (ady > 30 && ady > adx && dy * TOUCH_DIR_DOWN_IS_POS_DY < 0) {
        close();
    }
}

static void build() {
    if (created) return;
    created = true;

    scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, Theme::Color::BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr, 0, 0);
    lv_obj_set_style_border_width(scr, 0, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, on_panel_pressed,  LV_EVENT_PRESSED,  NULL);
    lv_obj_add_event_cb(scr, on_panel_released, LV_EVENT_RELEASED, NULL);

    // ── Title ──────────────────────────────────────────────────────────────
    // Caption stays generic — the QR routes to the speaker's web UI
    // dashboard, which is the entry point for everything (pairing,
    // volume, paired-devices management). Not BT-specific anymore.
    lv_obj_t *title = lv_label_create(scr);
    lv_label_set_text(title, "SCAN ZUR STEUERUNG");
    lv_obj_set_style_text_color       (title, Theme::text_secondary,         0);
    lv_obj_set_style_text_font        (title, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(title, Theme::LETTER_SPACE_LABEL,     0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 25);
    lv_obj_add_flag(title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(title, LV_OBJ_FLAG_CLICKABLE);

    // ── QR code ────────────────────────────────────────────────────────────
    // 300 px is a touch smaller than the standby QR (320) because we
    // need room for the title + hint at top/bottom. quiet_zone must be
    // explicit — LVGL 9.x leaves it off by default and phones won't
    // scan without the ~4-module white margin (see
    // feedback_lvgl_qrcode_quiet_zone in memory).
    qr_code = lv_qrcode_create(scr);
    lv_qrcode_set_size(qr_code, 300);
    lv_qrcode_set_dark_color (qr_code, lv_color_black());
    lv_qrcode_set_light_color(qr_code, lv_color_white());
    lv_qrcode_set_quiet_zone(qr_code, true);
    lv_obj_align(qr_code, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(qr_code, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(qr_code, LV_OBJ_FLAG_CLICKABLE);

    qr_caption = lv_label_create(scr);
    lv_label_set_text(qr_caption, "");
    lv_obj_set_style_text_color(qr_caption, Theme::accent, 0);
    lv_obj_set_style_text_font (qr_caption, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(qr_caption, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_set_style_text_align(qr_caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(qr_caption, LV_ALIGN_BOTTOM_MID, 0, -90);
    lv_obj_add_flag(qr_caption, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(qr_caption, LV_OBJ_FLAG_CLICKABLE);

    // Apply any URL the bridge pushed before build() ran (the bridge
    // sends QR: once at start; if that arrived before show() built
    // this screen the cache picks it up here).
    if (qr_url_cached.length() > 0 && !qr_url_applied) {
        lv_qrcode_update(qr_code, qr_url_cached.c_str(), qr_url_cached.length());
        lv_label_set_text(qr_caption, short_caption_from_url(qr_url_cached).c_str());
        qr_url_applied = true;
    }

    // ── Hint ──────────────────────────────────────────────────────────────
    lbl_hint = lv_label_create(scr);
    lv_label_set_text(lbl_hint, "SWIPE UP TO CLOSE");
    lv_obj_set_style_text_color       (lbl_hint, Theme::text_secondary,      0);
    lv_obj_set_style_text_opa         (lbl_hint, (lv_opa_t)100,              0);
    lv_obj_set_style_text_font        (lbl_hint, Theme::font_display_md(),   0);
    lv_obj_set_style_text_letter_space(lbl_hint, Theme::LETTER_SPACE_LABEL,  0);
    lv_obj_align(lbl_hint, LV_ALIGN_BOTTOM_MID, 0, -55);
    lv_obj_add_flag(lbl_hint, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_hint, LV_OBJ_FLAG_CLICKABLE);
}

void set_qr_url(const char *url) {
    if (!url || !url[0]) return;
    qr_url_cached  = String(url);
    qr_url_applied = false;
    if (qr_code) {
        lv_qrcode_update(qr_code, qr_url_cached.c_str(), qr_url_cached.length());
        qr_url_applied = true;
    }
    if (qr_caption) {
        lv_label_set_text(qr_caption, short_caption_from_url(qr_url_cached).c_str());
    }
}

// ─── Public API ─────────────────────────────────────────────────────────────

void show() {
    if (!created) build();
    prev_scr = lv_screen_active();
    opened_at_ms = millis();
    lv_screen_load_anim(scr, LV_SCR_LOAD_ANIM_OVER_TOP, 250, 0, false);
}

bool is_visible() {
    return created && lv_screen_active() == scr;
}

void close() {
    if (!is_visible()) return;
    lv_obj_t *target = prev_scr ? prev_scr : scr;
    lv_screen_load_anim(target, LV_SCR_LOAD_ANIM_OVER_BOTTOM, 250, 0, false);
}

void update() {
    if (!is_visible()) return;
    if (millis() - opened_at_ms >= AUTO_CLOSE_MS) {
        close();
    }
}

}  // namespace ScreenSettings
