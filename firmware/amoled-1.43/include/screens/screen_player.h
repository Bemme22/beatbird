// =============================================================================
// screens/screen_player.h — Active player + standby layouts
// =============================================================================
// Nothing-Glyph aesthetic, full Mockup-Spec:
//   · 24-dot volume ring (outer, r=218, accent)
//   · source marker at 12 o'clock (covers top vol dot)
//   · source label under marker (NDot mono, dim accent)
//   · title (display_lg, accent) + artist (display_md, dim)
//   · play/pause/stop glyph (geometric)
//   · 12-dot energy smile at bottom (r=142, FX bands left→right)
//   · volume % (display_md, accent)
//   · 60-dot progress stipple ring (r=192, accent)
//   · standby: clock + pulsating heartbeat dot
//
// Reads from State::app (with Dirty-flag-driven partial redraws) and from
// Theme::accent / Theme::accent_dim. Sends user input via Proto::send_*.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace ScreenPlayer {

/** Build LVGL tree. Idempotent — safe to call once during setup(). */
void create();

/** Make the player screen the active one (non-animated). */
void show();

/** Per-frame poll. Reads dirty flags, repaints what changed, animates
 *  the energy ring while playing. Cheap when nothing has changed. */
void update();

/** Returns the screen object — used as `transition_to` target from boot. */
lv_obj_t *root();

/** True iff this screen is currently active (regardless of standby/player mode). */
bool is_visible();

}  // namespace ScreenPlayer