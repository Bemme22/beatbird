// =============================================================================
// proto/serial_rx.cpp — Inbound serial parser
// =============================================================================
#include "proto.h"
#include "state.h"
#include "theme.h"
#include "screens/screen_standby.h"

#ifdef ARDUINO
  #include <Arduino.h>
#else
  #include "sim/arduino_shim.h"
#endif
#include <string.h>

namespace Proto {

// ─── Inbound dispatcher ─────────────────────────────────────────────────────

// 1024 instead of 640: IMG: cover chunks carry base64-encoded JPEG slices
// ~800 chars long ("IMG:NNN:<800-char-base64>"). Bridge ships 600 raw
// bytes per chunk → 800 base64 chars; rest is the header overhead.
static char line_buf[1024];
static size_t line_pos = 0;

void begin()
{
    Serial.begin(115200);
    line_pos = 0;
}

static void dispatch(const char *line)
{
    if (!line || !line[0]) return;
    handle_line(line);
}

void poll()
{
    while (Serial.available()) {
        int c = Serial.read();
        if (c < 0) break;
        if (c == '\n') {
            line_buf[line_pos] = '\0';
            // trim trailing CR
            if (line_pos > 0 && line_buf[line_pos - 1] == '\r') {
                line_buf[line_pos - 1] = '\0';
            }
            dispatch(line_buf);
            line_pos = 0;
            return;
        }
        if (line_pos < sizeof(line_buf) - 1) {
            line_buf[line_pos++] = (char)c;
        } else {
            // overflow — discard and resync on next newline
            line_pos = 0;
        }
    }
}

// ─── Verb dispatch ──────────────────────────────────────────────────────────

void handle_line(const char *line)
{
    if (!line || !line[0])              return;

    // Any inbound line from the Pi means the bridge is alive — clear the boot
    // screen even if we never receive a PAL: (e.g. ESP32 power-cycled after
    // the bridge already sent its one-shot palette on its own startup, so the
    // _palette_sent idempotency flag suppresses a re-send).
    State::app.connected_to_pi = true;

    // Cheap prefix-check, no String allocation
    if (!strncmp(line, "PAL:",  4))     { handle_palette_line(line + 4); return; }
    if (!strncmp(line, "SYS:",  4))     { handle_system_line(line);      return; }
    if (!strncmp(line, "BOOT:", 5))     { handle_boot_line(line + 5);    return; }
    if (!strncmp(line, "WX:",   3))     { handle_weather_line(line);     return; }
    if (!strncmp(line, "STBY:", 5))     { ScreenStandby::set_flap_text(line + 5); return; }
    if (!strncmp(line, "IMG:",  4))     { handle_cover_line(line + 4);   return; }
    if (!strncmp(line, "TIME:", 5))     {
        State::set_clock(String(line + 5));
        return;
    }
    if (!strncmp(line, "VOL:",   4) ||
        !strncmp(line, "STATE:", 6) ||
        !strncmp(line, "SOURCE:",7)) {
        handle_legacy_line(line);
        return;
    }
    if (line[0] == '[')                 return;   // [hb] from us, ignore

    // Otherwise: ST: state line (or unknown — same parser will quietly skip)
    handle_state_line(line);
}

// ─── Field parser ───────────────────────────────────────────────────────────

// Parse a `KEY<sep>value` pair from a `|`-delimited line. The state-line
// uses `:` (e.g. `ST:play|TI:song|AR:artist`), the weather-line uses `=`
// (e.g. `WX:t=17|c=1|h=23|l=11`) — they're parsed by the same engine with
// a separator override below.
static bool parse_field_sep(const char *line, const char *key, char sep,
                            char *out, size_t out_size)
{
    if (!line || !key || !out || out_size == 0) return false;
    size_t key_len = strlen(key);

    // Search for `KEY<sep>` preceded by `|` or at start-of-line
    const char *p = line;
    while ((p = strstr(p, key)) != nullptr) {
        bool boundary_ok = (p == line) || (*(p - 1) == '|');
        bool sep_ok      = (*(p + key_len) == sep);
        if (boundary_ok && sep_ok) {
            const char *start = p + key_len + 1;     // skip "KEY<sep>"
            const char *end   = strchr(start, '|');
            size_t len = end ? (size_t)(end - start) : strlen(start);
            if (len >= out_size) len = out_size - 1;
            memcpy(out, start, len);
            out[len] = '\0';
            return true;
        }
        p += key_len;
    }
    return false;
}

bool parse_field(const char *line, const char *key, char *out, size_t out_size)
{
    return parse_field_sep(line, key, ':', out, out_size);
}

static bool parse_field_eq(const char *line, const char *key, char *out, size_t out_size)
{
    return parse_field_sep(line, key, '=', out, out_size);
}

int parse_int_at(const char *line, size_t key_len)
{
    return atoi(line + key_len);
}

// ─── ST: state line ─────────────────────────────────────────────────────────

void handle_state_line(const char *line)
{
    char buf[256];

    if (parse_field(line, "ST", buf, sizeof(buf))) {
        if      (!strcmp(buf, "play"))          State::set_play_state(State::PLAY_PLAYING);
        else if (!strcmp(buf, "pause"))         State::set_play_state(State::PLAY_PAUSED);
        else if (!strcmp(buf, "stop"))          State::set_play_state(State::PLAY_STOPPED);
        else if (!strcmp(buf, "standby"))       State::set_play_state(State::PLAY_STANDBY);
        else if (!strcmp(buf, "shutdown_warn")) State::set_play_state(State::PLAY_SHUTDOWN_WARN);
        else if (!strcmp(buf, "shutdown"))      State::set_play_state(State::PLAY_SHUTDOWN);
    }

    if (parse_field(line, "TI", buf, sizeof(buf))) State::set_title(String(buf));
    if (parse_field(line, "AR", buf, sizeof(buf))) State::set_artist(String(buf));
    if (parse_field(line, "SO", buf, sizeof(buf))) {
        State::set_source(State::source_from_string(buf));
    }
    if (parse_field(line, "VO", buf, sizeof(buf))) State::set_volume(atoi(buf));

    uint32_t pos = 0, dur = 1;
    bool pos_changed = false;
    if (parse_field(line, "PO", buf, sizeof(buf))) { pos = (uint32_t)atol(buf); pos_changed = true; }
    if (parse_field(line, "DU", buf, sizeof(buf))) { dur = max(1L, atol(buf));  pos_changed = true; }
    if (pos_changed) State::set_position(pos, dur);

    if (parse_field(line, "LV", buf, sizeof(buf))) {
        float e = atof(buf) / 100.0f;
        if (e < 0.0f) e = 0.0f;
        if (e > 1.0f) e = 1.0f;
        State::set_energy(e);
    }

    if (parse_field(line, "TM", buf, sizeof(buf))) State::set_clock(String(buf));

    // FX:n,n,n,...  — 0..100 per band, up to 16 bands
    if (parse_field(line, "FX", buf, sizeof(buf))) {
        uint8_t bands[16];
        uint8_t count = 0;
        char *tok = strtok(buf, ",");
        while (tok && count < 16) {
            int v = atoi(tok);
            if (v < 0) v = 0;
            if (v > 100) v = 100;
            bands[count++] = (uint8_t)v;
            tok = strtok(nullptr, ",");
        }
        if (count > 0) State::set_spectrum(bands, count);
    }
}

// ─── SYS: system status line ────────────────────────────────────────────────

void handle_system_line(const char *line)
{
    char buf[32];

    // SYS: subfields are key=value pairs (cp=21.5|sv=1|wi=-67…), NOT
    // key:value. The bridge has always sent them this way; the parser
    // was historically calling parse_field (which expects ':') and
    // silently returning false on every field — so every SYS field has
    // been stuck at its compile-time default since this file was written.
    // Symptom: WIFI WEAK never fired (rssi stayed 0), SPOTIFY OFFLINE
    // permanently latched at boot once defaults changed. Same fix WX:
    // already had.
    //
    // Also strip the "SYS:" prefix — parse_field_eq's boundary check needs
    // each key to be at start-of-line or right after '|'. With the prefix
    // kept, the first field is preceded by ':' and gets rejected.
    if (!strncmp(line, "SYS:", 4)) line += 4;

    if (parse_field_eq(line, "cp", buf, sizeof(buf)))      State::sys.cpu_temp_c = atof(buf);
    if (parse_field_eq(line, "ht", buf, sizeof(buf)))      State::sys.amp_stereo = String(buf);
    if (parse_field_eq(line, "hstereo", buf, sizeof(buf))) State::sys.amp_stereo = String(buf);
    if (parse_field_eq(line, "hs", buf, sizeof(buf)))      State::sys.amp_sub    = String(buf);
    if (parse_field_eq(line, "hsub", buf, sizeof(buf)))    State::sys.amp_sub    = String(buf);
    if (parse_field_eq(line, "ds", buf, sizeof(buf)))      State::sys.dsp_active = (buf[0] == '1');
    if (parse_field_eq(line, "sv", buf, sizeof(buf)))      State::sys.svc_active = (buf[0] == '1');
    if (parse_field_eq(line, "wi", buf, sizeof(buf)))      State::sys.wifi_rssi  = atoi(buf);
    if (parse_field_eq(line, "gw", buf, sizeof(buf)))      State::sys.gateway_ok = (buf[0] == '1');
    if (parse_field_eq(line, "ss", buf, sizeof(buf)))      State::sys.spotify_stuck = (buf[0] == '1');

    State::app.last_status_rx = millis();
    State::mark_dirty(State::Dirty::SYSTEM);
}

// ─── WX: weather line ───────────────────────────────────────────────────────
//
// Format: WX:t=<int>|c=<icon>|h=<int>|l=<int>
//   t = current temperature in °C (rounded)
//   c = WeatherIcon enum value (0=clear .. 6=thunder)
//   h = today's high in °C
//   l = today's low  in °C
//
// Any subset of fields may be present; missing fields keep their last
// known value. Receiving any valid WX: line flips State::weather.valid
// to true, which the standby screen uses as gate for showing the
// weather block (graceful degrade if no WX: ever received).

void handle_weather_line(const char *line)
{
    char buf[16];
    bool any = false;

    // Strip the "WX:" prefix — parse_field_eq's boundary check expects each
    // key to be at start-of-line or right after '|'. With the prefix kept,
    // the first field (`t=…`) is preceded by `:` and gets rejected.
    if (!strncmp(line, "WX:", 3)) line += 3;

    // WX: uses `=` between key and value (per docs/protocol.md), unlike the
    // state-line's `:`. parse_field_eq picks the right separator.
    if (parse_field_eq(line, "t", buf, sizeof(buf))) { State::weather.temp_c = atoi(buf); any = true; }
    if (parse_field_eq(line, "c", buf, sizeof(buf))) {
        int v = atoi(buf);
        if (v >= 0 && v <= 6) {
            State::weather.icon = (State::WeatherIcon)v;
            any = true;
        }
    }
    if (parse_field_eq(line, "h", buf, sizeof(buf))) { State::weather.high_c = atoi(buf); any = true; }
    if (parse_field_eq(line, "l", buf, sizeof(buf))) { State::weather.low_c  = atoi(buf); any = true; }

    if (any) {
        State::weather.valid = true;
    }
}

// ─── PAL: palette from Pi ───────────────────────────────────────────────────
//
// Two accepted forms:
//   1. Legacy single-accent:    PAL:2D6A4F        (or PAL:#2D6A4F)
//   2. Extended palette:        PAL:a=2D6A4F|g=52B788|d=1B4332|p=F4EFE0|s=A89E89|e=C73E2C
//
// Keys (each optional): a=accent  g=accent_glow  d=accent_dim  p=text_primary
//                       s=text_secondary  e=accent_alert
// The new form is detected by spotting '=' in the body. Missing slots keep
// their previous value (or the compile-time default at boot).

void handle_palette_line(const char *body)
{
    if (!body) return;
    if (body[0] == '#') body++;

    // Heuristic: if the body contains '=', treat as the new key=value form.
    // Otherwise it's the legacy 6-hex single-accent shortcut.
    if (!strchr(body, '=')) {
        if (Theme::set_accent_hex(body)) {
            State::app.connected_to_pi = true;
        }
        return;
    }

    // key=value form. Iterate over the 6 known slots; missing ones are
    // silently skipped so a bridge can push a partial palette if it wants.
    char buf[8];
    static const char SLOTS[] = "agdpse";
    bool any = false;
    for (size_t i = 0; SLOTS[i]; i++) {
        const char k[2] = { SLOTS[i], 0 };
        if (parse_field_eq(body, k, buf, sizeof(buf))) {
            if (Theme::set_palette_slot_hex(SLOTS[i], buf)) any = true;
        }
    }
    if (any) State::app.connected_to_pi = true;
}

// ─── BOOT: progress line ────────────────────────────────────────────────────

void handle_boot_line(const char *body)
{
    // Format: stage|progress  e.g. "wifi|45"
    const char *pipe = strchr(body, '|');
    int progress = pipe ? atoi(pipe + 1) : 0;
    State::app.boot_progress = progress;
    State::mark_dirty(State::Dirty::SYSTEM);
}

// ─── IMG: album-cover chunked binary transport ──────────────────────────────
// Pi sends one cover as:
//     IMG:start|size=NNNN
//     IMG:0:<base64 of first ~600 bytes>
//     IMG:1:<base64 of next 600 bytes>
//     ...
//     IMG:end
// Chunks decode straight into a heap buffer (PSRAM on ESP32, plain heap on
// sim). On `end` we mark Dirty::COVER so screen_player.cpp can swap the
// background image source. The JPEG itself is decoded by LVGL's tjpgd.

static uint8_t *g_cover_buf      = nullptr;
static size_t   g_cover_buf_cap  = 0;
static size_t   g_cover_buf_len  = 0;
static size_t   g_cover_expected = 0;
static bool     g_cover_collecting = false;

static void cover_buf_ensure(size_t needed)
{
    if (g_cover_buf_cap >= needed) return;
    if (g_cover_buf) free(g_cover_buf);
    g_cover_buf = nullptr;
#ifdef ARDUINO
    g_cover_buf = (uint8_t *)ps_malloc(needed);  // PSRAM preferred
#endif
    if (!g_cover_buf) g_cover_buf = (uint8_t *)malloc(needed);
    g_cover_buf_cap = g_cover_buf ? needed : 0;
}

static int b64_val(int c)
{
    if (c >= 'A' && c <= 'Z') return c - 'A';
    if (c >= 'a' && c <= 'z') return c - 'a' + 26;
    if (c >= '0' && c <= '9') return c - '0' + 52;
    if (c == '+') return 62;
    if (c == '/') return 63;
    return -1;
}

static size_t b64_decode(const char *src, size_t src_len, uint8_t *dst, size_t dst_max)
{
    size_t out = 0;
    int bits = 0, hold = 0;
    for (size_t i = 0; i < src_len; i++) {
        int v = b64_val(src[i]);
        if (v < 0) continue;  // skip whitespace + padding
        hold = (hold << 6) | v;
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            if (out < dst_max) dst[out++] = (uint8_t)((hold >> bits) & 0xFF);
        }
    }
    return out;
}

void handle_cover_line(const char *body)
{
    if (!strncmp(body, "start", 5)) {
        const char *sz = strstr(body, "size=");
        g_cover_expected = sz ? (size_t)atoi(sz + 5) : 0;
        if (g_cover_expected == 0 || g_cover_expected > 256 * 1024) {
            // Defensive cap — a 256 KB cover would already be huge for our use.
            g_cover_collecting = false;
            return;
        }
        cover_buf_ensure(g_cover_expected + 256);  // small margin
        g_cover_buf_len    = 0;
        g_cover_collecting = (g_cover_buf != nullptr);
        return;
    }
    if (!strncmp(body, "end", 3)) {
        if (g_cover_collecting && g_cover_buf_len > 0) {
            State::mark_dirty(State::Dirty::COVER);
        }
        g_cover_collecting = false;
        return;
    }
    if (!g_cover_collecting || !g_cover_buf) return;
    // Chunk: "<seq>:<base64>"
    const char *colon = strchr(body, ':');
    if (!colon) return;
    const char *b64 = colon + 1;
    size_t b64_len = strlen(b64);
    size_t remaining = g_cover_buf_cap - g_cover_buf_len;
    g_cover_buf_len += b64_decode(b64, b64_len, g_cover_buf + g_cover_buf_len, remaining);
}

// ─── Cover accessors for screen_player.cpp ──────────────────────────────────

const uint8_t *cover_data() { return g_cover_buf; }
size_t         cover_size() { return g_cover_buf_len; }

// ─── Legacy single-shot lines ───────────────────────────────────────────────

void handle_legacy_line(const char *line)
{
    if (!strncmp(line, "VOL:", 4)) {
        int v = atoi(line + 4);
        State::set_volume(v);
        return;
    }
    if (!strncmp(line, "STATE:", 6)) {
        const char *s = line + 6;
        if      (!strcasecmp(s, "PLAY"))           State::set_play_state(State::PLAY_PLAYING);
        else if (!strcasecmp(s, "PAUSE"))          State::set_play_state(State::PLAY_PAUSED);
        else if (!strcasecmp(s, "STOP"))           State::set_play_state(State::PLAY_STOPPED);
        else if (!strcasecmp(s, "STANDBY"))        State::set_play_state(State::PLAY_STANDBY);
        else if (!strcasecmp(s, "SHUTDOWN_WARN"))  State::set_play_state(State::PLAY_SHUTDOWN_WARN);
        else if (!strcasecmp(s, "SHUTDOWN"))       State::set_play_state(State::PLAY_SHUTDOWN);
        return;
    }
    if (!strncmp(line, "SOURCE:", 7)) {
        State::set_source(State::source_from_string(line + 7));
        return;
    }
}

}  // namespace Proto
