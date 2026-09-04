#pragma once
// =============================================================================
// led_status.h — addressable status strip on the display ESP32
// =============================================================================
// RobinPi calls it the "Brustfleck": two SK6812-RGBW bars flanking the driver
// on the front baffle, wired to a free GPIO of the AMOLED board. They hang on
// the display ESP32 rather than the Pi because both sit on the front (one
// harness instead of a lead across the pressure chamber) and because the ESP32
// already parses the bridge's ST:/SYS:/PAL: lines — the status logic is free
// here and would have to be rebuilt on the Pi.
//
// NOTHING is driven until the bridge pushes a LED: line (see docs/protocol.md).
// That is deliberate, not laziness:
//   * pin/count/chip/wiring are per-ENCLOSURE facts, so they belong in the
//     speaker profile YAML — one firmware image serves every speaker;
//   * a compile-time default pin would be actively dangerous. GPIO18 (the
//     RobinPi strip pin) is MAIN_I2C_SDA on the 1.43 board — a Beat/Zipp
//     flashed with a default-on strip would bit-bang its own I2C bus.
// A speaker with no strip configured therefore costs a few hundred bytes of
// flash and no GPIO at all.
//
// Rendering runs in its own FreeRTOS task, not in loop(): LVGL blocks loop()
// for tens of ms per frame and an animation clocked off that inherits every
// hitch.
// =============================================================================

#include <stdint.h>

namespace LedStatus {

// How the strip maps state onto pixels.
enum Mapping : uint8_t {
    MAP_AREA   = 0,  // whole strip = one field; level and colour carry the
                     // state, position carries nothing. For a diffused cluster.
    MAP_MIRROR = 1,  // two symmetric bars, filled from the centre outwards.
                     // For bars that are visible along their length.
};

// Where the two bars of a MAP_MIRROR strip are joined — a WIRING fact, hence a
// profile setting. INNER: the jumper hides behind the driver, so the chain runs
// outer-left → centre → outer-right (index 0 = far left). OUTER: the bars are
// joined at their far ends, so index 0 = centre-left.
enum ChainJoin : uint8_t {
    JOIN_INNER = 0,
    JOIN_OUTER = 1,
};

#ifdef ARDUINO

/** (Re)configure the strip from a LED: line. count == 0 disables it and frees
 *  the driver. Idempotent: an identical config is a no-op, so the bridge can
 *  re-send on every reconnect without the strip flickering. Starts the render
 *  task on first success. Returns false if the config was rejected
 *  (implausible pin/count). */
bool configure(int pin, int count, bool rgbw, uint8_t brightness,
               Mapping mapping, ChainJoin join);

/** True once a LED: line has enabled a strip. */
bool active();

#else   // desktop simulator — no strip hardware

inline bool configure(int, int, bool, uint8_t, Mapping, ChainJoin) { return false; }
inline bool active() { return false; }

#endif

}  // namespace LedStatus
