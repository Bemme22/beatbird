// =============================================================================
// BeatBird Display v4 — Libratone Beat Speaker Control UI
// Board: Waveshare ESP32-S3-Touch-AMOLED-1.43 (SH8601 display via QSPI)
//
// v4 (cleanup):
//   · All UI logic moved to ScreenBoot / ScreenPlayer
//   · Legacy AppState + handle_serial removed — single source of truth is now
//     State::app, written by Proto::poll()
//   · Local standby timer removed — Pi drives standby exclusively via
//     ST:standby
//   · Status screen removed — diagnostics belong on the Pi web UI / HA
//   · main.cpp now owns only: hardware bring-up, the LVGL touch driver,
//     brightness dimming, and the main loop
//
// v4.1 (touch debounce):
//   · Touch callback now requires N consecutive empty reads before reporting
//     RELEASED. Fixes single-frame FT6x36 / I²C glitches causing one tap to
//     fire two on_released events (which was making PLAYPAUSE pause-then-
//     immediately-resume, looking like "music keeps playing").
// =============================================================================

#include <Arduino.h>
#include <lvgl.h>
#include <Wire.h>
#include "pins.h"
#include "esp_log.h"
#include "state.h"
#include "theme.h"
#include "proto.h"
#include "screens/screen_boot.h"
#include "screens/screen_player.h"
#include "screens/screen_settings.h"
#include "screens/screen_standby.h"

// LVGL internal hit-test shim — kept for compilation parity with prior builds.
struct _lv_hit_test_info_t {
    const lv_point_t *point;
    bool res;
};

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_idf_version.h"
#include "sh8601/esp_lcd_sh8601.h"
#include "esp_heap_caps.h"

// ─── Hardware handles ───────────────────────────────────────────────────────
static esp_lcd_panel_handle_t    panel_handle     = NULL;
static esp_lcd_panel_io_handle_t io_handle_global = NULL;
static SemaphoreHandle_t         flush_done_sem   = NULL;
static volatile int              dma_done_count   = 0;
static bool                      touch_dev        = false;
static uint8_t                   disp_brightness  = 255;

// ─── SH8601 init sequence (from Waveshare reference) ────────────────────────
// Panel orientation: via build flag DISPLAY_ROTATE_DEG.
//   "native" | "default" → no MADCTL command sent, use the panel's hardware
//                          default (this is what Beat #1 wants — also the
//                          original init-commit state of this firmware)
//   90  → 0xA0 (MV+MX)   — Zipp Mini 2 mounting (current default for that env)
//   180 → 0xC0 (MX+MY)
//   270 → 0x60 (MV+MY)
//   0   → 0x00 explicitly (rarely useful; native is preferred for no-rotation
//          speakers since it leaves the panel's column/row gap untouched)
// Touch coords are transformed in the LVGL touch read callback below — keep
// in sync with whatever rotation is used here.
//
// Define DISPLAY_ROTATE_NATIVE to skip the MADCTL command entirely (Beat path).
// Legacy DISPLAY_ROTATE_90 is honored for backward compat.
#ifdef DISPLAY_ROTATE_90
  #if DISPLAY_ROTATE_90
    #define DISPLAY_ROTATE_DEG 90
  #else
    #define DISPLAY_ROTATE_NATIVE 1
  #endif
#endif
#ifndef DISPLAY_ROTATE_NATIVE
  #ifndef DISPLAY_ROTATE_DEG
  #define DISPLAY_ROTATE_DEG 90
  #endif
  #if   DISPLAY_ROTATE_DEG ==   0
  #define BB_MADCTL 0x00
  #elif DISPLAY_ROTATE_DEG ==  90
  #define BB_MADCTL 0xA0
  #elif DISPLAY_ROTATE_DEG == 180
  #define BB_MADCTL 0xC0
  #elif DISPLAY_ROTATE_DEG == 270
  #define BB_MADCTL 0x60
  #else
  #error "DISPLAY_ROTATE_DEG must be 0, 90, 180 or 270 (or define DISPLAY_ROTATE_NATIVE)"
  #endif
#endif

static const sh8601_lcd_init_cmd_t sh8601_init_cmds[] = {
    {0x11, (uint8_t[]){0x00}, 0, 80},
#ifndef DISPLAY_ROTATE_NATIVE
    {0x36, (uint8_t[]){BB_MADCTL}, 1,  0},
#endif
    {0xC4, (uint8_t[]){0x80}, 1,  0},
    {0x53, (uint8_t[]){0x20}, 1,  1},
    {0x63, (uint8_t[]){0xFF}, 1,  1},
    {0x51, (uint8_t[]){0xFF}, 1,  1},
    // 0x29 (Display On) sent after first LVGL frame to avoid green flash on boot
};

// =============================================================================
// LVGL flush callbacks
// =============================================================================
static uint32_t lv_tick_cb_ms() { return (uint32_t)millis(); }

static bool on_color_trans_done(esp_lcd_panel_io_handle_t,
                                esp_lcd_panel_io_event_data_t *,
                                void *)
{
    #pragma GCC diagnostic push
    #pragma GCC diagnostic ignored "-Wvolatile"
    dma_done_count++;
    #pragma GCC diagnostic pop
    BaseType_t awoken = pdFALSE;
    xSemaphoreGiveFromISR(flush_done_sem, &awoken);
    return awoken == pdTRUE;
}

static void lvgl_flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *color_p)
{
    static int flush_count = 0;
    lv_draw_sw_rgb565_swap(color_p,
        lv_area_get_width(area) * lv_area_get_height(area));
    esp_lcd_panel_draw_bitmap(panel_handle,
        area->x1, area->y1, area->x2 + 1, area->y2 + 1, color_p);
    if (++flush_count == 1) {
        lv_timer_t *t = lv_timer_create([](lv_timer_t *t) {
            esp_lcd_panel_disp_on_off(panel_handle, true);
            lv_timer_set_repeat_count(t, 0);
        }, 60, nullptr);
        lv_timer_set_repeat_count(t, 1);
    }
}

static void lvgl_flush_wait_cb(lv_display_t *)
{
    xSemaphoreTake(flush_done_sem, portMAX_DELAY);
}

static void lvgl_rounder_cb(lv_event_t *e)
{
    lv_area_t *area = (lv_area_t *)lv_event_get_param(e);
    area->x1 = (area->x1 >> 1) << 1;
    area->y1 = (area->y1 >> 1) << 1;
    area->x2 = ((area->x2 >> 1) << 1) + 1;
    area->y2 = ((area->y2 >> 1) << 1) + 1;
}

// =============================================================================
// Touch driver — FT6x36 polling, with glitch debounce
// =============================================================================
// The FT6x36 occasionally reports "no finger" for a single poll cycle during
// a sustained touch (I²C transaction failure, ESD on the touch overlay, or a
// short between-frame sampling gap on the controller). Without debouncing,
// LVGL interprets such a frame as a release+press pair → on_released fires
// mid-touch → discrete commands like PLAYPAUSE end up sent twice (pausing
// then immediately resuming).
//
// Solution: require RELEASE_STREAK_THRESHOLD consecutive empty reads before
// actually signalling RELEASED. At ~16 ms LVGL refresh, 2 frames = ~32 ms
// of grace, well below human reaction time but ample to absorb single-frame
// sensor / I²C glitches.
// Shared state between the touch poll task (writer) and LVGL's
// input callback (reader). Critical section guards a coherent snapshot
// — pressed + x/y change together, so we don't want LVGL to read a
// pressed=true with stale coordinates from before the latest event.
static portMUX_TYPE touch_mux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool     touch_state_pressed = false;
static volatile uint16_t touch_state_x       = 0;
static volatile uint16_t touch_state_y       = 0;

// Dedicated FreeRTOS task hammers the touch chip's I²C every 4 ms
// (250 Hz) — well above the LVGL 60 Hz refresh — so even a 10 ms soft
// tap that lived entirely between two LVGL polls now lands in at least
// two consecutive task ticks and gets latched into the press state.
//
// Filter logic is the same release-streak hysteresis the original
// in-callback code used, just scaled to the 4 ms tick: 4 streak ticks
// ≈ 16 ms of "still pressed" grace after the chip drops the touch.
// Catches I²C glitches and end-of-tap micro-bounces without making
// PLAYPAUSE fire twice.
static void touch_poll_task(void * /*arg*/)
{
    constexpr uint8_t RELEASE_STREAK_THRESHOLD = 4;
    uint16_t lx = 0, ly = 0;
    bool     was_pressed    = false;
    uint8_t  release_streak = 0;

    for (;;) {
        if (!touch_dev) {
            // No touch chip — task still runs but never reports a press.
            // Sleep longer to save CPU.
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }

        uint8_t buf[5] = {0};
        bool touch_present = false;
        Wire.beginTransmission(TOUCH_I2C_ADDR);
        Wire.write(0x02);
        if (Wire.endTransmission(false) == 0 &&
            Wire.requestFrom(TOUCH_I2C_ADDR, 5) == 5) {
            for (uint8_t i = 0; i < 5; i++) if (Wire.available()) buf[i] = Wire.read();
            touch_present = (buf[0] != 0);
        }

        if (touch_present) {
            lx = (((uint16_t)buf[1] & 0x0F) << 8) | buf[2];
            ly = (((uint16_t)buf[3] & 0x0F) << 8) | buf[4];
            was_pressed    = true;
            release_streak = 0;
        } else if (was_pressed && release_streak < RELEASE_STREAK_THRESHOLD) {
            release_streak++;
        } else {
            was_pressed    = false;
            release_streak = 0;
        }

        portENTER_CRITICAL(&touch_mux);
        touch_state_pressed = was_pressed;
        if (was_pressed) {
            touch_state_x = lx;
            touch_state_y = ly;
        }
        portEXIT_CRITICAL(&touch_mux);

        vTaskDelay(pdMS_TO_TICKS(4));
    }
}

static void lvgl_touchpad_cb(lv_indev_t *indev, lv_indev_data_t *data)
{
    if (!touch_dev) { data->state = LV_INDEV_STATE_RELEASED; return; }

    bool pressed;
    uint16_t lx, ly;
    portENTER_CRITICAL(&touch_mux);
    pressed = touch_state_pressed;
    lx      = touch_state_x;
    ly      = touch_state_y;
    portEXIT_CRITICAL(&touch_mux);

    if (pressed) {
        uint16_t raw_x = lx < LCD_WIDTH  ? lx : LCD_WIDTH  - 1;
        uint16_t raw_y = ly < LCD_HEIGHT ? ly : LCD_HEIGHT - 1;
#ifdef DISPLAY_ROTATE_NATIVE
        data->point.x = raw_x;
        data->point.y = raw_y;
#elif DISPLAY_ROTATE_DEG ==   0
        data->point.x = raw_x;
        data->point.y = raw_y;
#elif DISPLAY_ROTATE_DEG ==  90
        // Zipp Mini 2 (and any other DEG=90 mount) reports y mirrored
        // relative to the visual frame: a touch on the visual TOP of the
        // panel comes in at raw_x ≈ LCD_WIDTH (so the previous
        // (LCD_WIDTH-1) - raw_x gave y_data ≈ 464 = visual BOTTOM in
        // LVGL's coord system). Plain transpose (no second flip) lines
        // up. Verified with on-device DEBUG:dx/dy logging: SY≈464 at
        // visual top, SY≈5 at visual bottom under the old map.
        data->point.x = raw_y;
        data->point.y = raw_x;
#elif DISPLAY_ROTATE_DEG == 180
        data->point.x = (LCD_WIDTH  - 1) - raw_x;
        data->point.y = (LCD_HEIGHT - 1) - raw_y;
#elif DISPLAY_ROTATE_DEG == 270
        data->point.x = (LCD_HEIGHT - 1) - raw_y;
        data->point.y = raw_x;
#endif
        data->state   = LV_INDEV_STATE_PRESSED;
        State::wake_screen();
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

// =============================================================================
// Brightness control + dim ramp
// =============================================================================
static void set_brightness(uint8_t b)
{
    if (!io_handle_global) return;
    uint32_t cmd = (0x02UL << 24) | (0x51UL << 8);
    esp_lcd_panel_io_tx_param(io_handle_global, (int)cmd, &b, 1);
    disp_brightness = b;
}

static void check_dim()
{
    static uint8_t target = 255;
    uint32_t idle = millis() - State::app.last_touch_ms;
    uint8_t new_target = (idle > Theme::DIM_AFTER_MS)
                       ? Theme::DIM_BRIGHTNESS
                       : Theme::FULL_BRIGHTNESS;
    if (new_target != target) target = new_target;
    if (disp_brightness == target) return;

    if (disp_brightness < target) {
        uint8_t step = min((int)(target - disp_brightness), 8);
        disp_brightness += step;
    } else {
        uint8_t step = max(1, (int)(disp_brightness - target) / 12);
        disp_brightness -= step;
    }
    set_brightness(disp_brightness);
}

// =============================================================================
// Setup
// =============================================================================
void setup()
{
    Proto::begin();                   // runs Serial.begin(115200)
    unsigned long t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);

    esp_log_level_set("*",                ESP_LOG_WARN);
    esp_log_level_set("esp32-hal-i2c-ng", ESP_LOG_WARN);
    Serial.println("\n=== BeatBird Display v4 ===");
#ifdef BEATBIRD_FW_VERSION
    Serial.printf("Firmware: %s\n", BEATBIRD_FW_VERSION);
#endif
    Proto::send_version();

    flush_done_sem = xSemaphoreCreateBinary();

    // Touch I2C
    Wire.begin(TOUCH_I2C_SDA, TOUCH_I2C_SCL, 300000);
    Wire.beginTransmission(TOUCH_I2C_ADDR);
    touch_dev = (Wire.endTransmission() == 0);
    Serial.printf("Touch: %s\n", touch_dev ? "OK" : "NOT FOUND");
    // 250 Hz dedicated touch poller — see touch_poll_task above.
    // 4 KB stack is generous for the loop's small locals + Wire calls.
    xTaskCreatePinnedToCore(touch_poll_task, "touch_poll", 4096, NULL,
                            5 /*prio*/, NULL, 1 /*core*/);

    // SPI bus (QSPI)
    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num     = -1;
    buscfg.miso_io_num     = -1;
    buscfg.data0_io_num    = LCD_SDIO0;
    buscfg.data1_io_num    = LCD_SDIO1;
    buscfg.sclk_io_num     = LCD_SCLK;
    buscfg.data2_io_num    = LCD_SDIO2;
    buscfg.data3_io_num    = LCD_SDIO3;
    buscfg.max_transfer_sz = LCD_WIDTH * 80 * 2;
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));

    // Panel IO
    esp_lcd_panel_io_handle_t io_handle = NULL;
    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num         = LCD_CS;
    io_config.dc_gpio_num         = -1;
    io_config.spi_mode            = 0;
    io_config.pclk_hz             = 60 * 1000 * 1000;
    io_config.trans_queue_depth   = 10;
    io_config.on_color_trans_done = on_color_trans_done;
    io_config.lcd_cmd_bits        = 32;
    io_config.lcd_param_bits      = 8;
    io_config.flags.quad_mode     = true;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(
        (esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_config, &io_handle));
    io_handle_global = io_handle;

    // SH8601 panel
    sh8601_vendor_config_t vendor_config = {};
    vendor_config.flags.use_qspi_interface = 1;
    vendor_config.init_cmds      = sh8601_init_cmds;
    vendor_config.init_cmds_size = sizeof(sh8601_init_cmds) / sizeof(sh8601_init_cmds[0]);

    esp_lcd_panel_dev_config_t panel_config = {};
    panel_config.reset_gpio_num = LCD_RST;
    panel_config.bits_per_pixel = 16;
    panel_config.vendor_config  = &vendor_config;
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
    panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
#else
    panel_config.color_space   = ESP_LCD_COLOR_SPACE_RGB;
#endif
    ESP_ERROR_CHECK(esp_lcd_new_panel_sh8601(io_handle, &panel_config, &panel_handle));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_handle));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel_handle));
    // SH8601 has a 6-pixel column offset between its raw addressing and the
    // visible 466×466 active area. Without compensating, the last 6 columns
    // wrap to the opposite edge of the display as visible "stripes".
    //   - NATIVE (no MADCTL): set x_gap=6, y_gap=0 (as in the init commit)
    //   - 90° (MADCTL=0xA0 = MV+MX): MX reverses column order, so the gap
    //     swaps into the y direction; we need y_gap=6, x_gap=0.
    // Other rotations follow the same logic.
#if defined(DISPLAY_ROTATE_NATIVE) || (defined(DISPLAY_ROTATE_DEG) && DISPLAY_ROTATE_DEG == 0)
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel_handle, 0x06, 0x00));
#elif defined(DISPLAY_ROTATE_DEG) && DISPLAY_ROTATE_DEG == 180
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel_handle, 0x06, 0x00));
#else  // 90° (Zipp default) or 270°
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel_handle, 0x00, 0x06));
#endif
    Serial.println("Display: OK");

    // LVGL
    lv_init();
    lv_tick_set_cb(lv_tick_cb_ms);

    size_t buf_size = LCD_WIDTH * 20 * sizeof(lv_color16_t);
    uint8_t *buf1 = (uint8_t *)heap_caps_malloc(buf_size, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    uint8_t *buf2 = (uint8_t *)heap_caps_malloc(buf_size, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!buf1 || !buf2) {
        Serial.println("ERROR: LVGL buffer alloc failed!");
        while (1) delay(100);
    }
    Serial.printf("LVGL: %u B x2 DMA, heap=%u\n", buf_size, ESP.getFreeHeap());

    lv_display_t *disp = lv_display_create(LCD_WIDTH, LCD_HEIGHT);
    lv_display_set_flush_cb(disp, lvgl_flush_cb);
    lv_display_set_flush_wait_cb(disp, lvgl_flush_wait_cb);
    lv_display_set_buffers(disp, buf1, buf2, buf_size, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_add_event_cb(disp, lvgl_rounder_cb, LV_EVENT_INVALIDATE_AREA, NULL);

    lv_indev_t *indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, lvgl_touchpad_cb);
    lv_indev_set_gesture_min_distance(indev, 50);
    lv_indev_set_long_press_time(indev, Theme::LONG_PRESS_MS);

    ScreenBoot::create();
    ScreenBoot::show();
    ScreenPlayer::create();
    ScreenStandby::create();

    Serial.println("Ready.");
    Proto::send_boot_marker();
}

// =============================================================================
// Loop
// =============================================================================
void loop()
{
    Proto::poll();

    lv_timer_handler();

    if (ScreenBoot::is_active()) {
        ScreenBoot::update();
        if (State::app.connected_to_pi) {
            ScreenBoot::transition_to(ScreenPlayer::root());
        }
    }

    ScreenPlayer::update();
    ScreenStandby::update();
    ScreenSettings::update();

    check_dim();

    Proto::send_heartbeat();

    delay(5);
}