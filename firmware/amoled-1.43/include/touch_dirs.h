// =============================================================================
// include/touch_dirs.h — per-build touch direction signs
// =============================================================================
// Beat #1 and Zipp Mini 2 use the same Waveshare AMOLED panel but mount
// it differently in their cases — Beat's panel sits rotated 180°
// relative to Zipp. Position-based UI works the same on both (the
// firmware draws into LVGL's coord frame and the touch chip reports in
// the same frame), but swipe directions read opposite to the user:
// physical-swipe-right on Beat is touch-dx<0, while on Zipp it's dx>0.
//
// Rotary volume is unaffected because atan2's rotation direction is
// preserved under axis mirroring (both x and y flip cancel out).
//
// Multiply your raw dx / dy by these signs before comparing to zero:
//
//   if (dx * TOUCH_DIR_RIGHT_IS_POS_DX > 0)  ... swipe-right action
//   if (dy * TOUCH_DIR_DOWN_IS_POS_DY  > 0)  ... swipe-down action
//   if (dy * TOUCH_DIR_DOWN_IS_POS_DY  < 0)  ... swipe-up action
//
// =============================================================================
#pragma once

// Per-case panel mount. After all the back-and-forth: Beat's case has
// the panel mounted such that LVGL's +x axis matches the user's
// physical right; Zipp's case (or the DEG=90 rotation we apply to the
// panel) ends up with X reading mirrored. Multiplying dx-derived
// gestures + the rotary atan2's x argument by this constant collapses
// the difference so the rest of the code can assume a single
// "right is positive x, down is positive y" convention.
//
// Verified empirically: with the multiplier set per below the user
// reports clockwise rotary = volume up and swipe-right = NEXT on
// both speakers.
#ifdef DISPLAY_ROTATE_NATIVE
    // Beat — no flip.
    constexpr int TOUCH_DIR_RIGHT_IS_POS_DX = +1;
    constexpr int TOUCH_DIR_DOWN_IS_POS_DY  = +1;
#else
    // Zipp DEG=90 — as of the main.cpp touch-cb X-flip
    // (point.x = (LCD_WIDTH-1) - raw_y), X is no longer mirrored, so the
    // gesture math needs +1 like Beat. The flip in main.cpp + this +1 move
    // in lockstep: skip/rotary (which use this multiplier) net to no change,
    // while taps + LVGL-native gestures (carousel tileview) are now correct.
    constexpr int TOUCH_DIR_RIGHT_IS_POS_DX = +1;
    constexpr int TOUCH_DIR_DOWN_IS_POS_DY  = +1;
#endif
