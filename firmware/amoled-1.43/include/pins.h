#pragma once
// =============================================================================
// BeatBird Display - Pin Definitions
// Board: Waveshare ESP32-S3-Touch-AMOLED-1.43
// =============================================================================
// Verifiziert gegen offiziellen Waveshare Demo-Code (user_config.h)
// =============================================================================

// --- Display: SH8601 via QSPI ---
// Waveshare ESP32-S3-Touch-AMOLED-1.43 pin assignments (verified working)
#define LCD_CS          9   // OLED CS - GPIO9
#define LCD_SCLK        10  // OLED CLK - GPIO10 (PCLK)
#define LCD_SDIO0       11  // OLED D0 - GPIO11
#define LCD_SDIO1       12  // OLED D1 - GPIO12
#define LCD_SDIO2       13  // OLED D2 - GPIO13
#define LCD_SDIO3       14  // OLED D3 - GPIO14
#define LCD_RST         21  // OLED RESET - GPIO21
#define LCD_EN          42  // OLED EN - GPIO42 (enable control)

#define LCD_WIDTH      466
#define LCD_HEIGHT     466

// --- I2C Buses ---
// Waveshare ESP32-S3-Touch-AMOLED-1.43 has TWO separate I2C buses:
// 1. Main I2C (GPIO 8/18) - for TCA9554 I/O expander and other devices
// 2. Touch I2C (GPIO 47/48) - dedicated for FT6x36 touch controller

// Main I2C Bus (for TCA9554 I/O expander)
#define MAIN_I2C_SDA    18  // Main I2C SDA - GPIO18
#define MAIN_I2C_SCL     8  // Main I2C SCL - GPIO8

// Touch I2C Bus (dedicated for FT6x36 touch controller)
#define TOUCH_I2C_SDA    47  // Touch SDA - GPIO47
#define TOUCH_I2C_SCL    48  // Touch SCL - GPIO48
#define TOUCH_INT       -1  // -1 = polling mode (no interrupt pin used)
#define TOUCH_RST       -1  // -1 = no reset pin control
#define TOUCH_I2C_ADDR  0x38 // FT6x36 touch controller address

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
