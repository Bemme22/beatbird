// =============================================================================
// screens/split_flap.h — Mercedes-Anzeigetafel-style label transitions
// =============================================================================
// Replace the contents of an LVGL label with a "split-flap" animation:
// each character position cycles through random glyphs for a few ticks,
// staggered left-to-right, then locks to the target character. Once all
// positions are done, the label settles on the final text.
//
// Use case: track-change on the player screen. Calling SplitFlap::set_text
// instead of lv_label_set_text gives the rotating-board reveal that
// reinforces the Nothing-Glyph aesthetic.
//
// Timing: 70 ms per tick, 5 random chars per position, 1-tick stagger
// between positions. A 12-char string animates in ~12 × 70 + 5 × 70 ≈ 1.2 s.
// =============================================================================
#pragma once

#include <lvgl.h>

namespace SplitFlap {

/** Start a split-flap animation on `label`, replacing the current text
 *  with `new_text`. If an animation is already running on this label,
 *  it is killed and replaced. Falls back to a plain `lv_label_set_text`
 *  if all animation slots are in use, or if old + new texts are identical. */
void set_text(lv_obj_t *label, const char *new_text);

/** True iff a flap animation is currently active on `label`. Used by
 *  callers chaining two-phase transitions (disintegrate → assemble)
 *  to poll for phase-A completion before kicking phase B. */
bool is_running(lv_obj_t *label);

}  // namespace SplitFlap
