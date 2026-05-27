// =============================================================================
// screens/screen_settings.h — Quick-settings panel (Android-style)
// =============================================================================
// Triggered by a swipe-down gesture from the player or standby screen.
// Hosts a QR code that points to the speaker's web UI dashboard — scanning
// it on a phone opens the controls page with the PAIR button + any other
// actions we add later. The on-display PAIR button is gone: pairing now
// starts from the phone side after scanning the QR.
//
// Swipe-up or a 12 s inactivity timer closes the panel back to whichever
// screen opened it.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace ScreenSettings {

void show();                // open with slide-from-top animation
bool is_visible();
void close();               // explicit dismiss (swipe-up or timeout)
void update();              // per-frame poll — drives auto-close timer

/** Cache the QR URL (same source as ScreenStandby::set_qr_url — the
 *  bridge pushes QR:<url> once at start, both screens receive it). */
void set_qr_url(const char *url);

}  // namespace ScreenSettings
