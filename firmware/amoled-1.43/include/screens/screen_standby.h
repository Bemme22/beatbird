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

/** Replace the rotating idle text with `text` using a split-flap animation.
 *  Triggered by Pi's STBY: serial line every ~45s while idle. Safe to call
 *  when the standby screen has not been built yet — caches the message and
 *  applies it on next create(). */
void set_flap_text(const char *text);

/** Set the standby date line (e.g. "SAMSTAG · 7. JUNI"). The bridge pushes a
 *  preformatted, localized string via the DATE: serial line on connect and on
 *  day-change. Safe to call before create(); cached and applied lazily. */
void set_date(const char *text);

/** Cache the BT-pairing QR URL (e.g. "http://zipp2minipi.local:8080/"). The
 *  bridge pushes this once at start + on display [boot] reconnect; the
 *  standby screen renders it as a QR code only while sys.bt_pairing == true,
 *  replacing the clock + weather block. Phone scans → opens speaker's web
 *  UI which has full pairing controls. Safe to call before create(); cached
 *  and applied lazily. */
void set_qr_url(const char *url);

}  // namespace ScreenStandby
