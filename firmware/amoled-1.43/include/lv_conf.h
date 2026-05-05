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

// === Image formats ===
#define LV_USE_BMP 0
#define LV_USE_PNG 1
#define LV_USE_GIF 0
#define LV_USE_SJPG 0

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

#endif // LV_CONF_H
