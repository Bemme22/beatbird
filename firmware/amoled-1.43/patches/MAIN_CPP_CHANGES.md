# main.cpp — Phase 2 Step 1 edits

Four surgical edits to wire the boot screen + PAL accent into the existing
main.cpp. None of the existing UI/touch/protocol behaviour changes.

If a chunk doesn't match exactly, use the surrounding context comments — the
edits are intentionally small and local.

---

## Edit 1 — Add the screen header include

**Find:**

```cpp
#include "pins.h"
#include "esp_log.h"
```

**Replace with:**

```cpp
#include "pins.h"
#include "esp_log.h"
#include "state.h"
#include "theme.h"
#include "screens/screen_boot.h"
```

---

## Edit 2 — Handle `PAL:` and set `connected_to_pi`

**Find** the top of `handle_serial()`:

```cpp
static void handle_serial(const String &line)
{
    if (line.length() == 0) return;

    // Whitelist — ignore everything else (I2C errors, boot messages, etc.)
    if (!line.startsWith("ST:") &&
        !line.startsWith("SYS:") &&
        !line.startsWith("TIME:") &&
        !line.startsWith("[")) return;
```

**Replace with:**

```cpp
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
```

---

## Edit 3 — Create + show the boot screen at end of setup()

**Find** near the end of `setup()`:

```cpp
    ui_create_main_screen();
    ui_create_status_screen();
    update_bottom_label();

    Serial.println("Ready.");
}
```

**Replace with:**

```cpp
    ui_create_main_screen();
    ui_create_status_screen();
    update_bottom_label();

    // Boot screen sits on top of main; it loads itself as the active screen
    // and animates away once State::app.connected_to_pi flips (see loop()).
    ScreenBoot::create();
    ScreenBoot::show();

    Serial.println("Ready.");
}
```

---

## Edit 4 — Drive the boot → player transition in loop()

**Find** in `loop()`:

```cpp
    // LVGL
    lv_timer_handler();
```

**Replace with:**

```cpp
    // LVGL
    lv_timer_handler();

    // Boot screen: handle accent updates + transition to main when the Pi
    // connects. Cheap when boot is already done (early return).
    if (ScreenBoot::is_active()) {
        ScreenBoot::update();
        if (State::app.connected_to_pi) {
            ScreenBoot::transition_to(scr_main);
        }
    }
```

---

## Verify

After applying the 4 edits, the file should still be ~720 lines (4–8 lines
added net). Compile-check before flashing:

```bash
cd firmware/amoled-1.43
pio run
```

Expected: clean build, no new warnings. The display will show the boot
screen on power-up until the Bridge sends its first ST: or SYS: line.

## Optional cleanup (later)

These can stay as-is for Step 1 — they'll be addressed when main.cpp is
properly broken up into the orchestrator pattern:

- The legacy `parse_field()` String-based parser is now duplicated by
  `Proto::parse_field()` in proto/serial_rx.cpp. Old one stays for now.
- The flat AppState struct in main.cpp duplicates State::app. Migrating
  the screens to read from State::app is a Phase 2 Step 2 task.
