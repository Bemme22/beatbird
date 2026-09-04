#pragma once
// =============================================================================
// led_status.h — addressable status strip on the display ESP32
// =============================================================================
// RobinPi's "Brustfleck": a short SK6812-RGBW cluster behind a resin light
// guide, wired to a free GPIO of the AMOLED board. It hangs on the display
// ESP32 rather than the Pi because both sit on the front of the enclosure
// (one harness instead of a lead across the pressure chamber) and because the
// ESP32 already parses the bridge's ST:/SYS:/PAL: lines — the status logic is
// free here and would have to be rebuilt on the Pi.
//
// NOTHING is driven until the bridge pushes a LED: line (see docs/protocol.md).
// That is deliberate, not laziness:
//   * pin/count/chip are per-ENCLOSURE facts, so they belong in the speaker
//     profile YAML — one firmware image serves every speaker;
//   * a compile-time default pin would be actively dangerous. GPIO18 (the
//     RobinPi strip pin) is MAIN_I2C_SDA on the 1.43 board — a Beat/Zipp
//     flashed with a default-on strip would bit-bang its own I2C bus.
// A speaker with no strip configured therefore costs a few hundred bytes of
// flash and no GPIO at all.
// =============================================================================

#include <stdint.h>

namespace LedStatus {

// How the cluster maps state onto pixels.
enum Mapping : uint8_t {
    MAP_AREA   = 0,  // whole cluster = one field; energy drives BRIGHTNESS.
                     // Correct behind a diffuser, where position is lost.
    MAP_MIRROR = 1,  // symmetric centre→outside meter (BeatPiMini's two
                     // side strips, where position IS visible).
};

#ifdef ARDUINO

/** (Re)configure the strip from a LED: line. count == 0 disables it and frees
 *  the driver. Idempotent: an identical config is a no-op, so the bridge can
 *  re-send on every reconnect without the strip flickering. Returns false if
 *  the config was rejected (implausible pin/count). */
bool configure(int pin, int count, bool rgbw, uint8_t brightness, Mapping mapping);

/** Render one frame. Call from loop(); self-throttles to ~50 fps. */
void tick();

/** True once a LED: line has enabled a strip. */
bool active();

#else   // desktop simulator — no NeoPixel hardware

inline bool configure(int, int, bool, uint8_t, Mapping) { return false; }
inline void tick() {}
inline bool active() { return false; }

#endif

}  // namespace LedStatus
