// =============================================================================
// screens/screen_settings.h — Quick-settings panel (Android-style)
// =============================================================================
// Triggered by a swipe-down gesture from the player or standby screen.
// Currently hosts just one action — "Pair Bluetooth" — which sends a
// CMD:BT_PAIR command to the bridge so the bridge can flip BlueZ into
// 60 s discoverable mode. The existing SYS:bt= path handles the visible
// PAIRING overlay from there.
//
// Swipe-up or a 12 s inactivity timer closes the panel back to whichever
// screen opened it.
//
// Designed for v1: one button. Future settings (volume preset, source
// switch, brightness, ...) slot in next to the BT button without
// changing the entry/exit gestures.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace ScreenSettings {

void show();                // open with slide-from-top animation
bool is_visible();
void close();               // explicit dismiss (swipe-up or timeout)
void update();              // per-frame poll — drives auto-close timer

}  // namespace ScreenSettings
