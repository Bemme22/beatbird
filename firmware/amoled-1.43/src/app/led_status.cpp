// =============================================================================
// app/led_status.cpp — SK6812 / WS2812 status strip (SPI-encoded)
// =============================================================================
// Wiring facts (pin, count, chip, brightness, mapping, chain join) arrive as a
// LED: line from the speaker profile — see docs/protocol.md and led_status.h
// for why they are not build flags.
//
// ⚠️ MEASURED, NOT ASSUMED: Adafruit_NeoPixel pulls in the RMT driver on the
// ESP32-S3 and costs ~43.7 KB of DRAM. This firmware already sits at 88.4 % of
// dram0_0_seg before any strip, so the library overflows the region in EVERY
// env (beat-1: over by 5544 B). We encode the waveform ourselves onto SPI3
// instead: four SPI bits per strip bit at 3.2 MHz —
//   0 → 1000  = 312 ns high, 938 ns low   (SK6812 T0H 300 ns ±150)
//   1 → 1100  = 625 ns high, 625 ns low   (SK6812 T1H 600 ns ±150)
// One strip byte = 4 SPI bytes, period 1.25 µs. spi_master is already linked
// for the panel on SPI2 and SPI3 was idle, so this costs no new driver: 304 B
// of DRAM plus 4 bytes of DMA buffer per strip byte.
//
// Colours come from the runtime Theme palette, i.e. the same PAL: line the
// screens use. The strip is a second view of the same state, never its own
// colour scheme.
//
// ⚠️⚠️ DURABLE — GAMMA BELONGS ON THE LEVEL, NOT ON EACH CHANNEL.
// A colour is defined by the RATIO of its channels. The first version dimmed
// to 8 bit, ran each dimmed channel through a gamma LUT, then multiplied by
// the brightness cap: three quantisations, after which the channels hit zero
// at different levels. For accent F0CB7B the ratio 240:203:123 came out as
// 17:11:3 at half level and 4:2:0 at a third — so the breathe visibly drifted
// red → yellow → gold instead of staying one warm tone (observed 04.09.2026).
// Now the perceptual curve is applied ONCE to the scalar level and the channel
// ratio is untouched; below level_floor() the 8-bit channels can no longer
// carry the hue at all, so the animation range is mapped above that floor.
// =============================================================================
#include "led_status.h"

#ifdef ARDUINO

#include <Arduino.h>
#include <math.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"

#include "pins.h"
#include "state.h"
#include "theme.h"

namespace LedStatus {

// ─── Live configuration (from LED:, see docs/protocol.md) ───────────────────
static int       cfg_pin        = -1;
static int       cfg_count      = 0;
static bool      cfg_rgbw       = true;
static uint8_t   cfg_brightness = 120;
static Mapping   cfg_mapping    = MAP_AREA;
static ChainJoin cfg_join       = JOIN_INNER;

// ─── SPI transport ──────────────────────────────────────────────────────────
static spi_device_handle_t spi_dev = nullptr;
static uint8_t            *dma_buf = nullptr;   // 4 SPI bytes per strip byte
static size_t              dma_len = 0;
static bool                bus_up  = false;

static SemaphoreHandle_t   cfg_lock  = nullptr;  // configure() vs render task
static TaskHandle_t        led_task  = nullptr;

static constexpr spi_host_device_t LED_SPI_HOST = SPI3_HOST;  // SPI2 = panel
static constexpr int      SPI_HZ    = 3200000;   // 4 SPI bits per strip bit
static constexpr uint32_t FRAME_MS  = 20;        // 50 fps; ~1.8 ms SPI per
                                                 // frame for 46 RGBW pixels
static constexpr uint32_t VOL_OVERLAY_MS = 1500; // volume overlay dwell
static constexpr float    GAMMA     = 2.6f;

// ─── Change tracking (self-contained; no extra fields in State::app) ────────
static int      last_volume    = -1;
static uint32_t vol_changed_ms = 0;

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

// ─── Colour helpers ─────────────────────────────────────────────────────────

struct RGB { uint8_t r, g, b; };

static inline uint8_t clamp8(int x) { return x < 0 ? 0 : (x > 255 ? 255 : x); }

static inline RGB from_theme(lv_color_t c) { return { c.red, c.green, c.blue }; }

/** Lowest level at which 8 bits still carry this colour: the smallest non-zero
 *  channel must survive as >= 2 after the brightness cap. Below it the channels
 *  quantise to 0/1 one after another and the hue falls apart — that is the
 *  red→yellow→gold drift, so we never render there. */
static float level_floor(RGB c)
{
    int lo = 256;
    if (c.r && c.r < lo) lo = c.r;
    if (c.g && c.g < lo) lo = c.g;
    if (c.b && c.b < lo) lo = c.b;
    if (lo == 256) return 0.0f;                       // colour is pure black
    const float top = lo * (cfg_brightness / 255.0f);
    if (top <= 2.0f) return 1.0f;                     // cap too low for nuance
    return powf(2.0f / top, 1.0f / GAMMA);
}

/** Map an animation value 0..1 into [level_floor, 1]. Use for anything meant to
 *  be LIT; pass level 0 straight to put() for anything meant to be dark. */
static inline float lit(RGB base, float x)
{
    if (x <= 0.0f) return 0.0f;
    if (x >  1.0f) x = 1.0f;
    const float f = level_floor(base);
    return f + (1.0f - f) * x;
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

/** Write one pixel: base colour at `level` (0 = off, 1 = full). The perceptual
 *  curve is applied to the level ONCE, so the channel ratio — and with it the
 *  hue — survives every dimming step. GRB(W) order, W left dark because the
 *  accent is a warm tint that a white LED would wash out. */
static inline void put(int i, RGB base, float level)
{
    if (i < 0 || i >= cfg_count) return;
    uint8_t r = 0, g = 0, b = 0;
    if (level > 0.0f) {
        if (level > 1.0f) level = 1.0f;
        const float lin = powf(level, GAMMA) * (cfg_brightness / 255.0f);
        r = clamp8((int)(base.r * lin + 0.5f));
        g = clamp8((int)(base.g * lin + 0.5f));
        b = clamp8((int)(base.b * lin + 0.5f));
    }
    uint8_t *p = dma_buf + (size_t)i * (cfg_rgbw ? 16 : 12);
    encode_byte(p + 0, g);
    encode_byte(p + 4, r);
    encode_byte(p + 8, b);
    if (cfg_rgbw) encode_byte(p + 12, 0);
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

// Index of the p-th pixel out from the centre on each half. JOIN_INNER is the
// common build (the jumper between the two strips hides behind the driver, so
// the chain runs outer-left → centre → outer-right); JOIN_OUTER is the same
// two strips joined at their far ends. Which one it is, is a WIRING fact and
// therefore lives in the profile, not in the code.
static inline void mirror_pair(int p, int half, int &left, int &right)
{
    if (cfg_join == JOIN_INNER) { left = half - 1 - p;  right = half + p; }
    else                        { left = p;             right = cfg_count - 1 - p; }
}

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

    // ── Fault: whole strip blinks, nothing below matters ──
    if (amp_fault()) {
        const float lv = ((now / 250) % 2) ? 1.0f : 0.15f;
        for (int i = 0; i < cfg_count; i++) put(i, alert, lit(alert, lv));
        flush();
        return;
    }

    const State::PlayState ps = State::app.state;
    const float energy = State::app.energy;

    if (cfg_mapping == MAP_AREA) {
        // One field of light: level and colour carry everything, position
        // carries nothing. Right behind a diffuser, wrong for a visible bar.
        RGB  base = accent;
        float lv  = 0.0f;
        if (vol_overlay) {
            lv = lit(accent, 0.20f + 0.80f * (State::app.volume / 100.0f));
        } else if (ps == State::PLAY_PLAYING) {
            base = glow;
            const float beat = 0.85f + 0.15f * sinf(now / 140.0f);
            lv = lit(glow, (0.18f + 0.82f * energy) * beat);
        } else if (ps == State::PLAY_PAUSED) {
            lv = lit(accent, 0.20f);
        } else if (ps == State::PLAY_STANDBY) {
            lv = lit(accent, 0.5f + 0.5f * sinf(now / 1300.0f));
        }
        for (int i = 0; i < cfg_count; i++) put(i, base, lv);
        flush();
        return;
    }

    // ── MAP_MIRROR: two symmetric bars, filled from the centre outwards ──
    const int half = cfg_count / 2;
    if (half < 1) { flush(); return; }

    for (int p = 0; p < half; p++) {
        int li, ri;
        mirror_pair(p, half, li, ri);
        RGB  base = accent;
        float lv  = 0.0f;

        if (vol_overlay) {
            const int filled = (int)((State::app.volume / 100.0f) * half + 0.5f);
            if (p < filled) { base = accent; lv = lit(accent, 0.9f); }
            else            { base = dim;    lv = lit(dim,    0.25f); }
        } else if (ps == State::PLAY_PLAYING) {
            const float beat = 0.85f + 0.15f * sinf(now / 140.0f);
            const int filled = (int)(energy * half + 0.5f);
            if (p < filled) { base = glow; lv = lit(glow, beat); }
            else            { base = dim;  lv = lit(dim,  0.30f); }
        } else if (ps == State::PLAY_PAUSED) {
            lv = lit(accent, 0.20f);
        } else if (ps == State::PLAY_STANDBY) {
            lv = lit(accent, 0.5f + 0.5f * sinf(now / 1300.0f));
        }
        put(li, base, lv);
        put(ri, base, lv);
    }
    if (cfg_count & 1) put(cfg_count - 1, accent, 0.0f);   // odd pixel stays dark
    flush();
}

// ─── Render task ────────────────────────────────────────────────────────────
// Own task on core 0, NOT the Arduino loop. LVGL blocks loop() for tens of ms
// while it composes a frame, and an LED animation clocked off that inherits
// every hitch — the judder seen on 04.09.2026. The strip needs a steady
// cadence, nothing else, so it gets its own timebase.

static void led_task_fn(void *)
{
    TickType_t last = xTaskGetTickCount();
    for (;;) {
        if (xSemaphoreTake(cfg_lock, portMAX_DELAY) == pdTRUE) {
            if (active()) render();
            xSemaphoreGive(cfg_lock);
        }
        vTaskDelayUntil(&last, pdMS_TO_TICKS(FRAME_MS));
    }
}

// ─── Configuration ──────────────────────────────────────────────────────────

bool configure(int pin, int count, bool rgbw, uint8_t brightness,
               Mapping mapping, ChainJoin join)
{
    if (count < 0 || count > 300) {
        Serial.printf("LED: rejected count=%d\n", count);
        return false;
    }
    if (!cfg_lock) {
        cfg_lock = xSemaphoreCreateMutex();
        if (!cfg_lock) return false;
    }
    xSemaphoreTake(cfg_lock, portMAX_DELAY);

    cfg_mapping    = mapping;
    cfg_join       = join;
    cfg_brightness = brightness;

    // count == 0 → this speaker has no strip. Release bus, pin and buffer.
    if (count == 0) {
        if (spi_dev || bus_up) { teardown(); Serial.println("LED: disabled"); }
        xSemaphoreGive(cfg_lock);
        return true;
    }

    if (!pin_is_free(pin)) {
        Serial.printf("LED: rejected pin=%d (reserved by panel/I2C)\n", pin);
        xSemaphoreGive(cfg_lock);
        return false;
    }

    // Idempotent: the bridge re-sends LED: on every reconnect, and rebuilding
    // the bus there would blink the strip on every USB hiccup.
    if (spi_dev && pin == cfg_pin && count == cfg_count && rgbw == cfg_rgbw) {
        xSemaphoreGive(cfg_lock);
        return true;
    }

    teardown();

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
        xSemaphoreGive(cfg_lock);
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
        xSemaphoreGive(cfg_lock);
        return false;
    }

    dma_buf = (uint8_t *)heap_caps_malloc(dma_len, MALLOC_CAP_DMA);
    if (!dma_buf) {
        Serial.printf("LED: DMA alloc of %u B failed\n", (unsigned)dma_len);
        teardown();
        xSemaphoreGive(cfg_lock);
        return false;
    }
    memset(dma_buf, 0, dma_len);

    cfg_pin   = pin;
    cfg_count = count;
    cfg_rgbw  = rgbw;

    Serial.printf("LED: pin=%d n=%d %s bri=%u map=%s join=%s (SPI3, %u B DMA)\n",
                  cfg_pin, cfg_count, cfg_rgbw ? "RGBW" : "RGB", cfg_brightness,
                  cfg_mapping == MAP_AREA ? "area" : "mirror",
                  cfg_join == JOIN_INNER ? "inner" : "outer", (unsigned)dma_len);

    xSemaphoreGive(cfg_lock);

    if (!led_task) {
        xTaskCreatePinnedToCore(led_task_fn, "led_status", 3072, NULL,
                                4 /*prio, below touch*/, &led_task, 0 /*core*/);
    }
    return true;
}

}  // namespace LedStatus

#endif  // ARDUINO
