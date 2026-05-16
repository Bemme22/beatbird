// =============================================================================
// screens/screen_boot.h — Boot / connection-waiting screen
// =============================================================================
// Shown from cold-boot until the Pi sends its first state line. Renders the
// BEATBIRD wordmark, a breathing "CONNECTING" status, a rotating outer arc,
// and a subline that mirrors `app.boot_progress` if the Pi emits BOOT: lines
// during start-up.
//
// Lifecycle:
//
//   setup():          ScreenBoot::create();    // build LVGL tree
//                     ScreenBoot::show();      // load as initial screen
//
//   loop() / proto:   when State::app.connected_to_pi becomes true,
//                     call ScreenBoot::transition_to(scr_main).
//
//                     ScreenBoot::update() handles palette swaps (accent
//                     change after PAL:) and subline updates from boot
//                     progress. Call it from the main loop every tick.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace ScreenBoot {

/** Build the screen objects + start the idle animations.
 *  Safe to call once during setup(). Calling twice is a no-op. */
void create();

/** Make the boot screen the active one (non-animated). Use at startup. */
void show();

/** Per-frame poll. Updates accent colour / subline text when dirty. */
void update();

/** Animate-fade from boot to `target` and stop the boot-screen animations.
 *  Idempotent — second call is a no-op. */
void transition_to(lv_obj_t *target);

/** True until transition_to() has run. */
bool is_active();

}  // namespace ScreenBoot
