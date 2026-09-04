// =============================================================================
// app/led_status.cpp — SK6812 / WS2812 status strip driver (SPI-encoded)
// =============================================================================
// Render behaviour ported from the RobinPi bring-up sketch (test_SK6812,
// env:bb-state), which was verified on hardware on 2026-09-03. Two things
// changed on the way in:
//
//   1. The wiring facts (pin, count, chip) now arrive as a LED: line from the
//      speaker profile instead of -DLED_DATA_PIN / -DLED_COUNT build flags —
//      one firmware image serves every speaker.
//
//   2. The bit-banger is our own, on SPI3, instead of Adafruit_NeoPixel.
//      ⚠️ MEASURED, NOT ASSUMED: Adafruit_NeoPixel pulls in the RMT driver on
//      the ESP32-S3 and costs ~43.7 KB of DRAM (beat-1 link: 289520 B → over
//      the 327680 B dram0_0_seg by 5544 B). This firmware already sits at
//      88.4 % DRAM before any strip, so the library does not fit in ANY env —
//      it is not a RobinPi-specific budget problem. The SPI encoder below
//      needs one DMA buffer of 4 bytes per LED byte (736 B for 46 RGBW
//      pixels) and no new driver: esp_lcd already links spi_master for the
//      panel on SPI2, and SPI3 is idle.
//
// Encoding: each strip bit becomes four SPI bits at 3.2 MHz (312.5 ns each).
//   0 → 1000  = 312 ns high, 938 ns low   (SK6812 T0H 300 ns ±150)
//   1 → 1100  = 625 ns high, 625 ns low   (SK6812 T1H 600 ns ±150)
// One strip byte = 4 SPI bytes, period 1.25 µs. MOSI idles low, and the
// ≥28 ms between frames is far past the 80 µs reset latch.
//
// Colours come from the runtime Theme palette, i.e. the same PAL: line the
// screens use. The strip is a second view of the same state, never its own
// colour scheme.
// =============================================================================
#include "led_status.h"

#ifdef ARDUINO

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "driver/spi_master.h"
#include "esp_heap_caps.h"

#include "pins.h"
#include "state.h"
#include "theme.h"

namespace LedStatus {

// ─── Live configuration (from LED:, see docs/protocol.md) ───────────────────
static int      cfg_pin        = -1;
static int      cfg_count      = 0;
static bool     cfg_rgbw       = true;
static uint8_t  cfg_brightness = 120;
static Mapping  cfg_mapping    = MAP_AREA;

// ─── SPI transport ──────────────────────────────────────────────────────────
static spi_device_handle_t spi_dev  = nullptr;
static uint8_t            *dma_buf  = nullptr;   // 4 SPI bytes per strip byte
static size_t              dma_len  = 0;
static bool                bus_up   = false;

static constexpr spi_host_device_t LED_SPI_HOST = SPI3_HOST;  // SPI2 = panel
static constexpr int      SPI_HZ     = 3200000;   // 4 SPI bits per strip bit
static constexpr uint32_t FRAME_MS   = 30;        // ~33 fps; one frame is
                                                  // ~1.8 ms of SPI for 46 RGBW
static constexpr uint32_t VOL_OVERLAY_MS = 1500;  // volume overlay dwell

// ─── Change tracking (self-contained; no extra fields in State::app) ────────
static int      last_volume    = -1;
static uint32_t vol_changed_ms = 0;
static uint32_t last_frame_ms  = 0;

// Perceptual ramp, built once. 256 B of DRAM against a visibly better low end:
// without it nearly all of a breathe/meter ramp happens in the first steps.
static uint8_t gamma_lut[256];
static bool    gamma_ready = false;

static void build_gamma()
{
    if (gamma_ready) return;
    for (int i = 0; i < 256; i++)
        gamma_lut[i] = (uint8_t)(powf(i / 255.0f, 2.6f) * 255.0f + 0.5f);
    gamma_ready = true;
}

// A pin the panel or the shared I2C bus already owns would take the display
// down with it — refuse those outright rather than trusting the YAML. The
// list is board-specific because pins.h is.
static bool pin_is_free(int pin)
{
    if (pin < 0 || pin > 48) return false;
    static const int taken[] = {
        LCD_CS, LCD_SCLK, LCD_SDIO0, LCD_SDIO1, LCD_SDIO2, LCD_SDIO3, LCD_RST,
        MAIN_I2C_SDA, MAIN_I2C_SCL, TOUCH_I2C_SDA, TOUCH_I2C_SCL,
        TOUCH_INT, TOUCH_RST,
#ifdef LCD_EN
        LCD_EN,
#endif
    };
    for (size_t i = 0; i < sizeof(taken) / sizeof(taken[0]); i++)
        if (taken[i] >= 0 && taken[i] == pin) return false;
    return true;
}

static void teardown()
{
    if (spi_dev) { spi_bus_remove_device(spi_dev); spi_dev = nullptr; }
    if (bus_up)  { spi_bus_free(LED_SPI_HOST);     bus_up  = false;  }
    if (dma_buf) { heap_caps_free(dma_buf);        dma_buf = nullptr; }
    dma_len   = 0;
    cfg_count = 0;
}

bool active() { return spi_dev != nullptr && cfg_count > 0; }

// ─── Configuration ──────────────────────────────────────────────────────────

bool configure(int pin, int count, bool rgbw, uint8_t brightness, Mapping mapping)
{
    if (count < 0 || count > 300) {
        Serial.printf("LED: rejected count=%d\n", count);
        return false;
    }

    cfg_mapping    = mapping;
    cfg_brightness = brightness;

    // count == 0 → this speaker has no strip. Release bus, pin and buffer.
    if (count == 0) {
        if (spi_dev || bus_up) {
            teardown();
            Serial.println("LED: disabled");
        }
        return true;
    }

    if (!pin_is_free(pin)) {
        Serial.printf("LED: rejected pin=%d (reserved by panel/I2C)\n", pin);
        return false;
    }

    // Idempotent: the bridge re-sends LED: on every reconnect, and rebuilding
    // the bus there would blink the strip on every USB hiccup.
    if (spi_dev && pin == cfg_pin && count == cfg_count && rgbw == cfg_rgbw)
        return true;

    teardown();
    build_gamma();

    const size_t bytes_per_led = rgbw ? 4 : 3;
    dma_len = count * bytes_per_led * 4;

    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num     = pin;
    buscfg.miso_io_num     = -1;
    buscfg.sclk_io_num     = -1;   // clock stays internal; the strip has none
    buscfg.quadwp_io_num   = -1;
    buscfg.quadhd_io_num   = -1;
    buscfg.max_transfer_sz = (int)dma_len;
    if (spi_bus_initialize(LED_SPI_HOST, &buscfg, SPI_DMA_CH_AUTO) != ESP_OK) {
        Serial.println("LED: spi_bus_initialize failed");
        dma_len = 0;
        return false;
    }
    bus_up = true;

    spi_device_interface_config_t devcfg = {};
    devcfg.clock_speed_hz = SPI_HZ;
    devcfg.mode           = 0;
    devcfg.spics_io_num   = -1;
    devcfg.queue_size     = 1;
    if (spi_bus_add_device(LED_SPI_HOST, &devcfg, &spi_dev) != ESP_OK) {
        Serial.println("LED: spi_bus_add_device failed");
        teardown();
        return false;
    }

    dma_buf = (uint8_t *)heap_caps_malloc(dma_len, MALLOC_CAP_DMA);
    if (!dma_buf) {
        Serial.printf("LED: DMA alloc of %u B failed\n", (unsigned)dma_len);
        teardown();
        return false;
    }
    memset(dma_buf, 0, dma_len);

    cfg_pin   = pin;
    cfg_count = count;
    cfg_rgbw  = rgbw;

    Serial.printf("LED: pin=%d n=%d %s bri=%u map=%s (SPI3, %u B DMA)\n",
                  cfg_pin, cfg_count, cfg_rgbw ? "RGBW" : "RGB", cfg_brightness,
                  cfg_mapping == MAP_AREA ? "area" : "mirror", (unsigned)dma_len);
    return true;
}

// ─── Pixel encoding ─────────────────────────────────────────────────────────

struct RGB { uint8_t r, g, b; };

static inline uint8_t clamp8(int x) { return x < 0 ? 0 : (x > 255 ? 255 : x); }

static inline RGB from_theme(lv_color_t c) { return { c.red, c.green, c.blue }; }

static inline RGB scale(RGB c, float f)
{
    return { clamp8((int)(c.r * f + 0.5f)),
             clamp8((int)(c.g * f + 0.5f)),
             clamp8((int)(c.b * f + 0.5f)) };
}

// Expand one strip byte into four SPI bytes (two strip bits per SPI byte).
static inline void encode_byte(uint8_t *out, uint8_t v)
{
    static const uint8_t nib[2] = {0x8, 0xC};   // 1000 = "0", 1100 = "1"
    for (int i = 0; i < 4; i++) {
        uint8_t hi = (v >> (7 - i * 2)) & 1;
        uint8_t lo = (v >> (6 - i * 2)) & 1;
        out[i] = (uint8_t)((nib[hi] << 4) | nib[lo]);
    }
}

// Gamma + global brightness cap, then GRB(W) order into the DMA buffer.
static inline void put(int i, RGB c)
{
    if (i < 0 || i >= cfg_count) return;
    const uint16_t b = cfg_brightness + 1;
    uint8_t g8 = (uint8_t)((gamma_lut[c.g] * b) >> 8);
    uint8_t r8 = (uint8_t)((gamma_lut[c.r] * b) >> 8);
    uint8_t b8 = (uint8_t)((gamma_lut[c.b] * b) >> 8);

    uint8_t *p = dma_buf + (size_t)i * (cfg_rgbw ? 16 : 12);
    encode_byte(p + 0, g8);
    encode_byte(p + 4, r8);
    encode_byte(p + 8, b8);
    if (cfg_rgbw) encode_byte(p + 12, 0);   // W stays dark: the accent is a
                                            // warm tint, white would wash it
}

static void flush()
{
    spi_transaction_t t = {};
    t.length    = dma_len * 8;
    t.tx_buffer = dma_buf;
    spi_device_transmit(spi_dev, &t);
}

// An amp channel reports "---" until the first SYS: line — that is "unknown",
// not "faulted". Only a value that is neither counts as a fault.
static bool amp_fault()
{
    auto bad = [](const String &s) { return s != "ok" && s != "---"; };
    return bad(State::sys.amp_stereo) || bad(State::sys.amp_sub);
}

// ─── Render ─────────────────────────────────────────────────────────────────
//
//   play     → energy (LV:) — brightness in MAP_AREA, meter length in MAP_MIRROR
//   pause    → accent, heavily dimmed
//   stop     → dark
//   standby  → slow breathe in accent (the speaker is alive, not asleep)
//   volume   → accent overlay for 1.5 s after a change
//   fault    → alert blink, overrides everything

static void render()
{
    const uint32_t now = millis();

    RGB accent = from_theme(Theme::accent);
    RGB glow   = from_theme(Theme::accent_glow);
    RGB dim    = from_theme(Theme::accent_dim);
    RGB alert  = from_theme(Theme::accent_alert);

    // Volume changes raise no dedicated event — watch the value itself.
    if (State::app.volume != last_volume) {
        if (last_volume >= 0) vol_changed_ms = now;
        last_volume = State::app.volume;
    }
    const bool vol_overlay = vol_changed_ms && (now - vol_changed_ms < VOL_OVERLAY_MS);

    // ── Fault: whole cluster blinks, nothing below matters ──
    if (amp_fault()) {
        RGB c = ((now / 250) % 2) ? alert : scale(alert, 0.15f);
        for (int i = 0; i < cfg_count; i++) put(i, c);
        flush();
        return;
    }

    const State::PlayState ps = State::app.state;
    const float energy = State::app.energy;

    if (cfg_mapping == MAP_AREA) {
        // One field of light. Behind a resin diffuser the cluster reads as a
        // single glowing spot, so position carries no information — level is
        // the only channel left, and colour separates the states.
        RGB c;
        if (vol_overlay) {
            c = scale(accent, 0.20f + 0.80f * (State::app.volume / 100.0f));
        } else if (ps == State::PLAY_PLAYING) {
            // Breathe gently around the signal level so quiet passages still
            // show life instead of going flat.
            float beat = 0.85f + 0.15f * sinf(now / 140.0f);
            c = scale(glow, (0.18f + 0.82f * energy) * beat);
        } else if (ps == State::PLAY_PAUSED) {
            c = scale(accent, 0.20f);
        } else if (ps == State::PLAY_STANDBY) {
            c = scale(accent, 0.12f + 0.43f * (0.5f + 0.5f * sinf(now / 1300.0f)));
        } else {
            c = {0, 0, 0};
        }
        for (int i = 0; i < cfg_count; i++) put(i, c);
        flush();
        return;
    }

    // ── MAP_MIRROR: symmetric centre→outside meter (two visible strips) ──
    const int half = cfg_count / 2;
    if (half < 1) { flush(); return; }

    for (int p = 0; p < half; p++) {
        RGB c;
        if (vol_overlay) {
            int lit = (int)((State::app.volume / 100.0f) * half + 0.5f);
            c = (p < lit) ? scale(accent, 0.9f) : scale(dim, 0.25f);
        } else if (ps == State::PLAY_PLAYING) {
            float beat = 0.85f + 0.15f * sinf(now / 140.0f);
            int lit = (int)(energy * half + 0.5f);
            c = (p < lit) ? scale(glow, beat) : scale(dim, 0.30f);
        } else if (ps == State::PLAY_PAUSED) {
            c = scale(accent, 0.20f);
        } else if (ps == State::PLAY_STANDBY) {
            c = scale(accent, 0.12f + 0.43f * (0.5f + 0.5f * sinf(now / 1300.0f)));
        } else {
            c = {0, 0, 0};
        }
        put(half + p, c);          // right half, centre → outside
        put(half - 1 - p, c);      // left half, mirrored
    }
    if (cfg_count & 1) put(cfg_count - 1, {0, 0, 0});   // odd pixel stays dark
    flush();
}

void tick()
{
    if (!active()) return;
    const uint32_t now = millis();
    if (now - last_frame_ms < FRAME_MS) return;
    last_frame_ms = now;
    render();
}

}  // namespace LedStatus

#endif  // ARDUINO
