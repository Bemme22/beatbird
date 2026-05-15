// =============================================================================
// theme.h — BeatBird Display Theme System
// =============================================================================
// Central source of truth for the visual language. Everything that has a
// colour, a size, a font, a duration or an opacity lives here. Screens read
// from this header — they never define their own constants.
//
// Accent colour is a runtime variable (Theme::accent) that the Pi pushes via
// `PAL:rrggbb` once after the serial connect handshake. All other tokens are
// compile-time constants tuned for the Nothing-Glyph aesthetic on a round
// 466×466 AMOLED panel.
// =============================================================================
#pragma once

#include <lvgl.h>
#include <stdint.h>

namespace Theme {

// ─── Display geometry ────────────────────────────────────────────────────────
// All radii are measured from the geometric centre (233, 233). Values are in
// physical pixels. Keep them aligned with the mockup so the firmware matches
// the design preview.

constexpr int CENTER       = 233;   // 466 / 2
constexpr int VOL_RING_R   = 218;   // outer volume dot ring
constexpr int VOL_DOT_R    = 4;     // filled dot radius
constexpr int VOL_DOT_R_DIM = 2;    // unfilled / dim dot radius
constexpr int VOL_DOT_COUNT = 24;   // dots around the ring

constexpr int PROG_RING_R  = 192;   // progress stipple ring
constexpr int PROG_DOT_R   = 1;     // very small — almost a pixel
constexpr int PROG_SEG_COUNT = 60;
constexpr int PROG_ARC_START_DEG = 135;   // bottom-left
constexpr int PROG_ARC_SWEEP_DEG = 270;   // around the bottom

constexpr int ENERGY_RING_R = 142;
constexpr int ENERGY_DOT_R  = 3;
constexpr int ENERGY_DOT_R_PEAK = 5;
constexpr int ENERGY_DOT_COUNT = 12;
constexpr int ENERGY_ARC_START_DEG = 200;  // bottom only — like a smile
constexpr int ENERGY_ARC_SWEEP_DEG = 140;
constexpr int ENERGY_DEFLECTION_PX = 16;   // max outward push of a peak dot

constexpr int SOURCE_MARKER_SIZE = 10;     // square px
constexpr int SOURCE_MARKER_Y    = 9;      // distance from top edge

// Text placement (relative to centre)
constexpr int TITLE_Y_OFFSET     = -22;
constexpr int ARTIST_Y_OFFSET    =  14;
constexpr int STATE_ICON_Y       =  64;
constexpr int VOLUME_PCT_Y       =  122;
constexpr int SOURCE_LABEL_Y     = -192;

// ─── Animations & timing ────────────────────────────────────────────────────

constexpr uint32_t ANIM_FADE_MS       = 200;
constexpr uint32_t ANIM_SWEEP_MS      = 350;
constexpr uint32_t STANDBY_AFTER_MS   = 5 * 60 * 1000;   // 5 min idle
constexpr uint32_t DIM_AFTER_MS       = 30 * 1000;       // 30 s idle dims display
constexpr uint32_t ACTION_TOAST_MS    = 1200;
constexpr uint32_t LONG_PRESS_MS      = 1500;
constexpr uint8_t  DIM_BRIGHTNESS     = 140;
constexpr uint8_t  FULL_BRIGHTNESS    = 255;

// ─── Colour palette (compile-time constants) ────────────────────────────────
// All colours are RGB565-safe on the SH8601. The accent is set at runtime
// from the profile via the PAL: serial command.

namespace Color {
    // Backgrounds — pure black for AMOLED pixels-off
    constexpr lv_color_t BG           = LV_COLOR_MAKE(0x00, 0x00, 0x00);
    constexpr lv_color_t BG_DIM       = LV_COLOR_MAKE(0x0F, 0x0F, 0x0F);
    constexpr lv_color_t BG_DEEP      = LV_COLOR_MAKE(0x16, 0x16, 0x16);

    // Foregrounds — neutral hierarchy
    constexpr lv_color_t TEXT_BODY    = LV_COLOR_MAKE(0x9a, 0x9a, 0x9a);
    constexpr lv_color_t TEXT_DIM     = LV_COLOR_MAKE(0x5a, 0x5a, 0x5a);
    constexpr lv_color_t TEXT_FAINT   = LV_COLOR_MAKE(0x2e, 0x2e, 0x2e);

    // Source markers (small dot only)
    constexpr lv_color_t SRC_SPOTIFY  = LV_COLOR_MAKE(0x1E, 0xD7, 0x60);
    constexpr lv_color_t SRC_BT       = LV_COLOR_MAKE(0x3F, 0x8C, 0xFF);
    constexpr lv_color_t SRC_TOSLINK  = LV_COLOR_MAKE(0xFF, 0x95, 0x00);
    constexpr lv_color_t SRC_SNAPCAST = LV_COLOR_MAKE(0xA6, 0x78, 0xFF);
    constexpr lv_color_t SRC_NONE     = LV_COLOR_MAKE(0x3a, 0x3a, 0x3a);

    // Defaults — overridden at runtime by PAL: command
    // Champagne gold — tuned for Zipp Mini 2 turquoise/cream enclosure
    constexpr lv_color_t ACCENT_DEFAULT = LV_COLOR_MAKE(0xF0, 0xCB, 0x7B);
}

// ─── Runtime accent (set by PAL: command from Pi) ───────────────────────────

extern lv_color_t accent;
extern lv_color_t accent_dim;   // ~25% accent on black, for unfilled segments

/** Apply a new accent colour. Called by the protocol layer when a PAL: line
 *  arrives. Recomputes derived shades and triggers a global UI refresh. */
void set_accent(uint8_t r, uint8_t g, uint8_t b);

/** Parse a 6-char hex string (no leading #) and apply. Returns true on success. */
bool set_accent_hex(const char *hex6);

// ─── Fonts ──────────────────────────────────────────────────────────────────
// Until Departure Mono is converted with lv_font_conv, we use Montserrat for
// build compatibility. The font getters are indirected so we can swap them
// out by changing only this file.
//
// Goal fonts (TODO: convert to LVGL .c via lv_font_conv):
//   FONT_DISPLAY  →  DepartureMono-Regular at 36px and 28px
//   FONT_BODY     →  DepartureMono-Regular at 14px
//
// See firmware/amoled-1.43/fonts/README.md for the conversion command.

inline const lv_font_t *font_display_lg() { return &lv_font_montserrat_32; }
inline const lv_font_t *font_display_md() { return &lv_font_montserrat_24; }
inline const lv_font_t *font_body()       { return &lv_font_montserrat_14; }
inline const lv_font_t *font_label()      { return &lv_font_montserrat_14; }
inline const lv_font_t *font_clock()      { return &lv_font_montserrat_48; }

// Letter spacing — pixel-style mono benefits from extra tracking
constexpr int LETTER_SPACE_DISPLAY = 3;
constexpr int LETTER_SPACE_LABEL   = 4;
constexpr int LETTER_SPACE_BODY    = 2;

}  // namespace Theme
