// =============================================================================
// proto/serial_tx.cpp — Outbound serial sender
// =============================================================================
#include "proto.h"

#include <Arduino.h>
#include <esp_system.h>

namespace Proto {

static uint32_t last_hb_ms = 0;

void send_volume(int v)
{
    if (v < 0)   v = 0;
    if (v > 100) v = 100;
    Serial.printf("VOL:%d\n", v);
}

void send_command(const char *cmd)
{
    if (!cmd || !cmd[0]) return;
    Serial.printf("CMD:%s\n", cmd);
}

void send_source_request(const char *src)
{
    if (!src || !src[0]) return;
    Serial.printf("CMD:SOURCE:%s\n", src);
}

void send_temperature(float celsius)
{
    Serial.printf("TEMP:%.1f\n", celsius);
}

void send_heartbeat()
{
    uint32_t now = millis();
    if (now - last_hb_ms < 10000) return;
    last_hb_ms = now;
    Serial.printf("[hb] t=%u heap=%u\n", (unsigned)now, (unsigned)ESP.getFreeHeap());
}

void send_boot_marker()
{
    // Emitted once at startup so the bridge can detect an ESP32 reboot
    // mid-session and re-send its one-shot PAL: palette. Without this the
    // bridge's `_palette_sent` idempotency flag would suppress the re-send
    // and the firmware would render with Theme::Color::ACCENT_DEFAULT until
    // the next bridge restart.
    Serial.println("[boot]");
}

void send_version()
{
#ifdef BEATBIRD_FW_VERSION
    Serial.printf("FW:%s\n", BEATBIRD_FW_VERSION);
#else
    Serial.println("FW:unknown");
#endif
}

}  // namespace Proto
