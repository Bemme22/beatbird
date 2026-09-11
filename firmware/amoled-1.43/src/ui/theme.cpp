// =============================================================================
// ui/theme.cpp — Runtime palette management
// =============================================================================
// Six tokens are exposed for runtime override via the extended PAL: protocol.
// Each defaults to a compile-time constant from Theme::Color so the firmware
// always boots with a sensible palette before the bridge connects.
// =============================================================================
#include "theme.h"
#include "state.h"

#include <ctype.h>
#include <stdint.h>

namespace Theme {

lv_color_t accent         = Color::ACCENT_DEFAULT;
lv_color_t accent_glow    = Color::ACCENT_GLOW_DEFAULT;
lv_color_t accent_dim     = Color::ACCENT_DIM_DEFAULT;
lv_color_t text_primary   = Color::TEXT_PRIMARY_DEFAULT;
lv_color_t text_secondary = Color::TEXT_SECONDARY_DEFAULT;
lv_color_t accent_alert   = Color::ACCENT_ALERT_DEFAULT;

static uint8_t hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return 0xFF;
}

// Parse a 6-char hex string into (r, g, b). Returns false on any non-hex
// digit or short string.
static bool parse_hex6(const char *hex6, uint8_t &r, uint8_t &g, uint8_t &b)
{
    if (!hex6) return false;
    uint8_t v[6];
    for (int i = 0; i < 6; i++) {
        char c = hex6[i];
        if (!c) return false;
        uint8_t n = hex_nibble(c);
        if (n == 0xFF) return false;
        v[i] = n;
    }
    r = (v[0] << 4) | v[1];
    g = (v[2] << 4) | v[3];
    b = (v[4] << 4) | v[5];
    return true;
}

// Brighten a colour WITHOUT washing it out: scale all three channels by the
// same gain until the largest one reaches 255. In HSV terms that is V -> 1
// with hue and saturation untouched, because the hue lives in the channel
// RATIO, not in the individual values.
//
// ⚠️⚠️ DURABLE — for a LIGHT SOURCE, "brighter" means more saturated, not
// more white. The obvious lerp towards white produces a pastel, and
// led_status.cpp renders accent_glow in the PLAY state at ~full level:
// pale colour × full level = white light. RobinPi's VU meter ran white
// while every other state was gold, because glow was never derived at all
// and sat on its compile-time default (observed 05.09.2026). The brightness
// is already there — only saturation can still carry the colour.
static lv_color_t brighten_saturating(uint8_t r, uint8_t g, uint8_t b)
{
    uint8_t mx = r;
    if (g > mx) mx = g;
    if (b > mx) mx = b;
    if (mx == 0) return LV_COLOR_MAKE(r, g, b);   // black: no hue to preserve
    return LV_COLOR_MAKE((uint8_t)((r * 255 + mx / 2) / mx),
                         (uint8_t)((g * 255 + mx / 2) / mx),
                         (uint8_t)((b * 255 + mx / 2) / mx));
}

void set_accent(uint8_t r, uint8_t g, uint8_t b)
{
    accent = LV_COLOR_MAKE(r, g, b);
    // Both shades that hang off the accent are derived here, unless the
    // bridge pushes explicit slots via the extended PAL: command. We can't
    // tell at this point whether it will, so we always recompute — the
    // parser walks the slots in the fixed order "agdpse", so an explicit
    // g=/d= later in the SAME line overwrites the derivation.
    accent_dim  = LV_COLOR_MAKE(r >> 2, g >> 2, b >> 2);   // ~25 % on black
    accent_glow = brighten_saturating(r, g, b);
    State::mark_dirty(State::Dirty::ACCENT | State::Dirty::ALL);
}

bool set_accent_hex(const char *hex6)
{
    uint8_t r, g, b;
    if (!parse_hex6(hex6, r, g, b)) return false;
    set_accent(r, g, b);
    return true;
}

bool set_palette_slot_hex(char slot, const char *hex6)
{
    uint8_t r, g, b;
    if (!parse_hex6(hex6, r, g, b)) return false;
    switch (slot) {
        case 'a': set_accent(r, g, b); return true;     // recomputes dim + marks dirty
        case 'g': accent_glow    = LV_COLOR_MAKE(r, g, b); break;
        case 'd': accent_dim     = LV_COLOR_MAKE(r, g, b); break;
        case 'p': text_primary   = LV_COLOR_MAKE(r, g, b); break;
        case 's': text_secondary = LV_COLOR_MAKE(r, g, b); break;
        case 'e': accent_alert   = LV_COLOR_MAKE(r, g, b); break;
        default:  return false;
    }
    State::mark_dirty(State::Dirty::ACCENT | State::Dirty::ALL);
    return true;
}

}  // namespace Theme
