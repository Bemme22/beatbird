// =============================================================================
// diag/panel_diag.cpp — panel addressing diagnostic (CO5300 / SH8601)
// =============================================================================
// Answers ONE question: is the bright edge stripe on RobinPi an addressing
// problem or the panel itself? Built as its own env so nothing here can
// perturb the product firmware:
//
//   pio run -e panel-diag -t upload      (board plugged into the PC, COM7)
//   pio device monitor -e panel-diag
//
// Deliberately does NOT use LVGL: it writes the panel directly through
// esp_lcd_panel_draw_bitmap, so full coverage of x=0..465 is guaranteed and
// "LVGL never invalidated that column" is excluded by construction.
//
// The test pattern paints a 2 px line of a DIFFERENT COLOUR on each edge:
//   left = red · right = green · top = blue · bottom = yellow
// plus a white ring at r=232 (the visible circle of the 1.75" panel) and a
// centre cross. Read it like this:
//   * all four coloured lines visible, ring evenly inset  → addressing is
//     correct; a stripe on top of that is the panel.
//   * one line missing and a bright stripe on that side   → content is shifted;
//     the gap is wrong. Sweep it with `g` until the line appears.
//   * ring cut off on one side                            → same, but easier
//     to see: the circle is concentric only when the window is right.
//
// Serial commands (115200, newline-terminated):
//   g<n>   set x gap 0..15      y<n>  set y gap 0..15
//   m<n>   MADCTL: 0/90/180/270, or `mn` for the panel default (no 0x36)
//   p      redraw test pattern      w  fill white       b  fill black
//   r/n/e  fill red / green / blue
//   o / O  display off / on         ?  help + current state
// =============================================================================
#include <Arduino.h>
#include <string.h>
#include <stdlib.h>

#include "driver/spi_master.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_heap_caps.h"

#include "pins.h"
#include "sh8601/esp_lcd_sh8601.h"

static esp_lcd_panel_handle_t    panel = nullptr;
static esp_lcd_panel_io_handle_t io    = nullptr;

static int  x_gap = 6;      // start value = what the product firmware uses
static int  y_gap = 0;
static int  madctl_deg = 270;

// Band buffer: one horizontal strip of the panel, DMA-capable.
static constexpr int BAND_H = 8;
static uint16_t *band = nullptr;

// ─── Panel bring-up (CO5300 init table copied from main.cpp) ────────────────

static const sh8601_lcd_init_cmd_t co5300_init_cmds[] = {
    {0x11, (uint8_t[]){0x00}, 0, 120}, // Sleep Out + 120 ms
    {0xFE, (uint8_t[]){0x00}, 1,  0},  // page/command-set select (unlock)
    {0xC4, (uint8_t[]){0x80}, 1,  0},  // SPI mode control (QSPI)
    {0x3A, (uint8_t[]){0x55}, 1,  0},  // Interface Pixel Format 16 bpp
    {0x53, (uint8_t[]){0x20}, 1,  0},  // Write CTRL Display1
    {0x63, (uint8_t[]){0xFF}, 1,  0},  // HBM brightness
    {0x51, (uint8_t[]){0xFF}, 1,  0},  // Normal-mode brightness (max)
    {0x58, (uint8_t[]){0x00}, 1,  0},  // Colour Enhancement off
    // MADCTL is NOT in the table — apply_madctl() sends it so it can be
    // changed at runtime, which is half the point of this tool.
};

static void apply_madctl()
{
    if (madctl_deg < 0) return;                      // panel default, send nothing
    uint8_t v = 0x00;
    switch (madctl_deg) {
        case 90:  v = 0xA0; break;
        case 180: v = 0xC0; break;
        case 270: v = 0x60; break;
        default:  v = 0x00; break;
    }
    esp_lcd_panel_io_tx_param(io, 0x36, &v, 1);
}

static void panel_begin()
{
    spi_bus_config_t buscfg = {};
    buscfg.mosi_io_num     = -1;
    buscfg.miso_io_num     = -1;
    buscfg.data0_io_num    = LCD_SDIO0;
    buscfg.data1_io_num    = LCD_SDIO1;
    buscfg.data2_io_num    = LCD_SDIO2;
    buscfg.data3_io_num    = LCD_SDIO3;
    buscfg.sclk_io_num     = LCD_SCLK;
    buscfg.max_transfer_sz = LCD_WIDTH * BAND_H * 2;
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));

    esp_lcd_panel_io_spi_config_t io_config = {};
    io_config.cs_gpio_num       = LCD_CS;
    io_config.dc_gpio_num       = -1;
    io_config.spi_mode          = 0;
    io_config.pclk_hz           = 40 * 1000 * 1000;
    io_config.trans_queue_depth = 10;
    io_config.lcd_cmd_bits      = 32;
    io_config.lcd_param_bits    = 8;
    io_config.flags.quad_mode   = true;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(
        (esp_lcd_spi_bus_handle_t)SPI2_HOST, &io_config, &io));

    sh8601_vendor_config_t vendor = {};
    vendor.flags.use_qspi_interface = 1;
    vendor.init_cmds      = co5300_init_cmds;
    vendor.init_cmds_size = sizeof(co5300_init_cmds) / sizeof(co5300_init_cmds[0]);

    esp_lcd_panel_dev_config_t pcfg = {};
    pcfg.reset_gpio_num = LCD_RST;
    pcfg.bits_per_pixel = 16;
    pcfg.vendor_config  = &vendor;
    pcfg.rgb_ele_order  = LCD_RGB_ELEMENT_ORDER_RGB;

    ESP_ERROR_CHECK(esp_lcd_new_panel_sh8601(io, &pcfg, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    apply_madctl();
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(panel, x_gap, y_gap));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
}

// ─── Drawing (direct, no LVGL) ──────────────────────────────────────────────

static inline uint16_t rgb565(uint8_t r, uint8_t g, uint8_t b)
{
    uint16_t c = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
    return (uint16_t)((c >> 8) | (c << 8));   // esp_lcd expects big-endian
}

static void fill(uint16_t colour)
{
    for (int i = 0; i < LCD_WIDTH * BAND_H; i++) band[i] = colour;
    for (int y = 0; y < LCD_HEIGHT; y += BAND_H) {
        int h = (y + BAND_H <= LCD_HEIGHT) ? BAND_H : (LCD_HEIGHT - y);
        esp_lcd_panel_draw_bitmap(panel, 0, y, LCD_WIDTH, y + h, band);
    }
}

// Background + one 2 px edge line per side in its own colour + ring at r=232.
static void pattern()
{
    const uint16_t BG     = rgb565(0x10, 0x10, 0x14);
    const uint16_t RING   = rgb565(0xFF, 0xFF, 0xFF);
    const uint16_t LEFT   = rgb565(0xFF, 0x00, 0x00);   // red
    const uint16_t RIGHT  = rgb565(0x00, 0xFF, 0x00);   // green
    const uint16_t TOP    = rgb565(0x30, 0x60, 0xFF);   // blue
    const uint16_t BOTTOM = rgb565(0xFF, 0xE0, 0x00);   // yellow

    const uint16_t RING_IN = rgb565(0x00, 0xE0, 0xFF);   // cyan, INSET ring

    const int   cx = LCD_WIDTH / 2, cy = LCD_HEIGHT / 2;
    const float R  = 232.0f;
    // A ring at the rim CANNOT reveal an offset: shift it and the overhanging
    // part simply falls outside the round aperture and is cropped, so it reads
    // as even either way. The mask hides exactly the error one is measuring.
    // This second ring sits well inside, where nothing crops it - an offset
    // shows immediately as an uneven gap between the two rings.
    // (RobinPi 10.09.2026: the r=232 ring read as centred while the player's
    // r=205 arc visibly did not.)
    const float R2 = 180.0f;

    for (int y0 = 0; y0 < LCD_HEIGHT; y0 += BAND_H) {
        int h = (y0 + BAND_H <= LCD_HEIGHT) ? BAND_H : (LCD_HEIGHT - y0);
        for (int dy = 0; dy < h; dy++) {
            const int y = y0 + dy;
            for (int x = 0; x < LCD_WIDTH; x++) {
                uint16_t c = BG;
                // Ring: 2 px wide, marks the visible circle of the panel.
                const float fx = x - cx + 0.5f, fy = y - cy + 0.5f;
                const float d  = sqrtf(fx * fx + fy * fy);
                if (d > R - 1.5f && d < R + 1.5f) c = RING;
                if (d > R2 - 1.5f && d < R2 + 1.5f) c = RING_IN;
                // Centre cross, so a shift is visible even without the ring.
                if ((x >= cx - 20 && x <= cx + 20 && y >= cy - 1 && y <= cy + 1) ||
                    (y >= cy - 20 && y <= cy + 20 && x >= cx - 1 && x <= cx + 1))
                    c = RING;
                // Edge lines LAST so they win over everything.
                if (x <= 1)              c = LEFT;
                if (x >= LCD_WIDTH  - 2) c = RIGHT;
                if (y <= 1)              c = TOP;
                if (y >= LCD_HEIGHT - 2) c = BOTTOM;
                band[dy * LCD_WIDTH + x] = c;
            }
        }
        esp_lcd_panel_draw_bitmap(panel, 0, y0, LCD_WIDTH, y0 + h, band);
    }
}


// Colour ruler: 8 px bands from each edge inwards, so ONE photo gives the
// exact offset. Whichever band is the first fully visible one at an edge says
// how many columns are being cut: red=0-7 green=8-15 blue=16-23 yellow=24-31
// magenta=32-39 cyan=40-47 white=48-55 orange=56-63.
static void ruler()
{
    const uint16_t BG = rgb565(0x10, 0x10, 0x14);
    static const uint16_t band_col[8] = {
        rgb565(0xFF,0x00,0x00), rgb565(0x00,0xFF,0x00), rgb565(0x30,0x60,0xFF),
        rgb565(0xFF,0xE0,0x00), rgb565(0xFF,0x00,0xFF), rgb565(0x00,0xE0,0xFF),
        rgb565(0xFF,0xFF,0xFF), rgb565(0xFF,0x80,0x00),
    };
    for (int y0 = 0; y0 < LCD_HEIGHT; y0 += BAND_H) {
        int h = (y0 + BAND_H <= LCD_HEIGHT) ? BAND_H : (LCD_HEIGHT - y0);
        for (int dy = 0; dy < h; dy++) {
            for (int x = 0; x < LCD_WIDTH; x++) {
                uint16_t c = BG;
                if (x < 64)                    c = band_col[x >> 3];
                else if (x >= LCD_WIDTH - 64)  c = band_col[(LCD_WIDTH - 1 - x) >> 3];
                band[dy * LCD_WIDTH + x] = c;
            }
        }
        esp_lcd_panel_draw_bitmap(panel, 0, y0, LCD_WIDTH, y0 + h, band);
    }
}

// Four horizontal zones, each drawing the SAME colour ruler but with the
// content shifted left by a different amount: top→bottom 0, 8, 16, 24 px.
// One photo then answers "how far off are we": the zone whose LEFT edge starts
// cleanly with the red band is the required offset. Zones sit in y=116..356
// because that is the only band where the round aperture still shows x<64.
static void zones()
{
    const uint16_t BG    = rgb565(0x10, 0x10, 0x14);
    const uint16_t SEP   = rgb565(0xFF, 0xFF, 0xFF);
    static const uint16_t band_col[8] = {
        rgb565(0xFF,0x00,0x00), rgb565(0x00,0xFF,0x00), rgb565(0x30,0x60,0xFF),
        rgb565(0xFF,0xE0,0x00), rgb565(0xFF,0x00,0xFF), rgb565(0x00,0xE0,0xFF),
        rgb565(0xFF,0xFF,0xFF), rgb565(0xFF,0x80,0x00),
    };
    const int Y0 = 116, ZH = 60, NZ = 4;

    for (int y0 = 0; y0 < LCD_HEIGHT; y0 += BAND_H) {
        int h = (y0 + BAND_H <= LCD_HEIGHT) ? BAND_H : (LCD_HEIGHT - y0);
        for (int dy = 0; dy < h; dy++) {
            const int y    = y0 + dy;
            const int zone = (y - Y0) / ZH;
            const bool in  = (y >= Y0 && zone >= 0 && zone < NZ);
            const int  off = in ? zone * 8 : -1;
            for (int x = 0; x < LCD_WIDTH; x++) {
                uint16_t c = BG;
                if (off >= 0) {
                    const int xr = x + off;          // content shifted LEFT
                    if (xr >= 0 && xr < 64)                   c = band_col[xr >> 3];
                    else if (xr >= LCD_WIDTH - 64 && xr < LCD_WIDTH)
                                                              c = band_col[(LCD_WIDTH - 1 - xr) >> 3];
                    if (((y - Y0) % ZH) < 2)                  c = SEP;
                }
                band[dy * LCD_WIDTH + x] = c;
            }
        }
        esp_lcd_panel_draw_bitmap(panel, 0, y0, LCD_WIDTH, y0 + h, band);
    }
}

// ─── Auto-sweep with an on-screen read-out ──────────────────────────────────
// Steps x_gap and paints the ruler plus the CURRENT VALUE as six binary blocks
// in the middle (MSB left, filled = 1). One photo taken at the moment the edge
// closes therefore carries its own measurement — no counting frames, no
// guessing which serial line belonged to which picture.
static bool     sweeping   = false;
static bool     sweep_y    = false;   // MV swaps the axes: for a vertical
                                      // stripe the PAGE address is the knob
static uint32_t sweep_next = 0;

static void draw_value(int v)
{
    const uint16_t ON  = rgb565(0xFF, 0xFF, 0xFF);
    const uint16_t OFF = rgb565(0x28, 0x28, 0x30);
    const int BW = 40, BH = 40, GAP = 10, N = 6;
    const int total = N * BW + (N - 1) * GAP;
    const int x0 = (LCD_WIDTH - total) / 2, y0 = (LCD_HEIGHT - BH) / 2;

    for (int i = 0; i < N; i++) {
        const bool bit = (v >> (N - 1 - i)) & 1;
        for (int k = 0; k < BW * BH; k++) band[k] = bit ? ON : OFF;
        esp_lcd_panel_draw_bitmap(panel, x0 + i * (BW + GAP), y0,
                                  x0 + i * (BW + GAP) + BW, y0 + BH, band);
    }
}

static void sweep_step()
{
    if (!sweeping) return;
    const uint32_t now = millis();
    if (now < sweep_next) return;
    sweep_next = now + 2500;

    int &g = sweep_y ? y_gap : x_gap;
    g += 2;
    if (g > 40) g = 0;
    esp_lcd_panel_set_gap(panel, x_gap, y_gap);
    ruler();
    draw_value(g);
    Serial.printf("[sweep] %s_gap=%d\n", sweep_y ? "y" : "x", g);
}
// ─── Serial console ─────────────────────────────────────────────────────────

static void status()
{
    Serial.printf("[diag] x_gap=%d y_gap=%d madctl=%s\n",
                  x_gap, y_gap,
                  madctl_deg < 0 ? "native" : String(madctl_deg).c_str());
}

static void help()
{
    Serial.println(F("g<n> x-gap | y<n> y-gap | m0/m90/m180/m270/mn MADCTL"));
    Serial.println(F("p pattern | s ruler | w white | b black | r red | n green | e blue"));
    Serial.println(F("o off | O on | ? status"));
    Serial.println(F("Pattern: left=RED right=GREEN top=BLUE bottom=YELLOW,"));
    Serial.println(F("ring r=232. All four lines visible + ring even => the"));
    Serial.println(F("addressing is right and any extra stripe is the panel."));
    status();
}

static void handle(char *s)
{
    switch (s[0]) {
        case 'g': x_gap = atoi(s + 1); esp_lcd_panel_set_gap(panel, x_gap, y_gap); pattern(); status(); break;
        case 'y': y_gap = atoi(s + 1); esp_lcd_panel_set_gap(panel, x_gap, y_gap); pattern(); status(); break;
        case 'm':
            madctl_deg = (s[1] == 'n') ? -1 : atoi(s + 1);
            apply_madctl(); pattern(); status();
            break;
        case 'p': pattern(); break;
        case 's': ruler();   break;
        case 'z': zones();   break;
        case 'A': sweeping = true; sweep_y = false; x_gap = -2; sweep_next = 0; Serial.println("[diag] sweep X"); break;
        case 'B': sweeping = true; sweep_y = true;  x_gap = 0; y_gap = -2; sweep_next = 0; Serial.println("[diag] sweep Y"); break;
        case 'X': sweeping = false; Serial.println("[diag] sweep off"); status(); break;
        case 'w': fill(rgb565(0xFF, 0xFF, 0xFF)); break;
        case 'b': fill(rgb565(0x00, 0x00, 0x00)); break;
        case 'r': fill(rgb565(0xFF, 0x00, 0x00)); break;
        case 'n': fill(rgb565(0x00, 0xFF, 0x00)); break;
        case 'e': fill(rgb565(0x00, 0x00, 0xFF)); break;
        case 'o': esp_lcd_panel_disp_on_off(panel, false); Serial.println("[diag] display OFF"); break;
        case 'O': esp_lcd_panel_disp_on_off(panel, true);  Serial.println("[diag] display ON");  break;
        default:  help(); break;
    }
}

void setup()
{
    Serial.begin(115200);
    delay(400);
    Serial.println("\n=== BeatBird panel diagnostic (CO5300 / SH8601) ===");

    band = (uint16_t *)heap_caps_malloc(LCD_WIDTH * BAND_H * 2, MALLOC_CAP_DMA);
    if (!band) { Serial.println("FATAL: band alloc failed"); while (1) delay(100); }

    panel_begin();
    Serial.println("[diag] panel up");
    pattern();
    help();
}

void loop()
{
    sweep_step();
    static char line[32];
    static uint8_t pos = 0;
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') { if (pos) { line[pos] = 0; handle(line); pos = 0; } }
        else if (pos < sizeof(line) - 1) line[pos++] = c;
    }
    delay(5);
}
