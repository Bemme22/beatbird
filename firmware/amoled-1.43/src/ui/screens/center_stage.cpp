// =============================================================================
// ui/screens/center_stage.cpp — Center-stage status slot implementation
// =============================================================================
#include "screens/center_stage.h"
#include "state.h"
#include "theme.h"

#ifdef ARDUINO
  #include <Arduino.h>
#else
  #include "sim/arduino_shim.h"
#endif
#include <lvgl.h>
#include <string.h>

namespace CenterStage {

// ─── Internal state ─────────────────────────────────────────────────────────

static lv_obj_t *label   = nullptr;
static lv_obj_t *spinner = nullptr;   // 5-dot orbit, shown while sys.spotify_stuck
static bool      created = false;
static bool      last_spinner_visible = false;

// Toast: a non-persistent message with an expiry. Suppressed while any
// persistent trigger is active.
static char      toast_buf[32]    = {0};
static uint32_t  toast_expires_ms = 0;

// What's currently rendered — used to avoid redundant lv_label_set_text
// calls every frame (LVGL allocates string memory on each set_text).
static char       last_text[32]    = {0};
static lv_color_t last_color       = LV_COLOR_MAKE(0, 0, 0);
static bool       last_hidden      = true;

// Fade-in / fade-out for show ↔ hide transitions. Snap-toggling HIDDEN
// looked janky; the label now opacity-tweens. STAGE_FADE_MS matches the
// title/artist dimming tween in screen_player.cpp so the two move together.
static constexpr uint32_t STAGE_FADE_MS = 220;
static lv_anim_t anim_opa;

static void opa_cb(void *var, int32_t v) {
    lv_obj_set_style_text_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void hide_after_fade_cb(lv_anim_t *a) {
    lv_obj_t *o = (lv_obj_t *)a->var;
    if (o) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
}

static void start_fade(lv_opa_t from, lv_opa_t to, bool hide_on_end) {
    if (!label) return;
    lv_anim_del(label, opa_cb);
    lv_anim_init(&anim_opa);
    lv_anim_set_var(&anim_opa, label);
    lv_anim_set_exec_cb(&anim_opa, opa_cb);
    lv_anim_set_values(&anim_opa, from, to);
    lv_anim_set_time(&anim_opa, STAGE_FADE_MS);
    lv_anim_set_path_cb(&anim_opa, lv_anim_path_ease_out);
    if (hide_on_end) lv_anim_set_completed_cb(&anim_opa, hide_after_fade_cb);
    lv_anim_start(&anim_opa);
}

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
    TriggerResult none = { nullptr, Theme::text_primary };

    // 0. Hide on standby / shutdown.
    if (app.state == PLAY_STANDBY ||
        app.state == PLAY_SHUTDOWN ||
        app.state == PLAY_SHUTDOWN_WARN) {
        return none;
    }

    // 1. PI OFFLINE — alert color (runtime configurable via PAL: e=…).
    //    Bumped from 12 s to 30 s after cover-art transfers were measured
    //    at ~6 s on real hardware: ESP32 stops servicing serial RX while
    //    LVGL decodes the JPEG, SYS heartbeats pile up in the CDC buffer
    //    and the old 12 s threshold tripped during every cover swap.
    if (app.connected_to_pi && app.last_status_rx > 0 &&
        (now - app.last_status_rx) > 30000) {
        return { "PI OFFLINE", Theme::accent_alert };
    }

    // 2. NO NETWORK — gateway ping failing. Most urgent connectivity state
    //    short of "Pi itself is dead": speaker is islanded, Spotify/Snapcast
    //    will both fail until the route is back. Bridge sends SYS:gw=0.
    if (!sys.gateway_ok) {
        return { "NO NETWORK", Theme::accent_alert };
    }

    // 3. SPOTIFY OFFLINE — Spotify is the active source but go-librespot
    //    service is down AND we're not in the middle of a stuck-restart.
    //    Excluding stuck_recent prevents this from firing during the ~2s
    //    "activating" window after the bridge kicked off a restart — that
    //    case is owned by step 6 (RECONNECTING).
    if (app.source == SRC_SPOTIFY && !sys.svc_active && !sys.spotify_stuck) {
        return { "SPOTIFY OFFLINE", Theme::accent_alert };
    }

    // 4. PAIRING — user just opened the BT pairing window from the web UI.
    //    Bridge polls bluetoothctl's Discoverable flag and pushes SYS:bt=1
    //    while it's on. Shown in accent so it reads as "we're doing
    //    something on purpose" rather than the alert-red of broken states.
    if (sys.bt_pairing) {
        return { "PAIRING", Theme::accent };
    }

    // 5. MUTE — primary text colour (cream), it's a state announcement
    if (app.volume == 0) {
        return { "MUTE", Theme::text_primary };
    }

    // 5. PAUSE — primary text colour
    if (app.state == PLAY_PAUSED) {
        return { "PAUSE", Theme::text_primary };
    }

    // 6. RECONNECTING — bridge fired a go-librespot restart in the last
    //    minute (typically after losing the AP heartbeat). Source label
    //    is already shown elsewhere, so the bare verb is clearer than
    //    "SPOTIFY RECONNECTING" which would also overflow at 33px.
    if (app.source == SRC_SPOTIFY && sys.spotify_stuck) {
        return { "RECONNECTING", Theme::text_secondary };
    }

    // 7. WIFI WEAK — secondary text colour (dimmer, less urgent than MUTE).
    if (sys.wifi_rssi != 0 && sys.wifi_rssi < -85) {
        return { "WIFI WEAK", Theme::text_secondary };
    }

    return none;
}

// ─── Render ─────────────────────────────────────────────────────────────────

static void apply(const char *text, lv_color_t color)
{
    if (!label) return;

    if (text == nullptr) {
        if (!last_hidden) {
            // Fade out, hide once anim completes
            start_fade(LV_OPA_COVER, LV_OPA_TRANSP, /*hide_on_end=*/true);
            last_hidden = true;
            last_text[0] = '\0';
        }
        return;
    }

    if (last_hidden) {
        lv_obj_clear_flag(label, LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_text_opa(label, LV_OPA_TRANSP, 0);
        start_fade(LV_OPA_TRANSP, LV_OPA_COVER, /*hide_on_end=*/false);
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

// ─── Spinner (orbit of 5 dots) ─────────────────────────────────────────────
// Shown next to the "RECONNECTING" text while sys.spotify_stuck is on.
// Five dots orbiting a centre point at constant angular velocity, with
// per-dot opacity tapering so the rotation reads as a comet trail rather
// than a steady wheel.

static constexpr int      SPINNER_RADIUS    = 18;
static constexpr int      SPINNER_DOT_COUNT = 5;
static constexpr int      SPINNER_DOT_R     = 3;
static constexpr float    SPINNER_PERIOD_MS = 1100.0f;   // one full revolution

static void draw_circle(lv_layer_t *layer, int cx, int cy, int r,
                        lv_color_t color, lv_opa_t opa) {
    if (opa == LV_OPA_TRANSP) return;
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = color;
    dsc.bg_opa   = opa;
    dsc.radius   = LV_RADIUS_CIRCLE;
    lv_area_t a = { cx - r, cy - r, cx + r, cy + r };
    lv_draw_rect(layer, &dsc, &a);
}

static void spinner_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_obj_t  *obj    = (lv_obj_t *)lv_event_get_target(e);
    lv_area_t coords; lv_obj_get_coords(obj, &coords);
    const int cx = (coords.x1 + coords.x2) / 2;
    const int cy = (coords.y1 + coords.y2) / 2;

    const float t = (float)millis() / SPINNER_PERIOD_MS;
    const float phase = (t - floorf(t)) * 6.2832f;   // 0..2π
    for (int i = 0; i < SPINNER_DOT_COUNT; i++) {
        // Trail: dot 0 leads (brightest), dot N-1 trails (dimmest).
        float a = phase - (float)i * (6.2832f / (float)SPINNER_DOT_COUNT) * 0.5f;
        int x = cx + (int)roundf(cosf(a) * (float)SPINNER_RADIUS);
        int y = cy + (int)roundf(sinf(a) * (float)SPINNER_RADIUS);
        // Linear opacity falloff over the trail.
        lv_opa_t opa = (lv_opa_t)(255 - (i * 200 / (SPINNER_DOT_COUNT - 1)));
        draw_circle(layer, x, y, SPINNER_DOT_R, Theme::accent, opa);
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
    lv_obj_set_style_text_color       (label, Theme::text_primary,         0);
    lv_obj_set_style_text_font        (label, Theme::font_display_lg(),    0);
    lv_obj_set_style_text_letter_space(label, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align       (label, LV_TEXT_ALIGN_CENTER,        0);
    lv_obj_align(label, LV_ALIGN_CENTER, 0, 0);   // dead center
    lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
    // Touch passes through to the parent screen
    lv_obj_add_flag(label, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(label, LV_OBJ_FLAG_CLICKABLE);

    // Spinner widget — small (60×60), positioned just below the label.
    // Hidden by default; update() reveals it while spotify_stuck is on.
    spinner = lv_obj_create(parent);
    lv_obj_remove_style_all(spinner);
    lv_obj_set_size(spinner, 60, 60);
    lv_obj_align(spinner, LV_ALIGN_CENTER, 0, 50);
    lv_obj_set_style_bg_opa(spinner, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(spinner, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag  (spinner, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_flag  (spinner, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_event_cb(spinner, spinner_draw_cb, LV_EVENT_DRAW_MAIN, NULL);
}

void update()
{
    if (!created) return;

    // Spinner visibility tracks spotify_stuck independently of the text
    // priority chain so it doesn't compete with PI OFFLINE / NO NETWORK
    // overlays — those are bigger emergencies that take the centre.
    const bool want_spinner =
        State::sys.spotify_stuck &&
        State::app.state != State::PLAY_STANDBY &&
        State::app.state != State::PLAY_SHUTDOWN &&
        State::app.state != State::PLAY_SHUTDOWN_WARN &&
        !(State::app.connected_to_pi == false ||
          !State::sys.gateway_ok);
    if (want_spinner != last_spinner_visible) {
        if (want_spinner) lv_obj_clear_flag(spinner, LV_OBJ_FLAG_HIDDEN);
        else              lv_obj_add_flag  (spinner, LV_OBJ_FLAG_HIDDEN);
        last_spinner_visible = want_spinner;
    }
    if (want_spinner) lv_obj_invalidate(spinner);

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
    if (label) {
        // Hard reset — kill any in-flight fade, hide immediately. Used by
        // standby/shutdown transitions where the player chrome owns the
        // centre after this call.
        lv_anim_del(label, opa_cb);
        lv_obj_set_style_text_opa(label, LV_OPA_TRANSP, 0);
        lv_obj_add_flag(label, LV_OBJ_FLAG_HIDDEN);
    }
}

}  // namespace CenterStage
