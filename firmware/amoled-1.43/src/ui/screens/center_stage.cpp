// =============================================================================
// ui/screens/center_stage.cpp — Center-stage status slot implementation
// =============================================================================
#include "screens/center_stage.h"
#include "state.h"
#include "theme.h"

#include <Arduino.h>
#include <lvgl.h>
#include <string.h>

namespace CenterStage {

// ─── Internal state ─────────────────────────────────────────────────────────

static lv_obj_t *label = nullptr;
static bool      created = false;

// Toast: a non-persistent message with an expiry. Suppressed while any
// persistent trigger is active.
static char      toast_buf[32]    = {0};
static uint32_t  toast_expires_ms = 0;

// What's currently rendered — used to avoid redundant lv_label_set_text
// calls every frame (LVGL allocates string memory on each set_text).
static char       last_text[32]    = {0};
static lv_color_t last_color       = LV_COLOR_MAKE(0, 0, 0);
static bool       last_hidden      = true;

// ─── Trigger evaluation ─────────────────────────────────────────────────────
//
// Returns the priority-winning persistent trigger, or {nullptr, _} if no
// persistent trigger is active (toast may still apply).

struct TriggerResult {
    const char *text;
    lv_color_t  color;
};

static TriggerResult evaluate_persistent_trigger()
{
    using namespace State;
    const uint32_t now = millis();
    TriggerResult none = { nullptr, Theme::accent };

    // 1. PI OFFLINE — only after we've received at least one status
    //    (avoids a false-positive at cold boot before bridge connect)
    if (app.connected_to_pi && app.last_status_rx > 0 &&
        (now - app.last_status_rx) > 5000) {
        return { "PI OFFLINE", Theme::Color::ACCENT_ALERT };
    }

    // 2. MUTE — volume reduced to 0
    if (app.volume == 0) {
        return { "MUTE", Theme::accent };
    }

    // 3. PAUSE — explicit pause state
    if (app.state == PLAY_PAUSED) {
        return { "PAUSE", Theme::accent };
    }

    // 4. WIFI WEAK — RSSI in danger zone, but rssi == 0 means "not
    //    reported yet" and shouldn't fire the warning
    if (sys.wifi_rssi != 0 && sys.wifi_rssi < -85) {
        return { "WIFI WEAK", Theme::accent_dim };
    }

    return none;
}

// ─── Render ─────────────────────────────────────────────────────────────────

static void apply(const char *text, lv_color_t color)
{
    if (!label) return;

    if (text == nullptr) {
        if (!last_hidden) {
            lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
            last_hidden = true;
            last_text[0] = '\0';
        }
        return;
    }

    if (last_hidden) {
        lv_obj_clear_flag(label, LV_OBJ_FLAG_HIDDEN);
        last_hidden = false;
    }

    // Update text if changed
    if (strncmp(text, last_text, sizeof(last_text)) != 0) {
        lv_label_set_text(label, text);
        strncpy(last_text, text, sizeof(last_text) - 1);
        last_text[sizeof(last_text) - 1] = '\0';
    }

    // Update color if changed (LVGL 9: lv_color_t has separate r/g/b members,
    // no .full union; compare component-wise to avoid layout assumptions)
    if (color.red   != last_color.red   ||
        color.green != last_color.green ||
        color.blue  != last_color.blue) {
        lv_obj_set_style_text_color(label, color, 0);
        last_color = color;
    }
}

// ─── Public API ─────────────────────────────────────────────────────────────

void create(lv_obj_t *parent)
{
    if (created) return;
    if (!parent) return;
    created = true;

    label = lv_label_create(parent);
    lv_label_set_text(label, "");
    lv_obj_set_style_text_color       (label, Theme::accent,               0);
    lv_obj_set_style_text_font        (label, Theme::font_display_lg(),    0);
    lv_obj_set_style_text_letter_space(label, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align       (label, LV_TEXT_ALIGN_CENTER,        0);
    lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);   // dead center
    lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
    // Touch passes through to the parent screen
    lv_obj_add_flag(label, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(label, LV_OBJ_FLAG_CLICKABLE);
}

void update()
{
    if (!created) return;

    TriggerResult t = evaluate_persistent_trigger();

    if (t.text) {
        // Persistent wins — show it, kill any pending toast
        apply(t.text, t.color);
        toast_expires_ms = 0;
        return;
    }

    // Otherwise toast (if alive)
    const uint32_t now = millis();
    if (toast_expires_ms != 0 && now < toast_expires_ms) {
        apply(toast_buf, Theme::accent);
        return;
    }
    if (toast_expires_ms != 0 && now >= toast_expires_ms) {
        toast_expires_ms = 0;   // expired
    }

    // Nothing — hide label
    apply(nullptr, Theme::accent);
}

void show_toast(const char *text, uint32_t duration_ms)
{
    if (!text || !text[0]) return;
    strncpy(toast_buf, text, sizeof(toast_buf) - 1);
    toast_buf[sizeof(toast_buf) - 1] = '\0';
    toast_expires_ms = millis() + duration_ms;
    // Don't call apply() directly — update() will pick it up next frame.
    // This avoids the toast briefly flashing if a persistent trigger
    // becomes active at the same time.
}

bool is_active()
{
    return created && !last_hidden;
}

void invalidate()
{
    last_text[0] = '\0';
    last_color   = LV_COLOR_MAKE(0, 0, 0);
    last_hidden  = true;
    if (label) lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
}

}  // namespace CenterStage
