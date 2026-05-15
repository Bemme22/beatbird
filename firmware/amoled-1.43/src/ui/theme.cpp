// =============================================================================
// ui/theme.cpp — Runtime accent colour management
// =============================================================================
#include "theme.h"
#include "state.h"

#include <ctype.h>
#include <stdint.h>

namespace Theme {

lv_color_t accent     = Color::ACCENT_DEFAULT;
lv_color_t accent_dim = LV_COLOR_MAKE(0x3C, 0x32, 0x1E);   // ~25% of default gold

static uint8_t hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return 0xFF;
}

void set_accent(uint8_t r, uint8_t g, uint8_t b)
{
    accent     = LV_COLOR_MAKE(r, g, b);
    // ~25% intensity for unfilled / dim variant — kept on the same hue
    accent_dim = LV_COLOR_MAKE(r >> 2, g >> 2, b >> 2);
    State::mark_dirty(State::Dirty::ACCENT | State::Dirty::ALL);
}

bool set_accent_hex(const char *hex6)
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
    uint8_t r = (v[0] << 4) | v[1];
    uint8_t g = (v[2] << 4) | v[3];
    uint8_t b = (v[4] << 4) | v[5];
    set_accent(r, g, b);
    return true;
}

}  // namespace Theme
