// =============================================================================
// proto.h — BeatBird Serial Protocol
// =============================================================================
// Wraps the pipe-delimited ASCII protocol between Pi and ESP32. Replaces the
// indexOf-based parser in the old main.cpp with a single-pass state-machine
// that minimises String allocations on the hot path.
//
// Protocol summary (full details in docs/protocol.md):
//
//   Pi → ESP32:
//     ST:play|TI:…|AR:…|SO:…|VO:..|PO:..|DU:..|LV:..|TM:..|FX:..,..
//     SYS:cp=..|ht=ok|hs=ok|ds=1|sv=1|wi=-58
//     PAL:F0CB7B                        ← accent colour from profile
//     BOOT:stage|progress
//     SOURCE:spotify | STATE:PLAY | VOL:45     (single-shot legacy)
//
//   ESP32 → Pi:
//     CMD:PLAYPAUSE | CMD:NEXT | CMD:PREV | CMD:STOP | CMD:SOURCE:bluetooth
//     VOL:0-100
//     TEMP:22.5
//     [hb] ...
// =============================================================================
#pragma once

#include <Arduino.h>

namespace Proto {

// ─── Initialization ─────────────────────────────────────────────────────────

void begin();

/** Poll Serial in the main loop. Reads at most one line per call and
 *  dispatches it to the right parser. Updates State::app via the setters. */
void poll();

// ─── Outbound (ESP32 → Pi) ──────────────────────────────────────────────────

void send_volume(int v);
void send_command(const char *cmd);              // "PLAYPAUSE", "NEXT", …
void send_source_request(const char *src);       // "spotify", "bluetooth", …
void send_temperature(float celsius);
void send_heartbeat();                            // periodic, every 10 s
void send_boot_marker();                          // once, at setup() end

// ─── Inbound parsers (visible for unit-testing) ─────────────────────────────

void handle_line(const char *line);
void handle_state_line(const char *line);         // ST:...
void handle_system_line(const char *line);        // SYS:...
void handle_palette_line(const char *line);       // PAL:rrggbb
void handle_boot_line(const char *line);          // BOOT:stage|progress
void handle_weather_line(const char *line);       // WX:t=...|c=...|h=...|l=...
void handle_legacy_line(const char *line);        // VOL: / STATE: / SOURCE:

// ─── Utility ────────────────────────────────────────────────────────────────

/** Extract a value for a pipe-separated key:value field from a line.
 *  Returns true on hit; *out points to a freshly allocated null-terminated
 *  buffer (caller must free). Returns false (and leaves *out untouched) on
 *  miss. The match requires the field to be preceded by '|' or start-of-line
 *  AND followed by ':' — this prevents "TI:" matching "SETIING:" etc. */
bool parse_field(const char *line, const char *key, char *out, size_t out_size);

/** Parse a single integer value out of a `KEY:value` token at known offset. */
int  parse_int_at(const char *line, size_t key_len);

}  // namespace Proto
