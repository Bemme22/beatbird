// =============================================================================
// app/state.cpp — App state setters with dirty-flag bookkeeping
// =============================================================================
#include "state.h"

#include <string.h>

namespace State {

App app;
System sys;

// ─── Setters ────────────────────────────────────────────────────────────────

void wake_screen()
{
    app.last_touch_ms = millis();
}

void set_play_state(PlayState s)
{
    if (app.state == s) return;
    app.state = s;
    if (s == PLAY_PLAYING) app.last_audio_ms = millis();
    mark_dirty(Dirty::STATE);
    wake_screen();
}

void set_source(Source src)
{
    if (app.source == src) return;
    app.source = src;
    mark_dirty(Dirty::SOURCE);
    wake_screen();
}

void set_title(const String &t)
{
    if (app.title == t) return;
    app.title = t;
    mark_dirty(Dirty::TITLE);
    wake_screen();
}

void set_artist(const String &a)
{
    if (app.artist == a) return;
    app.artist = a;
    mark_dirty(Dirty::ARTIST);
    // Note: no wake_screen() here — artist change typically follows a title
    // change in the same state push and wake_screen() already fired.
}

void set_volume(int v)
{
    if (v < 0)   v = 0;
    if (v > 100) v = 100;
    if (app.volume == v) return;
    app.volume = v;
    mark_dirty(Dirty::VOLUME);
    wake_screen();
}

void set_position(uint32_t pos_ms, uint32_t dur_ms)
{
    bool changed = false;
    if (app.pos_ms != pos_ms) { app.pos_ms = pos_ms; changed = true; }
    if (app.dur_ms != dur_ms) { app.dur_ms = dur_ms; changed = true; }
    if (changed) mark_dirty(Dirty::PROGRESS);
}

void set_energy(float e)
{
    if (e < 0.0f) e = 0.0f;
    if (e > 1.0f) e = 1.0f;
    // Energy changes constantly — set unconditionally, screens decide when
    // to redraw based on their own thresholds.
    app.energy = e;
    mark_dirty(Dirty::ENERGY);
}

void set_spectrum(const uint8_t *bands, uint8_t count)
{
    if (!bands || count == 0) return;
    if (count > 16) count = 16;
    bool changed = (count != app.spectrum_bands);
    for (uint8_t i = 0; i < count; i++) {
        if (app.spectrum[i] != bands[i]) {
            app.spectrum[i] = bands[i];
            changed = true;
        }
    }
    app.spectrum_bands = count;
    if (changed) mark_dirty(Dirty::SPECTRUM);
}

void set_clock(const String &hhmm)
{
    if (app.clockStr == hhmm) return;
    app.clockStr = hhmm;
    mark_dirty(Dirty::CLOCK);
}

// ─── Source string ⇄ enum ──────────────────────────────────────────────────

Source source_from_string(const char *s)
{
    if (!s) return SRC_NONE;
    if (!strcasecmp(s, "spotify"))   return SRC_SPOTIFY;
    if (!strcasecmp(s, "bluetooth")) return SRC_BLUETOOTH;
    if (!strcasecmp(s, "bt"))        return SRC_BLUETOOTH;
    if (!strcasecmp(s, "toslink"))   return SRC_TOSLINK;
    if (!strcasecmp(s, "snapcast"))  return SRC_SNAPCAST;
    return SRC_NONE;
}

const char *source_to_string(Source s)
{
    switch (s) {
        case SRC_SPOTIFY:   return "spotify";
        case SRC_BLUETOOTH: return "bluetooth";
        case SRC_TOSLINK:   return "toslink";
        case SRC_SNAPCAST:  return "snapcast";
        case SRC_NONE:
        default:            return "none";
    }
}

}  // namespace State
