// =============================================================================
// state.h — BeatBird App State
// =============================================================================
// Single source of truth for what the display is currently showing. Every
// screen reads from State::app, the protocol layer writes to it. Mutations
// must be done via the `state_*` setters so dirty-flags can be raised and
// screens redraw efficiently.
// =============================================================================
#pragma once

#include <Arduino.h>
#include <stdint.h>

namespace State {

enum PlayState : uint8_t {
    PLAY_STOPPED       = 0,
    PLAY_PLAYING       = 1,
    PLAY_PAUSED        = 2,
    PLAY_STANDBY       = 3,
    PLAY_SHUTDOWN_WARN = 4,   // user is holding the power button — show "halten zum ausschalten"
    PLAY_SHUTDOWN      = 5,   // long-press confirmed — pi is about to poweroff
};

enum Source : uint8_t {
    SRC_NONE      = 0,
    SRC_SPOTIFY   = 1,
    SRC_BLUETOOTH = 2,
    SRC_TOSLINK   = 3,
    SRC_SNAPCAST  = 4,
};

enum Screen : uint8_t {
    SCR_BOOT   = 0,
    SCR_PLAYER = 1,
    SCR_SOURCE = 2,
    SCR_STATUS = 3,
};

// ─── Dirty flags — bitmask of what changed since last render ────────────────

namespace Dirty {
    constexpr uint32_t NONE      = 0;
    constexpr uint32_t STATE     = 1u << 0;
    constexpr uint32_t TITLE     = 1u << 1;
    constexpr uint32_t ARTIST    = 1u << 2;
    constexpr uint32_t SOURCE    = 1u << 3;
    constexpr uint32_t VOLUME    = 1u << 4;
    constexpr uint32_t PROGRESS  = 1u << 5;
    constexpr uint32_t ENERGY    = 1u << 6;
    constexpr uint32_t SPECTRUM  = 1u << 7;
    constexpr uint32_t CLOCK     = 1u << 8;
    constexpr uint32_t SYSTEM    = 1u << 9;
    constexpr uint32_t ACCENT    = 1u << 10;
    constexpr uint32_t ALL       = 0xFFFFFFFFu;
}

// ─── Track / playback state ─────────────────────────────────────────────────

struct App {
    // Playback
    PlayState state    = PLAY_STOPPED;
    Source    source   = SRC_NONE;
    String    title    = "";
    String    artist   = "";
    int       volume   = 35;                // 0..100
    uint32_t  pos_ms   = 0;
    uint32_t  dur_ms   = 1;                 // guard against div/0
    float     energy   = 0.0f;              // 0..1 from LV: field
    uint8_t   spectrum[16] = {};            // 0..100 from FX: field
    uint8_t   spectrum_bands = 0;           // number of FX bands received
    String    clockStr = "--:--";

    // Boot / connection
    bool      connected_to_pi = false;
    uint32_t  boot_progress = 0;            // 0..100 from BOOT: line

    // UI
    Screen    active_screen = SCR_BOOT;
    uint32_t  dirty = Dirty::ALL;

    // Timing
    uint32_t  last_touch_ms   = 0;
    uint32_t  last_audio_ms   = 0;
    uint32_t  last_status_rx  = 0;
};

// ─── System telemetry from SYS: line ────────────────────────────────────────

struct System {
    float cpu_temp_c   = 0.0f;
    String amp_stereo  = "---";
    String amp_sub     = "---";
    bool   dsp_active  = false;
    bool   svc_active  = false;        // go-librespot service running
    int    wifi_rssi   = 0;
    bool   gateway_ok  = true;         // bridge pings default gw — false = no route off Pi
    bool   spotify_stuck = false;      // bridge fired a go-librespot restart in last 60s
};

// ─── Weather (pushed by bridge every ~30 min via WX: serial line) ───────────

enum WeatherIcon : uint8_t {
    WX_CLEAR   = 0,
    WX_PARTLY  = 1,
    WX_CLOUDY  = 2,
    WX_FOG     = 3,
    WX_RAIN    = 4,
    WX_SNOW    = 5,
    WX_THUNDER = 6,
};

struct Weather {
    bool        valid  = false;     // false until first WX: received
    int         temp_c = 0;
    int         high_c = 0;
    int         low_c  = 0;
    WeatherIcon icon   = WX_PARTLY;
};

// ─── Globals ────────────────────────────────────────────────────────────────

extern App app;
extern System sys;
extern Weather weather;

inline void mark_dirty(uint32_t bits) { app.dirty |= bits; }
inline bool is_dirty(uint32_t bits)   { return (app.dirty & bits) != 0; }
inline void clear_dirty(uint32_t bits){ app.dirty &= ~bits; }

// ─── Mutators (use these, not direct assignment) ────────────────────────────

void set_play_state(PlayState s);
void set_source(Source src);
void set_title(const String &t);
void set_artist(const String &a);
void set_volume(int v);
void set_position(uint32_t pos_ms, uint32_t dur_ms);
void set_energy(float e);
void set_spectrum(const uint8_t *bands, uint8_t count);
void set_clock(const String &hhmm);

// Reset the dim-timer (last_touch_ms). Called on user touch AND on
// "significant" bridge events (new track, volume, play-state, source) so the
// display brightens up when something interesting happens — not only on
// physical interaction.
void wake_screen();

Source source_from_string(const char *s);
const char *source_to_string(Source s);

}  // namespace State
