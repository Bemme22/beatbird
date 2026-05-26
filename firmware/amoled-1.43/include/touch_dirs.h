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

// Empirically both Beat and Zipp report touch coords in the same
// convention (right = +dx, down = +dy) once the touch_cb's rotation
// logic in main.cpp has run. Earlier per-build flips here were based
// on misinterpreted user feedback; the actual answer was "they're
// the same". Kept as constants so they're easy to flip per build
// again if a future case mounts the panel differently.
constexpr int TOUCH_DIR_RIGHT_IS_POS_DX = +1;
constexpr int TOUCH_DIR_DOWN_IS_POS_DY  = +1;
