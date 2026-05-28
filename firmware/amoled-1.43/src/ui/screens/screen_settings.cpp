// =============================================================================
// ui/screens/screen_settings.cpp — Swipeable quick-settings carousel
// =============================================================================
//
// Swipe-down from the player / standby screen opens this panel; horizontal
// swipes flip between pages (LVGL tileview snap-to-grid); vertical swipe-up
// or the 12 s inactivity timer closes it.
//
// Page 1: QR code → speaker's web UI dashboard. Best for WLAN users —
//         scan, get the full controls page.
// Page 2: PAIR BLUETOOTH button — for guests who aren't on the WLAN, or
//         anyone who prefers the classic flow. Sends CMD:BT_PAIR; bridge
//         flips BlueZ into 60 s discoverable mode.
//
// Future pages slot in by adding more lv_tileview_add_tile() calls + a
// dot to the page-indicator row at the bottom — no gesture-handling
// changes needed. Candidates documented in STATUS.md::Roadmap.
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

static lv_obj_t *scr            = nullptr;
static lv_obj_t *prev_scr       = nullptr;   // remembered so close() can return to it
static lv_obj_t *tileview       = nullptr;
static lv_obj_t *tile_qr        = nullptr;
static lv_obj_t *tile_pair      = nullptr;
static lv_obj_t *qr_code        = nullptr;
static lv_obj_t *qr_caption     = nullptr;
static lv_obj_t *qr_title       = nullptr;
static lv_obj_t *pair_btn       = nullptr;
static lv_obj_t *pair_lbl       = nullptr;
static lv_obj_t *pair_title     = nullptr;
static lv_obj_t *dot_qr         = nullptr;   // page-indicator dots
static lv_obj_t *dot_pair       = nullptr;
static lv_obj_t *lbl_hint       = nullptr;   // "swipe up to close"
static bool      created        = false;
static uint32_t  opened_at_ms   = 0;

// QR URL cache. ScreenStandby has its own copy on its own widget; the
// protocol layer dispatches set_qr_url to both. We don't share a global
// buffer because the LVGL widgets live in separate trees and each owns
// its own render.
static String    qr_url_cached  = "";
static bool      qr_url_applied = false;

// Swipe-up to close is now driven by LV_EVENT_GESTURE on scr, so no
// manual press-start tracking needed here anymore (LVGL does the
// classification before firing the gesture event).

// Inactivity timeout. Long enough to read the screen, short enough that
// a forgotten-open panel doesn't sit there for hours blocking the
// standby clock. Resets whenever the user swipes between tiles, so a
// reader on page 2 doesn't get auto-closed while exploring.
static constexpr uint32_t AUTO_CLOSE_MS = 12000;

// ─── Helpers ────────────────────────────────────────────────────────────────

static String short_caption_from_url(const String &url) {
    String s = url;
    int p = s.indexOf("://");
    if (p >= 0) s = s.substring(p + 3);
    while (s.length() > 0 && s.charAt(s.length() - 1) == '/') {
        s = s.substring(0, s.length() - 1);
    }
    return s;
}

static void refresh_dots(uint32_t active_col) {
    // The currently-visible tile gets the accent fill; the other(s)
    // stay at a muted accent_dim so the row reads as "you're on dot N
    // of M". 8 px is large enough to see, small enough not to compete
    // with the page content.
    if (dot_qr)   lv_obj_set_style_bg_color(dot_qr,
        active_col == 0 ? Theme::accent : Theme::accent_dim, 0);
    if (dot_pair) lv_obj_set_style_bg_color(dot_pair,
        active_col == 1 ? Theme::accent : Theme::accent_dim, 0);
}

// ─── Touch handling ─────────────────────────────────────────────────────────

static void on_panel_gesture(lv_event_t * /*e*/) {
    // LVGL's LV_EVENT_GESTURE is the only event that survives the
    // tileview's scroll handling — PRESSED/RELEASED get partially
    // swallowed when the tileview decides to take the touch for its
    // own snap logic. Gesture events fire AFTER the scroll classifier
    // has already disambiguated, and the direction comes pre-classified
    // by LVGL using the user's actual finger trajectory.
    //
    // We act only on LV_DIR_TOP (physical up) here. LV_DIR_LEFT/RIGHT
    // are the tileview's domain and we don't want to interfere.
    // LV_DIR_BOTTOM is ignored because there's no swipe-down meaning
    // inside an already-open panel.
    lv_dir_t dir = lv_indev_get_gesture_dir(lv_indev_active());
    if (dir == LV_DIR_TOP) {
        close();
    }
}

static void on_tile_changed(lv_event_t * /*e*/) {
    // Page indicator + inactivity reset. lv_tileview emits VALUE_CHANGED
    // after a swipe lands on a new tile; we read the active tile to
    // figure out which dot to highlight.
    if (!tileview) return;
    lv_obj_t *active = lv_tileview_get_tile_active(tileview);
    uint32_t col = 0;
    if (active == tile_pair) col = 1;
    refresh_dots(col);
    opened_at_ms = millis();   // reset auto-close timer on user interaction
}

static void on_pair_clicked(lv_event_t * /*e*/) {
    // Send the existing BT_PAIR command — bridge handler still around
    // (handle_display_command in bridge.py opens a 60 s discoverable
    // window). Optimistic UI: swap the label to "PAIRING…" + close the
    // panel so the user can watch their phone's BT picker. The bridge's
    // SYS:bt=1 push lights the PAIRING badge on the player screen within
    // ~5 s, giving the "is it working?" answer without staying on this
    // panel.
    Proto::send_command("BT_PAIR");
    if (pair_lbl) lv_label_set_text(pair_lbl, "PAIRING…");
    close();
}

// ─── Build ──────────────────────────────────────────────────────────────────

static void build_tile_qr(lv_obj_t *parent) {
    qr_title = lv_label_create(parent);
    lv_label_set_text(qr_title, "SCAN ZUR STEUERUNG");
    lv_obj_set_style_text_color       (qr_title, Theme::text_secondary,         0);
    lv_obj_set_style_text_font        (qr_title, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(qr_title, Theme::LETTER_SPACE_LABEL,     0);
    lv_obj_align(qr_title, LV_ALIGN_TOP_MID, 0, 25);
    lv_obj_add_flag(qr_title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(qr_title, LV_OBJ_FLAG_CLICKABLE);

    qr_code = lv_qrcode_create(parent);
    lv_qrcode_set_size(qr_code, 280);
    lv_qrcode_set_dark_color (qr_code, lv_color_black());
    lv_qrcode_set_light_color(qr_code, lv_color_white());
    lv_qrcode_set_quiet_zone(qr_code, true);
    lv_obj_align(qr_code, LV_ALIGN_CENTER, 0, -10);
    lv_obj_add_flag(qr_code, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(qr_code, LV_OBJ_FLAG_CLICKABLE);

    qr_caption = lv_label_create(parent);
    lv_label_set_text(qr_caption, "");
    lv_obj_set_style_text_color(qr_caption, Theme::accent, 0);
    lv_obj_set_style_text_font (qr_caption, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(qr_caption, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_set_style_text_align(qr_caption, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(qr_caption, LV_ALIGN_BOTTOM_MID, 0, -85);
    lv_obj_add_flag(qr_caption, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(qr_caption, LV_OBJ_FLAG_CLICKABLE);

    if (qr_url_cached.length() > 0 && !qr_url_applied) {
        lv_qrcode_update(qr_code, qr_url_cached.c_str(), qr_url_cached.length());
        lv_label_set_text(qr_caption, short_caption_from_url(qr_url_cached).c_str());
        qr_url_applied = true;
    }
}

static void build_tile_pair(lv_obj_t *parent) {
    pair_title = lv_label_create(parent);
    lv_label_set_text(pair_title, "BLUETOOTH PAIREN");
    lv_obj_set_style_text_color       (pair_title, Theme::text_secondary,       0);
    lv_obj_set_style_text_font        (pair_title, Theme::font_display_md(),    0);
    lv_obj_set_style_text_letter_space(pair_title, Theme::LETTER_SPACE_LABEL,   0);
    lv_obj_align(pair_title, LV_ALIGN_TOP_MID, 0, 25);
    lv_obj_add_flag(pair_title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(pair_title, LV_OBJ_FLAG_CLICKABLE);

    // Big circular button — accent fill, accent border, label centred.
    // Sized to dominate the round display so guests know exactly where
    // to tap without prior briefing.
    pair_btn = lv_obj_create(parent);
    lv_obj_remove_style_all(pair_btn);
    lv_obj_set_size(pair_btn, 260, 260);
    lv_obj_align(pair_btn, LV_ALIGN_CENTER, 0, -10);
    lv_obj_set_style_bg_color(pair_btn, Theme::accent_dim, 0);
    lv_obj_set_style_bg_opa  (pair_btn, LV_OPA_COVER,      0);
    lv_obj_set_style_radius  (pair_btn, LV_RADIUS_CIRCLE,  0);
    lv_obj_set_style_border_color(pair_btn, Theme::accent, 0);
    lv_obj_set_style_border_width(pair_btn, 3,             0);
    lv_obj_add_flag(pair_btn, LV_OBJ_FLAG_CLICKABLE);
    // Bubble gestures so a vertical swipe-up that starts on top of the
    // button still triggers close. Tap-without-movement keeps firing
    // CLICKED on the button itself.
    lv_obj_add_flag(pair_btn, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(pair_btn, on_pair_clicked, LV_EVENT_CLICKED, NULL);

    pair_lbl = lv_label_create(pair_btn);
    lv_label_set_text(pair_lbl, "TAP");
    lv_obj_set_style_text_color       (pair_lbl, Theme::text_primary,           0);
    lv_obj_set_style_text_font        (pair_lbl, Theme::font_display_lg(),      0);
    lv_obj_set_style_text_letter_space(pair_lbl, Theme::LETTER_SPACE_DISPLAY,   0);
    lv_obj_center(pair_lbl);
    lv_obj_add_flag(pair_lbl, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(pair_lbl, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *sub = lv_label_create(parent);
    lv_label_set_text(sub, "60 SEKUNDEN SICHTBAR");
    lv_obj_set_style_text_color       (sub, Theme::accent,                      0);
    lv_obj_set_style_text_font        (sub, Theme::font_display_md(),           0);
    lv_obj_set_style_text_letter_space(sub, Theme::LETTER_SPACE_LABEL,          0);
    lv_obj_set_style_text_align       (sub, LV_TEXT_ALIGN_CENTER,               0);
    lv_obj_align(sub, LV_ALIGN_BOTTOM_MID, 0, -85);
    lv_obj_add_flag(sub, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(sub, LV_OBJ_FLAG_CLICKABLE);
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
    lv_obj_add_event_cb(scr, on_panel_gesture, LV_EVENT_GESTURE, NULL);

    // ── Tileview ───────────────────────────────────────────────────────────
    // Fills the whole screen; tiles snap horizontally on swipe. The
    // hint + page-dot row are siblings of the tileview, not children,
    // so they stay anchored at the bottom while pages scroll past.
    //
    // anim_time tuned down from the LVGL default (300 ms) to 120 ms:
    // the QR's 280×280 1-bit canvas + DMA recompose during scroll
    // looked choppy on the ESP32-S3 at the default duration. Faster
    // snap means fewer mid-motion frames so the eye reads it as
    // "snap" rather than "stutter".
    tileview = lv_tileview_create(scr);
    lv_obj_remove_style_all(tileview);
    lv_obj_set_size(tileview, 466, 466);
    lv_obj_set_style_bg_opa(tileview, LV_OPA_TRANSP, 0);
    lv_obj_set_style_anim_time(tileview, 120, 0);
    lv_obj_clear_flag(tileview, LV_OBJ_FLAG_CLICKABLE);
    // Tileview eats horizontal swipes for its own snap logic; vertical
    // gestures (close) are caught by scr's LV_EVENT_GESTURE handler.
    // GESTURE events bubble by default, so we don't need a handler
    // registered on the tileview itself.
    lv_obj_add_flag(tileview, LV_OBJ_FLAG_GESTURE_BUBBLE);
    // First arg col_id=0,1; row_id=0; dir=LV_DIR_LEFT|RIGHT for the
    // tiles that can be swiped to/from. tile_qr can only go right
    // (to tile_pair); tile_pair can only go left (back to tile_qr).
    tile_qr   = lv_tileview_add_tile(tileview, 0, 0, LV_DIR_RIGHT);
    tile_pair = lv_tileview_add_tile(tileview, 1, 0, LV_DIR_LEFT);
    lv_obj_add_event_cb(tileview, on_tile_changed, LV_EVENT_VALUE_CHANGED, NULL);

    build_tile_qr  (tile_qr);
    build_tile_pair(tile_pair);

    // ── Page indicator dots ────────────────────────────────────────────────
    // Two small accent dots ~30 px from the bottom edge. The active
    // one is accent-coloured, the other accent_dim. Bigger displays
    // would benefit from a tap-to-jump affordance; here the row is
    // small enough that it reads as a status, not a control.
    dot_qr = lv_obj_create(scr);
    lv_obj_remove_style_all(dot_qr);
    lv_obj_set_size(dot_qr, 8, 8);
    lv_obj_set_style_radius(dot_qr, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(dot_qr, Theme::accent, 0);
    lv_obj_set_style_bg_opa(dot_qr, LV_OPA_COVER, 0);
    lv_obj_align(dot_qr, LV_ALIGN_BOTTOM_MID, -10, -35);
    lv_obj_clear_flag(dot_qr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(dot_qr, LV_OBJ_FLAG_GESTURE_BUBBLE);

    dot_pair = lv_obj_create(scr);
    lv_obj_remove_style_all(dot_pair);
    lv_obj_set_size(dot_pair, 8, 8);
    lv_obj_set_style_radius(dot_pair, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(dot_pair, Theme::accent_dim, 0);
    lv_obj_set_style_bg_opa(dot_pair, LV_OPA_COVER, 0);
    lv_obj_align(dot_pair, LV_ALIGN_BOTTOM_MID, 10, -35);
    lv_obj_clear_flag(dot_pair, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_flag(dot_pair, LV_OBJ_FLAG_GESTURE_BUBBLE);

    // ── Hint ──────────────────────────────────────────────────────────────
    // Pure ASCII — the Departure Mono font we use here doesn't have
    // glyphs for the unicode arrow chars (▸ ▴ ↑). Earlier versions
    // rendered them as missing-glyph boxes. "^ ZU" is short enough
    // not to clip on the round display and reads as "up-arrow close".
    lbl_hint = lv_label_create(scr);
    lv_label_set_text(lbl_hint, "^ ZU");
    lv_obj_set_style_text_color       (lbl_hint, Theme::text_secondary,         0);
    lv_obj_set_style_text_opa         (lbl_hint, (lv_opa_t)100,                 0);
    lv_obj_set_style_text_font        (lbl_hint, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(lbl_hint, Theme::LETTER_SPACE_LABEL,     0);
    lv_obj_align(lbl_hint, LV_ALIGN_BOTTOM_MID, 0, -15);
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
    // Always open on page 1 (QR) — that's the entry point we want most
    // users to see first. The PAIR page is a deliberate second step.
    if (tileview && tile_qr) {
        lv_tileview_set_tile(tileview, tile_qr, LV_ANIM_OFF);
    }
    if (pair_lbl) lv_label_set_text(pair_lbl, "TAP");   // reset after a previous PAIRING…
    refresh_dots(0);
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
