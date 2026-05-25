#ifndef LV_CONF_H
#define LV_CONF_H

// LVGL 9.x Configuration for BeatBird Display
// Board: ESP32-S3 with 8MB PSRAM, 466x466 AMOLED

// === Color ===
#define LV_COLOR_DEPTH 16

// === Memory ===
#define LV_MEM_CUSTOM 1          // Use stdlib malloc (PSRAM-aware)
#define LV_MEM_SIZE (256 * 1024) // Fallback if custom not used

// === Display ===
#define LV_USE_OS LV_OS_NONE
#define LV_DEF_REFR_PERIOD 16    // ~60fps target

// === Drawing ===
#define LV_DRAW_BUF_STRIDE_ALIGN 4
#define LV_DRAW_BUF_ALIGN 4

// === Logging ===
#define LV_USE_LOG 1
#define LV_LOG_LEVEL LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF 1

// === Input devices ===
#define LV_USE_INDEV 1

// === Widgets (enable what we need) ===
#define LV_USE_ARC 1
#define LV_USE_LABEL 1
#define LV_USE_IMAGE 1
#define LV_USE_BTN 1
#define LV_USE_SLIDER 1
#define LV_USE_SWITCH 1
#define LV_USE_ROLLER 1
#define LV_USE_ANIMIMG 1

// Label scroll speed (px/sec) for LV_LABEL_LONG_SCROLL / SCROLL_CIRCULAR.
// LVGL default is 40 — for Departure-Mono titles at 33 px that's too quick
// to read comfortably. 20 gives a leisurely, easy-to-follow pace.
#define LV_LABEL_DEF_SCROLL_SPEED 20

// === Image formats ===
#define LV_USE_BMP 0
#define LV_USE_PNG 1
#define LV_USE_GIF 0
#define LV_USE_SJPG 0
// TJpgDec (tinyjpgdec) — small JPEG decoder used by the album-cover
// background. Pi pre-processes covers to ~5-30 KB JPEGs; firmware
// decodes them on the fly when the IMG: stream completes.
//
// LV_USE_FS_MEMFS is the required companion: tjpgd reads via LVGL's FS
// abstraction even when the source is an in-memory buffer. Without it
// the decoder logs "needs FS_MEMFS to decode from data" and falls back
// to drawing nothing.
#define LV_USE_TJPGD 1
#define LV_USE_FS_MEMFS 1
#define LV_FS_MEMFS_LETTER 'M'

// === Font ===
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_18 1
#define LV_FONT_MONTSERRAT_24 1
#define LV_FONT_MONTSERRAT_32 1
#define LV_FONT_MONTSERRAT_48 1
#define LV_FONT_DEFAULT &lv_font_montserrat_18

// === Animations ===
#define LV_USE_ANIM 1

// === Themes ===
#define LV_USE_THEME_DEFAULT 1
#define LV_THEME_DEFAULT_DARK 1  // Dark theme for AMOLED

// === Extra features ===
#define LV_USE_SNAPSHOT 0
#define LV_USE_GRID 1
#define LV_USE_FLEX 1

// === Tick ===
// LVGL 9.2 removed LV_TICK_CUSTOM — use lv_tick_set_cb() in setup() instead.

// ─── Simulator backend (desktop builds only) ────────────────────────────────
// Enabled by the [env:sim] PlatformIO env which sets -DBEATBIRD_SIM=1.
// On the ESP32 path these defines stay off and the panel init in main.cpp
// owns the LVGL display the usual way.
#ifdef BEATBIRD_SIM
  #define LV_USE_SDL          1
  #define LV_SDL_HOR_RES      466
  #define LV_SDL_VER_RES      466
  #define LV_SDL_RENDER_MODE  LV_DISPLAY_RENDER_MODE_DIRECT
  #define LV_SDL_BUF_COUNT    1
  #define LV_SDL_FULLSCREEN   0
  // DIRECT_EXIT=1 closes the program when the SDL window is closed.
  #define LV_SDL_DIRECT_EXIT  1
  // Keep LV_USE_OS=LV_OS_NONE so we tick LVGL manually from sim_main's
  // main loop — simpler than wiring up pthreads, and aligns with how the
  // ESP32 build does it (no OS scheduling layer inside LVGL).
#endif

#endif // LV_CONF_H