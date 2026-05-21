// =============================================================================
// screens/center_stage.h — Status announcement slot in the player screen
// =============================================================================
// Single LVGL label centered on the player screen that becomes the
// "status stage" — only shows text when something needs the user's
// attention. Otherwise empty so Title + Artist have the bühne.
//
// Trigger priority (highest first):
//   1. PI OFFLINE   (last_status_rx > 5 s, accent_alert)
//   2. MUTE         (volume == 0, accent)
//   3. PAUSE        (state == PAUSED, accent)
//   4. WIFI WEAK    (rssi < -85, accent_dim)
//   5. Toast        (1.2 s, accent — only when no persistent trigger)
//   - none → label hidden, title/artist fully visible
//
// Triggers are evaluated automatically inside update(). Toasts are
// raised externally via show_toast() — typically from on_released() in
// screen_player.cpp when a NEXT or PREV command is sent.
// =============================================================================
#pragma once

#include <lvgl.h>
#include <stdint.h>

namespace CenterStage {

/** Create the label inside `parent`. Safe to call once during the
 *  parent screen's create(). Idempotent — second call is a no-op. */
void create(lv_obj_t *parent);

/** Per-frame poll. Evaluates the trigger priority chain against
 *  current State and updates the label. Cheap when nothing changed. */
void update();

/** Push a transient toast (e.g. "SKIP →") for `duration_ms`. Toasts
 *  are suppressed while any persistent trigger is active — they only
 *  show when the stage is otherwise empty. */
void show_toast(const char *text, uint32_t duration_ms = 1200);

/** True iff a persistent trigger or toast is currently being shown. */
bool is_active();

/** Force the label to clear and re-evaluate on next update(). Used by
 *  screen_player.cpp when leaving / entering standby. */
void invalidate();

}  // namespace CenterStage
