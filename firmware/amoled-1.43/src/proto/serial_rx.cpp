// =============================================================================
// proto/serial_rx.cpp — Inbound serial parser
// =============================================================================
#include "proto.h"
#include "state.h"
#include "theme.h"

#include <Arduino.h>
#include <string.h>

namespace Proto {

// ─── Inbound dispatcher ─────────────────────────────────────────────────────

static char line_buf[640];   // generous — FX can carry 16 comma-separated values
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

    // Cheap prefix-check, no String allocation
    if (!strncmp(line, "PAL:",  4))     { handle_palette_line(line + 4); return; }
    if (!strncmp(line, "SYS:",  4))     { handle_system_line(line);      return; }
    if (!strncmp(line, "BOOT:", 5))     { handle_boot_line(line + 5);    return; }
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

bool parse_field(const char *line, const char *key, char *out, size_t out_size)
{
    if (!line || !key || !out || out_size == 0) return false;
    size_t key_len = strlen(key);

    // Search for `KEY:` preceded by `|` or at start-of-line
    const char *p = line;
    while ((p = strstr(p, key)) != nullptr) {
        bool boundary_ok = (p == line) || (*(p - 1) == '|');
        bool colon_ok    = (*(p + key_len) == ':');
        if (boundary_ok && colon_ok) {
            const char *start = p + key_len + 1;     // skip "KEY:"
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

    if (parse_field(line, "cp", buf, sizeof(buf)))      State::sys.cpu_temp_c = atof(buf);
    if (parse_field(line, "ht", buf, sizeof(buf)))      State::sys.amp_stereo = String(buf);
    if (parse_field(line, "hstereo", buf, sizeof(buf))) State::sys.amp_stereo = String(buf);
    if (parse_field(line, "hs", buf, sizeof(buf)))      State::sys.amp_sub    = String(buf);
    if (parse_field(line, "hsub", buf, sizeof(buf)))    State::sys.amp_sub    = String(buf);
    if (parse_field(line, "ds", buf, sizeof(buf)))      State::sys.dsp_active = (buf[0] == '1');
    if (parse_field(line, "sv", buf, sizeof(buf)))      State::sys.svc_active = (buf[0] == '1');
    if (parse_field(line, "wi", buf, sizeof(buf)))      State::sys.wifi_rssi  = atoi(buf);

    State::app.last_status_rx = millis();
    State::mark_dirty(State::Dirty::SYSTEM);
}

// ─── PAL: accent colour from Pi ─────────────────────────────────────────────

void handle_palette_line(const char *hex)
{
    if (!hex) return;
    // Skip optional leading '#'
    if (hex[0] == '#') hex++;

    if (Theme::set_accent_hex(hex)) {
        State::app.connected_to_pi = true;
        State::mark_dirty(State::Dirty::ACCENT | State::Dirty::ALL);
    }
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
