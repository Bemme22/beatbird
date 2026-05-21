// =============================================================================
// screens/screen_standby.h — Standby screen with weather
// =============================================================================
// Replaces the standby branch inside screen_player.cpp's show_standby_mode().
// Renders a balanced clock-top + weather-mid layout:
//
//   y=130   clock          (44 px, accent)
//   y=240   weather icon   (dot-built, ~60 px tall, centered)
//   y=320   temperature    (38 px, accent)
//   y=358   high / low     (11 px, dim)
//   y=385   condition      (10 px, faint)
//   y=415   heartbeat dot  (4 px, pulsing accent)
//
// Reads from:
//   - State::app.clockStr    (Pi pushes TIME: every minute)
//   - State::weather         (Bridge pushes WX: every 30 min)
//
// If no WX: line has ever been received (weather.valid == false), the
// screen degrades gracefully to just clock + heartbeat — same look as
// the old standby mode.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace ScreenStandby {

/** Build the LVGL tree. Idempotent — safe to call once in setup(). */
void create();

/** Make this screen active (non-animated). */
void show();

/** Returns the screen root object. */
lv_obj_t *root();

/** True iff currently the active screen. */
bool is_visible();

/** Per-frame poll. Reads dirty flags from State and repaints what changed.
 *  Cheap when nothing has changed. Call from the main loop. */
void update();

}  // namespace ScreenStandby
