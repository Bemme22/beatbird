#pragma once
// =============================================================================
// BeatBird Display - Pin Definitions
// =============================================================================
// Two supported boards, selected at compile time by the PlatformIO env:
//   default            → Waveshare ESP32-S3-Touch-AMOLED-1.43 (SH8601 + FT6x36)
//   -DBOARD_AMOLED_175 → Waveshare ESP32-S3-Touch-AMOLED-1.75 (CO5300 + CST9217
//                        + AXP2101 PMIC).  Same 466×466 panel resolution, so the
//                        whole UI/LVGL/app layer is shared — only the low-level
//                        panel driver, touch IC and power path differ.
// Pins verified against the official Waveshare demos (user_config.h /
// Mylibrary/pin_config.h).
// =============================================================================

#if defined(BOARD_AMOLED_175)
// ---- Waveshare ESP32-S3-Touch-AMOLED-1.75 -----------------------------------
// Display: CO5300 via QSPI (reuses the generic SH8601 esp_lcd driver)
#define LCD_CS          12  // OLED CS  - GPIO12
#define LCD_SCLK        38  // OLED CLK - GPIO38
#define LCD_SDIO0        4  // OLED D0  - GPIO4
#define LCD_SDIO1        5  // OLED D1  - GPIO5
#define LCD_SDIO2        6  // OLED D2  - GPIO6
#define LCD_SDIO3        7  // OLED D3  - GPIO7
#define LCD_RST         39  // OLED RESET - GPIO39
// NOTE: no LCD_EN GPIO — panel power is gated by the AXP2101 PMIC over I2C.

// ONE shared I2C bus (GPIO15/14): touch + AXP2101 + IMU + RTC.
#define TOUCH_I2C_SDA   15  // shared SDA - GPIO15
#define TOUCH_I2C_SCL   14  // shared SCL - GPIO14
#define TOUCH_INT       11  // CST9217 INT  - GPIO11
#define TOUCH_RST       40  // CST9217 RST  - GPIO40
#define TOUCH_I2C_ADDR  0x5A // CST9217 (TODO Step 2: verify addr + read protocol)
#define MAIN_I2C_SDA    15
#define MAIN_I2C_SCL    14
#define PMU_I2C_ADDR    0x34 // AXP2101 PMIC (powers the AMOLED rail)

#else
// ---- Waveshare ESP32-S3-Touch-AMOLED-1.43 (default) -------------------------
// Display: SH8601 via QSPI (verified working)
#define LCD_CS          9   // OLED CS - GPIO9
#define LCD_SCLK        10  // OLED CLK - GPIO10 (PCLK)
#define LCD_SDIO0       11  // OLED D0 - GPIO11
#define LCD_SDIO1       12  // OLED D1 - GPIO12
#define LCD_SDIO2       13  // OLED D2 - GPIO13
#define LCD_SDIO3       14  // OLED D3 - GPIO14
#define LCD_RST         21  // OLED RESET - GPIO21
#define LCD_EN          42  // OLED EN - GPIO42 (enable control)

// TWO separate I2C buses: Main (8/18, TCA9554) + Touch (47/48, FT6x36)
#define MAIN_I2C_SDA    18  // Main I2C SDA - GPIO18
#define MAIN_I2C_SCL     8  // Main I2C SCL - GPIO8
#define TOUCH_I2C_SDA    47  // Touch SDA - GPIO47
#define TOUCH_I2C_SCL    48  // Touch SCL - GPIO48
#define TOUCH_INT       -1  // -1 = polling mode (no interrupt pin used)
#define TOUCH_RST       -1  // -1 = no reset pin control
#define TOUCH_I2C_ADDR  0x38 // FT6x36 touch controller address

#endif

#define LCD_WIDTH      466
#define LCD_HEIGHT     466

// --- QMI8658 IMU ---
#define IMU_I2C_ADDR    0x6B

// --- PCF85063 RTC ---
#define RTC_I2C_ADDR    0x51

// --- Buttons ---
#define BTN_BOOT         0   // BOOT button (active low)
// PWR button is handled by power management, not GPIO

// --- TF Card (SPI) ---
// (Optional, for loading assets from SD)
// #define SD_CS          ...
// #define SD_MOSI        ...
// #define SD_MISO        ...
// #define SD_SCLK        ...
