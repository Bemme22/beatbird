// =============================================================================
// ui/screens/screen_settings.cpp — Quick-settings panel implementation
// =============================================================================
#include "screens/screen_settings.h"
#include "proto.h"
#include "state.h"
#include "theme.h"

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
static lv_obj_t *btn_pair      = nullptr;
static lv_obj_t *lbl_pair      = nullptr;
static lv_obj_t *lbl_hint      = nullptr;   // small "swipe up to close" line
static bool      created       = false;
static uint32_t  opened_at_ms  = 0;
// Press-start tracker for the swipe-up-to-close gesture. Same pattern
// as screen_standby; single-finger capacitive touch so file-scope is fine.
static int       press_sx      = 0;
static int       press_sy      = 0;

// Inactivity timeout. Long enough to read the screen, short enough that
// a forgotten-open panel doesn't sit there for hours blocking the
// standby clock.
static constexpr uint32_t AUTO_CLOSE_MS = 12000;

// ─── Build ──────────────────────────────────────────────────────────────────

static void on_pair_clicked(lv_event_t * /*e*/) {
    Proto::send_command("BT_PAIR");
    // Optimistic feedback — bridge needs ~50-200 ms to flip BlueZ and
    // another up-to-5 s for SYS:bt=1 to arrive. Without an immediate
    // local change the user thinks the tap was lost.
    if (lbl_pair) lv_label_set_text(lbl_pair, "PAIRING…");
    // Close right after so the user can read the standby PAIRING overlay
    // or watch their phone's BT picker. The bridge's PAIRING badge will
    // light up within 5 s.
    close();
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
    if (ady > 30 && ady > adx && dy < 0) {
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
    lv_obj_t *title = lv_label_create(scr);
    lv_label_set_text(title, "SETTINGS");
    lv_obj_set_style_text_color       (title, Theme::text_secondary,         0);
    lv_obj_set_style_text_font        (title, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(title, Theme::LETTER_SPACE_LABEL,     0);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 60);
    lv_obj_add_flag(title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(title, LV_OBJ_FLAG_CLICKABLE);

    // ── Pair Bluetooth button ──────────────────────────────────────────────
    // Centered on the round display. Big enough that a relaxed thumb-tap
    // lands reliably (~220×80 region). Border + accent fill so it reads
    // as a touchable affordance, not just text.
    btn_pair = lv_obj_create(scr);
    lv_obj_remove_style_all(btn_pair);
    lv_obj_set_size(btn_pair, 280, 100);
    lv_obj_align(btn_pair, LV_ALIGN_CENTER, 0, -10);
    lv_obj_set_style_bg_color(btn_pair, Theme::accent_dim, 0);
    lv_obj_set_style_bg_opa  (btn_pair, LV_OPA_COVER,      0);
    lv_obj_set_style_radius  (btn_pair, 18,                0);
    lv_obj_set_style_border_color(btn_pair, Theme::accent, 0);
    lv_obj_set_style_border_width(btn_pair, 2,             0);
    lv_obj_add_flag(btn_pair, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(btn_pair, on_pair_clicked, LV_EVENT_CLICKED, NULL);

    lbl_pair = lv_label_create(btn_pair);
    lv_label_set_text(lbl_pair, "PAIR BLUETOOTH");
    lv_obj_set_style_text_color       (lbl_pair, Theme::text_primary,        0);
    lv_obj_set_style_text_font        (lbl_pair, Theme::font_display_md(),   0);
    lv_obj_set_style_text_letter_space(lbl_pair, Theme::LETTER_SPACE_LABEL,  0);
    lv_obj_center(lbl_pair);
    lv_obj_add_flag(lbl_pair, LV_OBJ_FLAG_GESTURE_BUBBLE);

    // ── Hint ──────────────────────────────────────────────────────────────
    lbl_hint = lv_label_create(scr);
    lv_label_set_text(lbl_hint, "SWIPE UP TO CLOSE");
    lv_obj_set_style_text_color       (lbl_hint, Theme::text_secondary,      0);
    lv_obj_set_style_text_opa         (lbl_hint, (lv_opa_t)100,              0);
    lv_obj_set_style_text_font        (lbl_hint, Theme::font_display_md(),   0);
    lv_obj_set_style_text_letter_space(lbl_hint, Theme::LETTER_SPACE_LABEL,  0);
    lv_obj_align(lbl_hint, LV_ALIGN_BOTTOM_MID, 0, -60);
    lv_obj_add_flag(lbl_hint, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_hint, LV_OBJ_FLAG_CLICKABLE);
}

// ─── Public API ─────────────────────────────────────────────────────────────

void show() {
    if (!created) build();
    // Reset button text in case we're re-opening after a previous
    // BT_PAIR sequence that left it in "PAIRING…" state.
    if (lbl_pair) lv_label_set_text(lbl_pair, "PAIR BLUETOOTH");
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
