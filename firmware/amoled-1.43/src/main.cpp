// =============================================================================
// BeatBird Display v3 - Libratone Beat Speaker Control UI
// Board: Waveshare ESP32-S3-Touch-AMOLED-1.43 (SH8601 display via QSPI)
//
// v3: Clean rewrite — no Dual-Core, no ArduinoJson, no complex LVGL objects.
//     Hardware init unchanged from v2. UI rebuilt from scratch.
//     All v3 patches applied: anti-flicker, energy ring, multi-line title,
//     reliable play/pause, gesture guard fix.
//
// Serial Protocol (Pi → ESP32):
//   ST:play|TI:Death of Love|AR:James Blake|SO:spotify|VO:28|PO:45000|DU:234000|LV:42|TM:20:15
//   SYS:cp=52.1|ht=ok|hs=ok|ds=1|sv=1|wi=-55
//
// Serial Protocol (ESP32 → Pi):
//   VOL:0-100
//   CMD:PLAYPAUSE
//   CMD:NEXT
//   CMD:PREV
// =============================================================================

#include <Arduino.h>
#include <lvgl.h>
#include <Wire.h>
#include "pins.h"
#include "esp_log.h"
#include "state.h"
#include "theme.h"
#include "screens/screen_boot.h"
#include "screens/screen_player.h"

struct _lv_hit_test_info_t {
    const lv_point_t * point;
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

// =============================================================================
// Hardware handles
// =============================================================================
static esp_lcd_panel_handle_t    panel_handle     = NULL;
static esp_lcd_panel_io_handle_t io_handle_global = NULL;
static SemaphoreHandle_t         flush_done_sem   = NULL;
static volatile int              dma_done_count   = 0;
static bool                      touch_dev        = false;

// =============================================================================
// SH8601 init sequence (from Waveshare reference, unchanged)
// =============================================================================
static const sh8601_lcd_init_cmd_t sh8601_init_cmds[] = {
    {0x11, (uint8_t[]){0x00}, 0, 80},
    {0x36, (uint8_t[]){0xA0}, 1,  0},   // ← NEU: MADCTL = MV+MX = +90° CW
    {0xC4, (uint8_t[]){0x80}, 1,  0},
    {0x53, (uint8_t[]){0x20}, 1,  1},
    {0x63, (uint8_t[]){0xFF}, 1,  1},
    {0x51, (uint8_t[]){0xFF}, 1,  1},
};

// =============================================================================
// Application State
// =============================================================================
enum PlayState { STATE_STOPPED, STATE_PLAYING, STATE_PAUSED, STATE_STANDBY };

struct AppState {
    PlayState state    = STATE_STOPPED;
    int       volume   = 35;
    String    title    = "";
    String    artist   = "";
    String    source   = "none";
    uint32_t  pos_ms   = 0;
    uint32_t  dur_ms   = 1;
    float     energy   = 0.0f;
    String    timeStr  = "--:--";
    unsigned long lastTouch   = 0;
    unsigned long lastAudioMs = 0;
    unsigned long standbyMs   = 300000;  // 5 min
} app;

// =============================================================================
// UI objects
// =============================================================================
static lv_obj_t *scr_main     = nullptr;
static lv_obj_t *arc_volume   = nullptr;
static lv_obj_t *arc_progress = nullptr;
static lv_obj_t *energy_layer = nullptr;
static lv_obj_t *lbl_title    = nullptr;
static lv_obj_t *lbl_artist   = nullptr;
static lv_obj_t *lbl_state    = nullptr;   // ▶ / ⏸ — center
static lv_obj_t *lbl_source   = nullptr;   // SPOTIFY — above bottom
static lv_obj_t *lbl_volume   = nullptr;   // 28% — very bottom
static lv_obj_t *lbl_bottom   = nullptr;   // kept for compat (hidden)
static lv_obj_t *lbl_standby  = nullptr;
static lv_obj_t *lbl_action   = nullptr;
static lv_timer_t *action_timer = nullptr;

// Status screen
static lv_obj_t *scr_status   = nullptr;
static bool      on_status    = false;
static lv_obj_t *lbl_st_cpu   = nullptr;
static lv_obj_t *lbl_st_amp   = nullptr;
static lv_obj_t *lbl_st_dsp   = nullptr;
static lv_obj_t *lbl_st_svc   = nullptr;
static lv_obj_t *lbl_st_wifi  = nullptr;
static lv_obj_t *lbl_st_song  = nullptr;
static lv_obj_t *lbl_st_stale = nullptr;

// Status data (from SYS: line)
static float  st_cpu    = 0.0f;
static String st_stereo = "---";
static String st_sub    = "---";
static bool   st_dsp    = false;
static bool   st_svc    = false;
static int    st_wifi   = 0;
static unsigned long last_status_rx = 0;

// Display brightness
static uint8_t disp_brightness = 255;

// Energy bar visualizer — 32 radial bars
#define ENERGY_BARS 32
static float bar_val[ENERGY_BARS]  = {};   // smoothed 0-1
static float bar_peak[ENERGY_BARS] = {};   // peak hold 0-1
static float bar_cos[ENERGY_BARS]  = {};   // precomputed cos (set in setup)
static float bar_sin[ENERGY_BARS]  = {};   // precomputed sin

// Arc update guard (prevents feedback loop when setting from serial)
static bool arc_serial_update = false;

// Gesture timing
static unsigned long last_gesture_ms = 0;
static unsigned long last_vol_ms     = 0;

// =============================================================================
// LVGL flush callbacks (unchanged from v2)
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
// Touch callback (unchanged from v2)
// =============================================================================
static void lvgl_touchpad_cb(lv_indev_t *indev, lv_indev_data_t *data)
{
    if (!touch_dev) { data->state = LV_INDEV_STATE_RELEASED; return; }

    uint8_t buf[5] = {0};
    Wire.beginTransmission(TOUCH_I2C_ADDR);
    Wire.write(0x02);
    if (Wire.endTransmission(false) != 0) {
        data->state = LV_INDEV_STATE_RELEASED; return;
    }
    if (Wire.requestFrom(TOUCH_I2C_ADDR, 5) != 5) {
        data->state = LV_INDEV_STATE_RELEASED; return;
    }
    for (uint8_t i = 0; i < 5; i++) if (Wire.available()) buf[i] = Wire.read();

    static uint16_t lx = 0, ly = 0;
    if (buf[0]) {
        uint16_t tx = (((uint16_t)buf[1] & 0x0F) << 8) | buf[2];
        uint16_t ty = (((uint16_t)buf[3] & 0x0F) << 8) | buf[4];
        if (abs((int)tx - (int)lx) > 1 || abs((int)ty - (int)ly) > 1) {
            lx = tx; ly = ty;
        }
        uint16_t raw_x = lx < LCD_WIDTH  ? lx : LCD_WIDTH  - 1;
        uint16_t raw_y = ly < LCD_HEIGHT ? ly : LCD_HEIGHT - 1;
        data->point.x = raw_y;
        data->point.y = (LCD_WIDTH - 1) - raw_x;
        data->state   = LV_INDEV_STATE_PRESSED;
        app.lastTouch = millis();
    } else {
        lx = ly = 0;
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

// =============================================================================
// Brightness control
// =============================================================================
static void set_brightness(uint8_t b)
{
    if (!io_handle_global) return;
    uint32_t cmd = (0x02UL << 24) | (0x51UL << 8);
    esp_lcd_panel_io_tx_param(io_handle_global, (int)cmd, &b, 1);
    disp_brightness = b;
}

// =============================================================================
// Helpers — string parsing
// =============================================================================
static String parse_field(const String &line, const char *key)
{
    // Search for "|KEY:" first (field in middle of line)
    // Fall back to "KEY:" only at position 0 (first field)
    // This prevents false matches inside field values e.g. "Guitar:" matching "AR:"
    String k_pipe  = String("|") + key + ":";
    String k_start = String(key) + ":";
    int idx, start;
    idx = line.indexOf(k_pipe);
    if (idx >= 0) {
        start = idx + k_pipe.length();
    } else if (line.startsWith(k_start)) {
        start = k_start.length();
    } else {
        return "";
    }
    int end = line.indexOf('|', start);
    return (end < 0) ? line.substring(start) : line.substring(start, end);
}

static const char* source_label(const String &s)
{
    if (s == "spotify")   return "SPOTIFY";
    if (s == "bluetooth") return "BLUETOOTH";
    if (s == "toslink")   return "TV";
    if (s == "snapcast")  return "MULTIROOM";
    return "";
}

static lv_color_t source_color(const String &s)
{
    if (s == "spotify")   return lv_color_make(30, 215, 96);
    if (s == "bluetooth") return lv_color_make(50, 130, 246);
    if (s == "toslink")   return lv_color_make(200, 200, 200);
    if (s == "snapcast")  return lv_color_make(255, 160, 40);
    return lv_color_make(80, 80, 80);
}

// =============================================================================
// Energy ring — radial bar visualizer (32 bars, precomputed angles)
// Lines are faster than arcs in LVGL — no curve pixel calculation needed.
// =============================================================================
static void energy_draw_cb(lv_event_t *e)
{
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_draw_line_dsc_t ldsc;
    lv_draw_line_dsc_init(&ldsc);
    ldsc.round_start = 1;
    ldsc.round_end   = 1;

    const int32_t CX      = LCD_WIDTH  / 2;
    const int32_t CY      = LCD_HEIGHT / 2;
    const float   R_INNER = 90.0f;   // bar origin — 14mm diameter
    const float   R_MIN   = 0.0f;    // bars start at 0, nearly invisible at low energy
    const float   RMAX    = 83.0f;   // max length — tip at 173px = 27mm diameter

    for (int i = 0; i < ENERGY_BARS; i++) {
        float v = bar_val[i];
        if (v < 0.012f) continue;   // truly invisible at near-zero

        // Bar always starts at R_INNER, extends at least R_MIN + energy*RMAX
        float r_end = R_INNER + R_MIN + v * RMAX;
        float cx = bar_cos[i];
        float cy = bar_sin[i];

        lv_point_precise_t p0 = { (lv_value_precise_t)(CX + cx * R_INNER), (lv_value_precise_t)(CY + cy * R_INNER) };
        lv_point_precise_t p1 = { (lv_value_precise_t)(CX + cx * r_end),   (lv_value_precise_t)(CY + cy * r_end)   };

        uint8_t g   = (uint8_t)(160 + v * 95);    // min 160, max 255 — always bright green
        ldsc.color  = lv_color_make(0, g, (uint8_t)(g * 0.3f));
        ldsc.opa    = (lv_opa_t)(180 + v * 75);   // min 180, max 255
        ldsc.width  = (uint16_t)(2 + (int)(v * 2));
        ldsc.p1     = p0;
        ldsc.p2     = p1;
        lv_draw_line(layer, &ldsc);

        // Peak dot
        if (bar_peak[i] > 0.05f) {
            float rp = R_INNER + R_MIN + bar_peak[i] * RMAX + 2.0f;
            lv_point_precise_t pp  = { (lv_value_precise_t)(CX + cx * rp), (lv_value_precise_t)(CY + cy * rp) };
            lv_point_precise_t pp1 = { pp.x + 1, pp.y };
            ldsc.color = lv_color_make(180, 255, 180);
            ldsc.opa   = (lv_opa_t)(150 + bar_peak[i] * 105);
            ldsc.width = 3;
            ldsc.p1    = pp;
            ldsc.p2    = pp1;
            lv_draw_line(layer, &ldsc);
        }
    }
}

// =============================================================================
// Update energy bars
// =============================================================================
static void update_energy()
{
    float e = (app.state == STATE_PLAYING) ? app.energy : 0.0f;
    float t = millis() * 0.001f;
    bool changed = false;

    for (int i = 0; i < ENERGY_BARS; i++) {
        float fast  = sinf(t * 12.0f + i * 0.7f) * 0.5f + 0.5f;
        float slow  = sinf(t *  3.0f + i * 1.1f) * 0.5f + 0.5f;

        // Scale energy so 40% input → ~80% bar height (full range actually used)
        float e_scaled = fminf(1.0f, e * 2.2f);
        float target = (e_scaled < 0.01f) ? 0.0f
                     : e_scaled * (0.65f + fast * 0.25f + slow * 0.10f);

        // Very fast attack for snappy response
        float alpha = (target > bar_val[i]) ? 0.85f : 0.14f;
        float prev  = bar_val[i];
        bar_val[i]  = bar_val[i] * (1.0f - alpha) + target * alpha;
        if (bar_val[i] < 0.005f) bar_val[i] = 0.0f;

        // Peak hold + slow decay
        if (bar_val[i] >= bar_peak[i]) bar_peak[i] = bar_val[i];
        else bar_peak[i] *= 0.986f;
        if (bar_peak[i] < 0.01f) bar_peak[i] = 0.0f;

        if (fabsf(bar_val[i] - prev) > 0.005f) changed = true;
    }

    if (changed && energy_layer) lv_obj_invalidate(energy_layer);
}

// =============================================================================
// Update bottom label — anti-flicker: only set when text changes
// =============================================================================
static void update_bottom_label()
{
    // State symbol
    if (lbl_state) {
        const char *sym = "";
        if      (app.state == STATE_PLAYING) sym = LV_SYMBOL_PLAY;
        else if (app.state == STATE_PAUSED)  sym = LV_SYMBOL_PAUSE;
        static char prev_sym[16] = "";
        if (strcmp(sym, prev_sym) != 0) {
            strncpy(prev_sym, sym, sizeof(prev_sym));
            lv_label_set_text(lbl_state, sym);
        }
    }

    // Source
    if (lbl_source) {
        const char *src = source_label(app.source);
        static char prev_src[32] = "";
        if (strcmp(src, prev_src) != 0) {
            strncpy(prev_src, src, sizeof(prev_src));
            lv_label_set_text(lbl_source, src);
            lv_obj_set_style_text_color(lbl_source, source_color(app.source), 0);
        }
    }

    // Volume
    if (lbl_volume) {
        char buf[12];
        snprintf(buf, sizeof(buf), "%d%%", app.volume);
        static char prev_vol[12] = "";
        if (strcmp(buf, prev_vol) != 0) {
            strncpy(prev_vol, buf, sizeof(prev_vol));
            lv_label_set_text(lbl_volume, buf);
        }
    }
}

// =============================================================================
// Update arcs — anti-flicker: only set when value changes
// =============================================================================
static void update_arcs()
{
    if (!arc_volume || !arc_progress) return;

    static int last_vol  = -1;
    static int last_prog = -1;

    if (app.volume != last_vol) {
        last_vol = app.volume;
        arc_serial_update = true;
        lv_arc_set_value(arc_volume, app.volume);
        arc_serial_update = false;
    }

    int prog = (app.dur_ms > 1)
        ? (int)(constrain((float)app.pos_ms / (float)app.dur_ms, 0.0f, 1.0f) * 100)
        : 0;
    if (prog != last_prog) {
        last_prog = prog;
        arc_serial_update = true;
        lv_arc_set_value(arc_progress, prog);
        arc_serial_update = false;
    }
}

// =============================================================================
// Gesture action feedback
// =============================================================================
static void action_hide_cb(lv_timer_t *)
{
    if (lbl_action) lv_obj_add_flag(lbl_action, LV_OBJ_FLAG_HIDDEN);
    action_timer = nullptr;
}

static void show_action(const char *txt)
{
    if (!lbl_action) return;
    lv_label_set_text(lbl_action, txt);
    lv_obj_clear_flag(lbl_action, LV_OBJ_FLAG_HIDDEN);
    if (action_timer) lv_timer_reset(action_timer);
    else {
        action_timer = lv_timer_create(action_hide_cb, 1200, nullptr);
        lv_timer_set_repeat_count(action_timer, 1);
    }
}

// =============================================================================
// Volume change via swipe
// =============================================================================
static void vol_change(int delta)
{
    int v = constrain(app.volume + delta, 0, 100);
    if (v == app.volume) return;
    app.volume = v;
    update_arcs();
    update_bottom_label();
    Serial.printf("VOL:%d\n", v);
}

// Forward declaration
static void wake_display();

// =============================================================================
// Touch callbacks
// =============================================================================
static void screen_tap_cb(lv_event_t *e)
{
    if (app.state == STATE_STANDBY) return;
    if (millis() - last_gesture_ms < 300) return;
    if (millis() - last_vol_ms     < 600) return;
    wake_display();
    Serial.println("CMD:PLAYPAUSE");
    show_action(app.state == STATE_PLAYING ? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
}

static void screen_gesture_cb(lv_event_t *e)
{
    lv_indev_t *indev = lv_indev_get_act();
    if (!indev || app.state == STATE_STANDBY) return;
    lv_dir_t dir = lv_indev_get_gesture_dir(indev);
    if (dir == LV_DIR_NONE) return;     // guard only when real gesture
    last_gesture_ms = millis();
    wake_display();

    char buf[32];
    switch (dir) {
        case LV_DIR_LEFT:
            Serial.println("CMD:NEXT");
            show_action(LV_SYMBOL_NEXT "  NEXT");
            break;
        case LV_DIR_RIGHT:
            Serial.println("CMD:PREV");
            show_action("PREV  " LV_SYMBOL_PREV);
            break;
        case LV_DIR_TOP:
            last_vol_ms = millis();
            vol_change(+10);
            snprintf(buf, sizeof(buf), "%s  %d%%", LV_SYMBOL_VOLUME_MAX, app.volume);
            show_action(buf);
            break;
        case LV_DIR_BOTTOM:
            last_vol_ms = millis();
            vol_change(-10);
            snprintf(buf, sizeof(buf), "%s  %d%%", LV_SYMBOL_VOLUME_MID, app.volume);
            show_action(buf);
            break;
        default: break;
    }
}

static void screen_long_press_cb(lv_event_t *e)
{
    on_status = true;

    if (lbl_st_cpu) {
        char buf[32];
        snprintf(buf, sizeof(buf), "%.1f\xC2\xB0""C", st_cpu);
        lv_label_set_text(lbl_st_cpu, buf);
        lv_color_t c = (st_cpu < 55) ? lv_color_make(0, 200, 80)
                     : (st_cpu < 70) ? lv_color_make(255, 180, 0)
                     :                 lv_color_make(255, 40, 40);
        lv_obj_set_style_text_color(lbl_st_cpu, c, 0);
    }
    if (lbl_st_amp) {
        char buf[48];
        snprintf(buf, sizeof(buf), "ST:%s  SUB:%s", st_stereo.c_str(), st_sub.c_str());
        lv_label_set_text(lbl_st_amp, buf);
    }
    if (lbl_st_dsp) {
        lv_label_set_text(lbl_st_dsp,
            st_dsp ? LV_SYMBOL_OK " CamillaDSP" : LV_SYMBOL_CLOSE " CamillaDSP");
        lv_obj_set_style_text_color(lbl_st_dsp,
            st_dsp ? lv_color_make(0, 200, 80) : lv_color_make(255, 40, 40), 0);
    }
    if (lbl_st_svc) {
        lv_label_set_text(lbl_st_svc,
            st_svc ? LV_SYMBOL_OK " Spotify" : LV_SYMBOL_CLOSE " Spotify");
        lv_obj_set_style_text_color(lbl_st_svc,
            st_svc ? lv_color_make(0, 200, 80) : lv_color_make(255, 40, 40), 0);
    }
    if (lbl_st_wifi && st_wifi != 0) {
        char buf[24];
        snprintf(buf, sizeof(buf), "WiFi %d dBm", st_wifi);
        lv_label_set_text(lbl_st_wifi, buf);
        lv_color_t c = (st_wifi > -50) ? lv_color_make(0, 200, 80)
                     : (st_wifi > -65) ? lv_color_make(255, 180, 0)
                     :                   lv_color_make(255, 40, 40);
        lv_obj_set_style_text_color(lbl_st_wifi, c, 0);
    }
    if (lbl_st_song) {
        String s = app.title.length() ? app.title + " \xE2\x80\x94 " + app.artist : "---";
        lv_label_set_text(lbl_st_song, s.c_str());
    }
    if (lbl_st_stale) {
        bool stale = (last_status_rx == 0) || (millis() - last_status_rx > 15000);
        if (stale) lv_obj_clear_flag(lbl_st_stale, LV_OBJ_FLAG_HIDDEN);
        else       lv_obj_add_flag  (lbl_st_stale, LV_OBJ_FLAG_HIDDEN);
    }
    lv_screen_load_anim(scr_status, LV_SCR_LOAD_ANIM_MOVE_LEFT, 300, 0, false);
}

static void status_tap_cb(lv_event_t *e)
{
    on_status = false;
    lv_screen_load_anim(scr_main, LV_SCR_LOAD_ANIM_MOVE_RIGHT, 300, 0, false);
}

static void arc_volume_cb(lv_event_t *e)
{
    if (arc_serial_update) return;
    // Arc is not clickable — callback only fires from lv_arc_set_value,
    // which is already guarded. Safety fallback only.
}

// =============================================================================
// Standby / wake / dim
// =============================================================================
static void check_standby()
{
    if (app.state == STATE_STANDBY) return;
    if (app.state == STATE_PLAYING) { app.lastAudioMs = millis(); return; }
    if (app.lastAudioMs == 0)       { app.lastAudioMs = millis(); return; }
    if (millis() - app.lastAudioMs < app.standbyMs) return;

    app.state = STATE_STANDBY;
    if (lbl_title)   lv_obj_add_flag(lbl_title,   LV_OBJ_FLAG_HIDDEN);
    if (lbl_artist)  lv_obj_add_flag(lbl_artist,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_state)   lv_obj_add_flag(lbl_state,   LV_OBJ_FLAG_HIDDEN);
    if (lbl_source)  lv_obj_add_flag(lbl_source,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_volume)  lv_obj_add_flag(lbl_volume,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_standby) {
        lv_label_set_text(lbl_standby, app.timeStr.c_str());
        lv_obj_clear_flag(lbl_standby, LV_OBJ_FLAG_HIDDEN);
    }
}

static void check_wake()
{
    if (app.state != STATE_STANDBY) return;
    static unsigned long last_wake = 0;
    if (app.lastTouch <= last_wake) return;
    last_wake = app.lastTouch;
    app.state = STATE_PAUSED;
    app.lastAudioMs = millis();
    if (lbl_title)   lv_obj_clear_flag(lbl_title,   LV_OBJ_FLAG_HIDDEN);
    if (lbl_artist)  lv_obj_clear_flag(lbl_artist,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_state)   lv_obj_clear_flag(lbl_state,   LV_OBJ_FLAG_HIDDEN);
    if (lbl_source)  lv_obj_clear_flag(lbl_source,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_volume)  lv_obj_clear_flag(lbl_volume,  LV_OBJ_FLAG_HIDDEN);
    if (lbl_standby) lv_obj_add_flag  (lbl_standby, LV_OBJ_FLAG_HIDDEN);
}

static void wake_display()
{
    app.lastTouch = millis();  // reset dim timer
}

static void check_dim()
{
    static uint8_t target = 255;
    uint8_t new_target = (millis() - app.lastTouch > 30000) ? 140 : 255;
    if (new_target != target) target = new_target;
    if (disp_brightness == target) return;

    // Ramp: fast up (brighten quickly on event), slow down (gentle fade)
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
// Status screen
// =============================================================================
static void ui_create_status_screen()
{
    scr_status = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr_status, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr_status, LV_OPA_COVER, 0);
    lv_obj_clear_flag(scr_status, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(scr_status, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr_status, status_tap_cb, LV_EVENT_SHORT_CLICKED, NULL);

    // Helper lambda for status rows
    auto row = [](lv_obj_t *p, int y, lv_obj_t **out) {
        *out = lv_label_create(p);
        lv_label_set_text(*out, "---");
        lv_obj_set_style_text_color(*out, lv_color_white(), 0);
        lv_obj_set_style_text_font(*out, &lv_font_montserrat_18, 0);
        lv_obj_set_align(*out, LV_ALIGN_CENTER);
        lv_obj_set_y(*out, y);
        lv_obj_set_width(*out, 300);
        lv_obj_set_style_text_align(*out, LV_TEXT_ALIGN_CENTER, 0);
    };

    lv_obj_t *title = lv_label_create(scr_status);
    lv_label_set_text(title, "SYSTEM");
    lv_obj_set_style_text_color(title, lv_color_make(70, 70, 70), 0);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_letter_space(title, 5, 0);
    lv_obj_set_align(title, LV_ALIGN_TOP_MID);
    lv_obj_set_y(title, 60);

    row(scr_status, -115, &lbl_st_cpu);
    row(scr_status,  -75, &lbl_st_amp);
    row(scr_status,  -35, &lbl_st_dsp);
    row(scr_status,    5, &lbl_st_svc);
    row(scr_status,   45, &lbl_st_wifi);

    lv_obj_t *sep = lv_obj_create(scr_status);
    lv_obj_remove_style_all(sep);
    lv_obj_set_size(sep, 260, 1);
    lv_obj_set_style_bg_color(sep, lv_color_make(35, 35, 35), 0);
    lv_obj_set_style_bg_opa(sep, LV_OPA_COVER, 0);
    lv_obj_set_align(sep, LV_ALIGN_CENTER);
    lv_obj_set_y(sep, 72);

    lbl_st_song = lv_label_create(scr_status);
    lv_label_set_text(lbl_st_song, "---");
    lv_obj_set_style_text_color(lbl_st_song, lv_color_make(150, 150, 150), 0);
    lv_obj_set_style_text_font(lbl_st_song, &lv_font_montserrat_14, 0);
    lv_obj_set_align(lbl_st_song, LV_ALIGN_CENTER);
    lv_obj_set_y(lbl_st_song, 90);
    lv_obj_set_width(lbl_st_song, 290);
    lv_obj_set_style_text_align(lbl_st_song, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_long_mode(lbl_st_song, LV_LABEL_LONG_SCROLL_CIRCULAR);

    lbl_st_stale = lv_label_create(scr_status);
    lv_label_set_text(lbl_st_stale, LV_SYMBOL_WARNING " no data from Pi");
    lv_obj_set_style_text_color(lbl_st_stale, lv_color_make(255, 80, 80), 0);
    lv_obj_set_style_text_font(lbl_st_stale, &lv_font_montserrat_14, 0);
    lv_obj_set_align(lbl_st_stale, LV_ALIGN_CENTER);
    lv_obj_set_y(lbl_st_stale, 120);
    lv_obj_add_flag(lbl_st_stale, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *hint = lv_label_create(scr_status);
    lv_label_set_text(hint, "tap to return");
    lv_obj_set_style_text_color(hint, lv_color_make(45, 45, 45), 0);
    lv_obj_set_style_text_font(hint, &lv_font_montserrat_14, 0);
    lv_obj_set_align(hint, LV_ALIGN_BOTTOM_MID);
    lv_obj_set_y(hint, -50);
}

// =============================================================================
// Main screen
// Layout:
//   r=229  Volume arc (blue)       outer ring
//   r=214  Progress arc (green)    second ring
//   r=148  Energy ring (custom)    third ring — bass glow + treble flicker
//   center Title (24px, 2-line wrap)
//          Artist (18px, scroll)
//   bottom State + Source + Vol%
// =============================================================================
static void ui_create_main_screen()
{
    scr_main = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr_main, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(scr_main, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr_main, 0, 0);
    lv_obj_set_style_border_width(scr_main, 0, 0);
    lv_obj_clear_flag(scr_main, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(scr_main, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr_main, screen_tap_cb,        LV_EVENT_SHORT_CLICKED, NULL);
    lv_obj_add_event_cb(scr_main, screen_gesture_cb,    LV_EVENT_GESTURE,       NULL);
    lv_obj_add_event_cb(scr_main, screen_long_press_cb, LV_EVENT_LONG_PRESSED,  NULL);

    // ── Volume arc (outermost, blue) ──────────────────────────────────────────
    arc_volume = lv_arc_create(scr_main);
    lv_obj_add_flag(arc_volume, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(arc_volume, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(arc_volume, LCD_WIDTH - 8, LCD_HEIGHT - 8);
    lv_obj_center(arc_volume);
    lv_arc_set_range(arc_volume, 0, 100);
    lv_arc_set_value(arc_volume, app.volume);
    lv_arc_set_bg_angles(arc_volume, 135, 45);
    lv_obj_set_style_bg_opa(arc_volume, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc_volume, 6, LV_PART_MAIN);
    lv_obj_set_style_arc_color(arc_volume, lv_color_make(0, 60, 25), LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc_volume, 6, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(arc_volume, lv_color_make(0, 180, 80), LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(arc_volume, true, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(arc_volume, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_pad_all(arc_volume, 0, LV_PART_KNOB);
    lv_obj_add_event_cb(arc_volume, arc_volume_cb, LV_EVENT_VALUE_CHANGED, NULL);

    // ── Progress arc (green) ──────────────────────────────────────────────────
    arc_progress = lv_arc_create(scr_main);
    lv_obj_add_flag(arc_progress, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(arc_progress, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_size(arc_progress, LCD_WIDTH - 38, LCD_HEIGHT - 38);
    lv_obj_center(arc_progress);
    lv_arc_set_range(arc_progress, 0, 100);
    lv_arc_set_value(arc_progress, 0);
    lv_arc_set_bg_angles(arc_progress, 135, 45);
    lv_obj_set_style_bg_opa(arc_progress, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc_progress, 5, LV_PART_MAIN);
    lv_obj_set_style_arc_color(arc_progress, lv_color_make(0, 18, 7), LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc_progress, 5, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(arc_progress, lv_color_make(0, 230, 118), LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(arc_progress, true, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(arc_progress, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_pad_all(arc_progress, 0, LV_PART_KNOB);

    // ── Energy ring (custom draw, r=148) ──────────────────────────────────────
    // Size: 320x320 centered — covers only the energy ring area (r=148±20)
    // This prevents LVGL from repainting the outer arcs on every animation frame
    energy_layer = lv_obj_create(scr_main);
    lv_obj_add_flag(energy_layer, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_remove_style_all(energy_layer);
    lv_obj_set_size(energy_layer, 360, 360);
    lv_obj_center(energy_layer);
    lv_obj_set_style_bg_opa(energy_layer, LV_OPA_TRANSP, 0);
    lv_obj_clear_flag(energy_layer, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(energy_layer, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(energy_layer, energy_draw_cb, LV_EVENT_DRAW_MAIN, NULL);

    // ── Title — 2-line wrap, center ───────────────────────────────────────────
    lbl_title = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_title, lv_color_white(), 0);
    lv_obj_set_style_text_font(lbl_title, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_align(lbl_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(lbl_title, 4, 0);
    lv_obj_set_width(lbl_title, 255);
    lv_obj_set_height(lbl_title, LV_SIZE_CONTENT);
    lv_label_set_text(lbl_title, "---");
    lv_label_set_long_mode(lbl_title, LV_LABEL_LONG_WRAP);
    lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, -26);
    lv_obj_clear_flag(lbl_title, LV_OBJ_FLAG_CLICKABLE);

    // ── Artist — scrolling ────────────────────────────────────────────────────
    lbl_artist = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_artist, lv_color_make(120, 120, 120), 0);
    lv_obj_set_style_text_font(lbl_artist, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_align(lbl_artist, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_artist, 255);
    lv_label_set_text(lbl_artist, "---");
    lv_label_set_long_mode(lbl_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_artist, LV_ALIGN_CENTER, 0, 22);
    lv_obj_clear_flag(lbl_artist, LV_OBJ_FLAG_CLICKABLE);

    // ── State symbol — center, where lbl_bottom was ──────────────────────────
    lbl_state = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_state, lv_color_white(), 0);
    lv_obj_set_style_text_font(lbl_state, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_align(lbl_state, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_state, 80);
    lv_label_set_text(lbl_state, "");
    lv_obj_align(lbl_state, LV_ALIGN_CENTER, 0, 68);
    lv_obj_clear_flag(lbl_state, LV_OBJ_FLAG_CLICKABLE);

    // ── Source — above volume, near bottom ───────────────────────────────────
    lbl_source = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_source, lv_color_make(30, 215, 96), 0);
    lv_obj_set_style_text_font(lbl_source, &lv_font_montserrat_14, 0);
    lv_obj_set_style_text_letter_space(lbl_source, 2, 0);
    lv_obj_set_style_text_align(lbl_source, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_source, 200);
    lv_label_set_text(lbl_source, "");
    lv_obj_align(lbl_source, LV_ALIGN_BOTTOM_MID, 0, -42);
    lv_obj_clear_flag(lbl_source, LV_OBJ_FLAG_CLICKABLE);

    // ── Volume — very bottom ─────────────────────────────────────────────────
    lbl_volume = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_volume, lv_color_make(180, 180, 180), 0);
    lv_obj_set_style_text_font(lbl_volume, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_align(lbl_volume, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_volume, 120);
    lv_label_set_text(lbl_volume, "35%");
    lv_obj_align(lbl_volume, LV_ALIGN_BOTTOM_MID, 0, -16);
    lv_obj_clear_flag(lbl_volume, LV_OBJ_FLAG_CLICKABLE);

    // ── Gesture action feedback ───────────────────────────────────────────────
    lbl_action = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_action, lv_color_white(), 0);
    lv_obj_set_style_text_font(lbl_action, &lv_font_montserrat_32, 0);
    lv_obj_set_style_text_align(lbl_action, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_action, 300);
    lv_label_set_text(lbl_action, "");
    lv_obj_align(lbl_action, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(lbl_action, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(lbl_action, LV_OBJ_FLAG_CLICKABLE);

    // ── Standby clock ─────────────────────────────────────────────────────────
    lbl_standby = lv_label_create(scr_main);
    lv_obj_set_style_text_color(lbl_standby, lv_color_make(170, 170, 170), 0);
    lv_obj_set_style_text_font(lbl_standby, &lv_font_montserrat_48, 0);
    lv_obj_set_style_text_align(lbl_standby, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_standby, 220);
    lv_label_set_text(lbl_standby, "--:--");
    lv_obj_align(lbl_standby, LV_ALIGN_CENTER, 0, 0);
    lv_obj_add_flag(lbl_standby, LV_OBJ_FLAG_HIDDEN);
    lv_obj_clear_flag(lbl_standby, LV_OBJ_FLAG_CLICKABLE);

    lv_screen_load(scr_main);
}

// =============================================================================
// Serial parser
// =============================================================================
static void handle_serial(const String &line)
{
    if (line.length() == 0) return;

    // ── PAL: accent colour from Pi (Phase 1 wiring) ──────────────────────────
    // Applies the per-speaker accent and flags the Pi as connected. Triggers
    // boot screen → player screen transition via ScreenBoot::update().
    if (line.startsWith("PAL:")) {
        String hex = line.substring(4);
        hex.trim();
        if (hex.startsWith("#")) hex = hex.substring(1);
        if (Theme::set_accent_hex(hex.c_str())) {
            State::app.connected_to_pi = true;
        }
        return;
    }

    // Whitelist — ignore everything else (I2C errors, boot messages, etc.)
    if (!line.startsWith("ST:") &&
        !line.startsWith("SYS:") &&
        !line.startsWith("TIME:") &&
        !line.startsWith("[")) return;

    // Any recognised non-heartbeat line means the Pi is alive.
    if (!line.startsWith("[")) {
        State::app.connected_to_pi = true;
    }

    // ── SYS: system status ────────────────────────────────────────────────────
    if (line.startsWith("SYS:")) {
        last_status_rx = millis();
        String p = line.substring(4);
        auto val = [&](const char *k) -> String {
            String key = String(k) + "=";
            int i = p.indexOf(key);
            if (i < 0) return "";
            int s = i + key.length();
            int e = p.indexOf('|', s);
            return (e < 0) ? p.substring(s) : p.substring(s, e);
        };
        String cp = val("cp"); if (cp.length()) st_cpu    = cp.toFloat();
        String ht = val("ht"); if (ht.length()) st_stereo = ht;
        String hs = val("hs"); if (hs.length()) st_sub    = hs;
        String ds = val("ds"); if (ds.length()) st_dsp    = (ds == "1");
        String sv = val("sv"); if (sv.length()) st_svc    = (sv == "1");
        String wi = val("wi"); if (wi.length()) st_wifi   = wi.toInt();
        return;
    }

    // ── TIME: clock ───────────────────────────────────────────────────────────
    if (line.startsWith("TIME:")) {
        app.timeStr = line.substring(5);
        if (app.state == STATE_STANDBY && lbl_standby)
            lv_label_set_text(lbl_standby, app.timeStr.c_str());
        return;
    }

    // ── Heartbeat — ignore ────────────────────────────────────────────────────
    if (line.startsWith("[")) return;

    // ── ST: main state line ───────────────────────────────────────────────────
    String st = parse_field(line, "ST");
    String ti = parse_field(line, "TI");
    String ar = parse_field(line, "AR");
    String so = parse_field(line, "SO");
    String vo = parse_field(line, "VO");
    String po = parse_field(line, "PO");
    String du = parse_field(line, "DU");
    String lv = parse_field(line, "LV");
    String tm = parse_field(line, "TM");

    // Playback state
    if (st.length()) {
        st.toLowerCase();
        PlayState prev = app.state;

        if      (st == "play")    { app.state = STATE_PLAYING;  app.lastAudioMs = millis(); }
        else if (st == "pause")   { app.state = STATE_PAUSED; }
        else if (st == "stop")    { app.state = STATE_STOPPED; }
        else if (st == "standby") { app.state = STATE_STANDBY; }

        if (app.state == STATE_STANDBY && prev != STATE_STANDBY) {
            if (lbl_title)   lv_obj_add_flag(lbl_title,   LV_OBJ_FLAG_HIDDEN);
            if (lbl_artist)  lv_obj_add_flag(lbl_artist,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_state)   lv_obj_add_flag(lbl_state,   LV_OBJ_FLAG_HIDDEN);
            if (lbl_source)  lv_obj_add_flag(lbl_source,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_volume)  lv_obj_add_flag(lbl_volume,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_standby) {
                lv_label_set_text(lbl_standby, app.timeStr.c_str());
                lv_obj_clear_flag(lbl_standby, LV_OBJ_FLAG_HIDDEN);
            }
        } else if (app.state != STATE_STANDBY && app.state != STATE_STOPPED
                   && prev == STATE_STANDBY) {
            // Only wake display on play/pause — not on stop
            if (lbl_title)   lv_obj_clear_flag(lbl_title,   LV_OBJ_FLAG_HIDDEN);
            if (lbl_artist)  lv_obj_clear_flag(lbl_artist,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_state)   lv_obj_clear_flag(lbl_state,   LV_OBJ_FLAG_HIDDEN);
            if (lbl_source)  lv_obj_clear_flag(lbl_source,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_volume)  lv_obj_clear_flag(lbl_volume,  LV_OBJ_FLAG_HIDDEN);
            if (lbl_standby) lv_obj_add_flag  (lbl_standby, LV_OBJ_FLAG_HIDDEN);
        }
        update_bottom_label();
    }

    // Track metadata — only update if non-empty (empty = API hiccup, keep last value)
    if (ti.length() && ti != app.title) {
        app.title = ti;
        wake_display();
        if (lbl_title) lv_label_set_text(lbl_title, ti.c_str());
    }
    if (ar.length() && ar != app.artist) {
        app.artist = ar;
        if (lbl_artist) {
            lv_label_set_text(lbl_artist,
                (ar == "Unknown Artist") ? "" : ar.c_str());
        }
    }
    // Clear title/artist only when explicitly stopped AND fields are empty
    if (app.state == STATE_STOPPED && !ti.length() && !ar.length()
        && app.title.length()) {
        static unsigned long stopped_since = 0;
        if (stopped_since == 0) stopped_since = millis();
        if (millis() - stopped_since > 8000) {
            app.title  = "";
            app.artist = "";
            if (lbl_title)  lv_label_set_text(lbl_title,  "---");
            if (lbl_artist) lv_label_set_text(lbl_artist, "");
            stopped_since = 0;
        }
    } else {
        static unsigned long stopped_since = 0;
        stopped_since = 0;
    }

    // Source — never clear if we had a known source (debounce transient "none")
    if (so.length()) {
        if (so != "none") {
            if (so != app.source) {
                app.source = so;
                update_bottom_label();
            }
        } else if (app.source == "none") {
            // already none — no change needed
        }
        // so == "none" but app.source is known: ignore — transient API gap
    }

    // Volume
    if (vo.length()) {
        int v = vo.toInt();
        if (v != app.volume) {
            app.volume = v;
            update_arcs();
            update_bottom_label();
        }
    }

    // Position / duration — only update arc if values changed
    bool pos_changed = false;
    if (po.length()) {
        uint32_t new_pos = (uint32_t)po.toInt();
        if (new_pos != app.pos_ms) { app.pos_ms = new_pos; pos_changed = true; }
    }
    if (du.length()) {
        uint32_t d = (uint32_t)du.toInt();
        uint32_t new_dur = (d > 0) ? d : 1;
        if (new_dur != app.dur_ms) { app.dur_ms = new_dur; pos_changed = true; }
    }
    if (pos_changed) update_arcs();

    // Signal level
    if (lv.length()) app.energy = constrain(lv.toFloat() / 100.0f, 0.0f, 1.0f);

    // Clock
    if (tm.length()) {
        app.timeStr = tm;
        if (app.state == STATE_STANDBY && lbl_standby)
            lv_label_set_text(lbl_standby, tm.c_str());
    }
}

// =============================================================================
// Setup
// =============================================================================
void setup()
{
    Serial.begin(115200);
    unsigned long t0 = millis();
    while (!Serial && millis() - t0 < 3000) delay(10);

    esp_log_level_set("*",                ESP_LOG_WARN);
    esp_log_level_set("esp32-hal-i2c-ng", ESP_LOG_WARN);

    Serial.println("\n=== BeatBird Display v3 ===");

    flush_done_sem = xSemaphoreCreateBinary();

    // Touch I2C
    Wire.begin(TOUCH_I2C_SDA, TOUCH_I2C_SCL, 300000);
    Wire.beginTransmission(TOUCH_I2C_ADDR);
    touch_dev = (Wire.endTransmission() == 0);
    Serial.printf("Touch: %s\n", touch_dev ? "OK" : "NOT FOUND");

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
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel_handle, 0x00, 0x06));   // war: (0x06, 0x00)
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
    lv_indev_set_gesture_min_distance(indev, 20);
    lv_indev_set_long_press_time(indev, 1500);

    // Precompute bar angles (once, avoids cos/sin every frame)
    for (int i = 0; i < ENERGY_BARS; i++) {
        float angle = ((float)i / ENERGY_BARS) * 2.0f * M_PI - M_PI / 2.0f;
        bar_cos[i] = cosf(angle);
        bar_sin[i] = sinf(angle);
    }

    ui_create_main_screen();
    ui_create_status_screen();
    update_bottom_label();

    // Boot screen sits on top of main; it loads itself as the active screen
    // and animates away once State::app.connected_to_pi flips (see loop()).
    ScreenBoot::create();
    ScreenBoot::show();

    // Player screen ready, idle behind the boot screen until PAL: arrives.
    ScreenPlayer::create();          // ← NEU

    Serial.println("Ready.");
}

// =============================================================================
// Loop — single core
// =============================================================================
void loop()
{
    // Serial
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.length() > 0) handle_serial(line);
    }

    // LVGL
    // LVGL
    lv_timer_handler();

    // Boot screen: handle accent updates + transition to main when the Pi
    // connects. Cheap when boot is already done (early return).
    if (ScreenBoot::is_active()) {
            ScreenBoot::update();
            if (State::app.connected_to_pi) {
                ScreenBoot::transition_to(ScreenPlayer::root());   // ← war: scr_main
            }
    }

    // ── Mirror legacy AppState → State::app so ScreenPlayer sees fresh data.
    //    Setters are idempotent: no dirty flag set when value is unchanged.
    //    Temporary bridge until handle_serial() is replaced by Proto::poll().
    State::set_play_state ((State::PlayState)app.state);
    State::set_source     (State::source_from_string(app.source.c_str()));
    State::set_title      (app.title);
    State::set_artist     (app.artist);
    State::set_volume     (app.volume);
    State::set_position   (app.pos_ms, app.dur_ms);
    State::set_energy     (app.energy);
    State::set_clock      (app.timeStr);

    // ── Player screen draws from State::app
    ScreenPlayer::update();

    // Energy animation (every loop iteration for smooth 60fps)
    update_energy();

    // Standby / wake / dim
    check_standby();
    check_wake();
    check_dim();

    // Heartbeat
    static unsigned long last_hb = 0;
    if (millis() - last_hb > 10000) {
        last_hb = millis();
        Serial.printf("[hb] heap=%u\n", ESP.getFreeHeap());
    }

    delay(5);
}