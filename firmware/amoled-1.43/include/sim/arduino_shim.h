// =============================================================================
// sim/arduino_shim.h — Portable stubs that let firmware UI code compile native
// =============================================================================
// Replaces <Arduino.h> + <esp_system.h> when building the desktop simulator.
// Provides just enough of the Arduino-ESP32 surface that the bridge ↔ display
// protocol, screen state, and LVGL UI code can link without the real chip.
//
// Anything ESP32-hardware-specific (display panel init, touch I²C, IMU)
// lives in `src/sh8601/` and `src/main.cpp` which are NOT compiled in the
// sim env — see the `src_filter` in platformio.ini's [env:sim].
//
// IMPORTANT: only ASCII-clean / std-library-portable APIs go here. If a new
// firmware file starts using something like ESP.deepSleep(), add a stub
// here rather than #ifdef'ing it at every call site.
// =============================================================================
#pragma once

#include <chrono>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>

// ─── Arduino String → std::string ────────────────────────────────────────────
// All firmware uses just .c_str() / .length() / operator==(c-string) / += /
// String(const char*) — verified by grep across src/. If a future file adds
// Arduino-specific methods (.toInt(), .indexOf(), …), either rewrite the
// caller in portable C++ or wrap std::string in a tiny String class here.
using String = std::string;

// ─── Time + sleep ────────────────────────────────────────────────────────────

inline uint32_t millis()
{
    static const auto t0 = std::chrono::steady_clock::now();
    auto now = std::chrono::steady_clock::now();
    return (uint32_t)std::chrono::duration_cast<std::chrono::milliseconds>(now - t0).count();
}

inline uint32_t micros()
{
    static const auto t0 = std::chrono::steady_clock::now();
    auto now = std::chrono::steady_clock::now();
    return (uint32_t)std::chrono::duration_cast<std::chrono::microseconds>(now - t0).count();
}

inline void delay(uint32_t ms)
{
    std::this_thread::sleep_for(std::chrono::milliseconds(ms));
}

inline void delayMicroseconds(uint32_t us)
{
    std::this_thread::sleep_for(std::chrono::microseconds(us));
}

// ─── Arduino random(min, max) — half-open interval, matches Arduino's API ────

inline long random(long max)              { return rand() % (max > 0 ? max : 1); }
inline long random(long min, long max)    { return min + (rand() % ((max - min) > 0 ? (max - min) : 1)); }

// ─── Minimal Serial stand-in (stdout) ────────────────────────────────────────
// Just enough for our protocol senders (printf / println) and any debug
// prints. read() / available() always say "nothing here" — the sim feeds
// protocol input via a different channel (see src/sim/sim_main.cpp).

class HardwareSerialShim {
public:
    void begin(uint32_t /*baud*/) {}
    operator bool() const { return true; }

    int printf(const char *fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        int n = vprintf(fmt, ap);
        va_end(ap);
        fflush(stdout);
        return n;
    }

    void print(const char *s)              { fputs(s, stdout); fflush(stdout); }
    void print(int v)                      { printf("%d", v); }
    void println()                         { putchar('\n'); fflush(stdout); }
    void println(const char *s)            { puts(s); fflush(stdout); }
    void println(int v)                    { printf("%d\n", v); }
    void println(const std::string &s)     { puts(s.c_str()); fflush(stdout); }

    int  read()                            { return -1; }
    int  available()                       { return 0; }
    void flush()                           { fflush(stdout); }
};

extern HardwareSerialShim Serial;

// ─── ESP.getFreeHeap() etc. — used by the heartbeat logger ───────────────────

struct EspShim {
    uint32_t getFreeHeap()  const { return 8u * 1024u * 1024u; }   // fake 8 MB
    uint32_t getMinFreeHeap() const { return 4u * 1024u * 1024u; }
    uint32_t getMaxAllocHeap() const { return 2u * 1024u * 1024u; }
    void     restart()             { exit(0); }
};

extern EspShim ESP;

// ─── ESP-LOG macros that touch our UI/protocol code → printf passthrough ─────
// The bigger ESP-LOG framework is only referenced from main.cpp / sh8601/
// (both excluded from the sim build), but if a UI file ever picks one up,
// silence it cleanly here instead of hitting an undefined-symbol link error.

#ifndef ESP_LOGI
#define ESP_LOGI(tag, fmt, ...) printf("[I][" tag "] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef ESP_LOGW
#define ESP_LOGW(tag, fmt, ...) printf("[W][" tag "] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef ESP_LOGE
#define ESP_LOGE(tag, fmt, ...) fprintf(stderr, "[E][" tag "] " fmt "\n", ##__VA_ARGS__)
#endif
#ifndef ESP_LOGD
#define ESP_LOGD(tag, fmt, ...) ((void)0)
#endif
