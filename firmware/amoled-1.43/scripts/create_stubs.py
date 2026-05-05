Import("env")
import os, re

libdeps = os.path.join(env.subst("$PROJECT_LIBDEPS_DIR"), env.subst("$PIOENV"))
gfx_src = os.path.join(libdeps, "GFX Library for Arduino", "src")

# ── 1. SPI stubs (esp32-hal-periman.h + esp_private/periph_ctrl.h) ──────────
# Arduino_ESP32SPI.h includes these headers (from Arduino-ESP32 2.x era)
# but they don't exist in the installed SDK. The project uses QSPI, not SPI,
# so stubs are safe — no periman/periph_ctrl symbols are actually called.
databus_dir = os.path.join(gfx_src, "databus")
if os.path.isdir(databus_dir):
    stub = "// Stub — symbols not used in this project\n#pragma once\n"

    periman = os.path.join(databus_dir, "esp32-hal-periman.h")
    if not os.path.exists(periman):
        with open(periman, "w") as f: f.write(stub)
        print("create_stubs: created esp32-hal-periman.h")

    esp_private = os.path.join(databus_dir, "esp_private")
    os.makedirs(esp_private, exist_ok=True)
    periph_ctrl = os.path.join(esp_private, "periph_ctrl.h")
    if not os.path.exists(periph_ctrl):
        with open(periph_ctrl, "w") as f: f.write(stub)
        print("create_stubs: created esp_private/periph_ctrl.h")

# ── 2. RGBPanel getFrameBuffer patch ─────────────────────────────────────────
# Arduino_ESP32RGBPanel.cpp::getFrameBuffer() uses esp_rgb_panel_t (2.x branch)
# or esp_lcd_rgb_panel_get_frame_buffer (3.x branch) — neither exists in the
# installed SDK headers. Patch both branches to return nullptr; since this
# project never uses the RGB panel.
rgb_cpp = os.path.join(databus_dir, "Arduino_ESP32RGBPanel.cpp")
if os.path.exists(rgb_cpp):
    with open(rgb_cpp, "r") as f:
        src = f.read()

    # Replace the problematic #if/else/endif block inside getFrameBuffer
    old = (
        "#if (!defined(ESP_ARDUINO_VERSION_MAJOR)) || (ESP_ARDUINO_VERSION_MAJOR < 3)\n"
        "  esp_rgb_panel_t *_rgb_panel;\n"
        "  _rgb_panel = __containerof(_panel_handle, esp_rgb_panel_t, base);\n"
        "\n"
        "  return (uint16_t *)_rgb_panel->fb;\n"
        "#else\n"
        "  void *frame_buffer = nullptr;\n"
        "  ESP_ERROR_CHECK(esp_lcd_rgb_panel_get_frame_buffer(_panel_handle, 1, &frame_buffer));\n"
        "\n"
        "  return ((uint16_t *)frame_buffer);\n"
        "#endif"
    )
    replacement = "  return nullptr; // patched: RGB panel not used in this project"

    if old in src:
        patched = src.replace(old, replacement)
        with open(rgb_cpp, "w") as f:
            f.write(patched)
        print("create_stubs: patched Arduino_ESP32RGBPanel.cpp getFrameBuffer()")
    elif "return nullptr; // patched" in src:
        print("create_stubs: Arduino_ESP32RGBPanel.cpp already patched")
    else:
        print("create_stubs: WARNING — could not find getFrameBuffer block to patch")
