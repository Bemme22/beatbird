// =============================================================================
// src/sim/sim_main.cpp — Desktop simulator entrypoint
// =============================================================================
// Builds the same UI tree (boot / player / standby screens) the real ESP32
// firmware does, but draws through LVGL's SDL backend in an x86 window so
// we can iterate on screens without flashing the device every change.
//
// What this file owns that main.cpp owns on hardware:
//   - LVGL display setup (lv_sdl_window_create instead of esp_lcd_panel_*)
//   - LVGL input setup (lv_sdl_mouse acts as the touch driver)
//   - Tick + timer pump (lv_timer_handler in the main loop)
//   - Screen lifecycle: create boot → fade-in → drive demo state
//   - Definition of the global Serial / ESP shim instances declared in
//     arduino_shim.h
//
// What this file does NOT own that main.cpp does:
//   - Panel init (SH8601 init sequence, MADCTL, set_gap)
//   - Touch I²C
//   - IMU + flush semaphores
//   - USB-CDC + real-Serial input pump
//
// Protocol input for the demo: a tiny scripted timeline (boot → palette →
// play track → standby) feeds Proto::handle_line() the same lines the
// bridge would normally send. Later we'll point a real PTY at the bridge.
// =============================================================================

#include "screens/screen_boot.h"
#include "screens/screen_player.h"
#include "screens/screen_standby.h"
#include "proto.h"
#include "state.h"
#include "theme.h"
#include "sim/arduino_shim.h"

#include <lvgl.h>
#include <SDL.h>

#include <cstdio>
#include <cstring>
#include <chrono>
#include <thread>

// ─── Global instances declared `extern` in arduino_shim.h ───────────────────

HardwareSerialShim Serial;
EspShim            ESP;

// ─── LVGL tick source ───────────────────────────────────────────────────────
// SDL provides high-precision tick counts; LVGL needs to know elapsed ms.
// Registered via lv_tick_set_cb so LVGL queries us instead of using its
// own internal counter.
static uint32_t tick_cb(void) {
    return SDL_GetTicks();
}

// ─── Demo timeline ──────────────────────────────────────────────────────────
// Feeds the protocol parser the same lines the bridge sends, so screens
// transition through realistic states. Each entry is { delay_after_prev_ms,
// "line" }. NULL line ends the timeline (we'll just keep refreshing UI then).

struct DemoStep {
    uint32_t    delay_ms;
    const char *line;
};

static const DemoStep demo[] = {
    // After boot screen fades in, push palette + a Spotify track.
    {  500,  "PAL:F0CB7B"                                                                                  },
    { 1500,  "TIME:14:32"                                                                                  },
    {  200,  "WX:t=18|c=1|h=22|l=11"                                                                       },
    { 2000,  "SYS:cp=21.5|hstereo=ok|hsub=ok|ds=1|sv=1|wi=-58|gw=1|ss=0"                                   },
    {  500,  "ST:play|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:42|PO:32000|DU:240000|LV:35|TM:14:32"     },
    {  500,  "FX:30,40,55,72,80,90,75,68,55,40,30,20"                                                      },
    // Move into a longer track to exercise scroll + flap.
    { 8000,  "ST:play|TI:Methodisch inkorrekt - Folge 220|AR:Mi220|SO:spotify|VO:42|PO:0|DU:6000000|LV:18" },
    // Pause for a beat.
    { 6000,  "ST:pause|TI:Methodisch inkorrekt - Folge 220|AR:Mi220|SO:spotify|VO:42|PO:42000|DU:6000000|LV:0" },
    // Then "stop" → bridge would push source=none + standby trigger.
    { 4000,  "ST:stop|TI:|AR:|SO:none|VO:42|PO:0|DU:1|LV:0"                                                },
    // Push some idle messages (split-flap on standby).
    { 1000,  "STBY:BEREIT WENN DU WILLST"                                                                  },
    { 6000,  "STBY:NIX LOS HIER"                                                                           },
    { 6000,  "STBY:404 SOUND FEHLT"                                                                        },
    {     0, NULL                                                                                          },
};

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    // ─── LVGL init ──────────────────────────────────────────────────────────
    lv_init();
    lv_tick_set_cb(tick_cb);

    // SDL display matches the AMOLED panel resolution (466×466 round).
    lv_display_t *disp = lv_sdl_window_create(466, 466);
    if (!disp) {
        fprintf(stderr, "lv_sdl_window_create failed\n");
        return 1;
    }
    lv_sdl_window_set_title(disp, "BeatBird Display Simulator");

    // SDL mouse → touch input. Drag/click maps to LV_INDEV_TYPE_POINTER which
    // is what the on-screen touch handlers expect.
    lv_indev_t *mouse = lv_sdl_mouse_create();
    (void)mouse;

    // ─── Build all screens once, just like main.cpp's setup() does. ─────────
    ScreenBoot::create();
    ScreenBoot::show();
    ScreenPlayer::create();
    ScreenStandby::create();

    // ─── Drive the demo timeline + LVGL tick loop ───────────────────────────
    Proto::send_version();   // mirrors what real firmware does on boot
    Proto::send_boot_marker();

    uint32_t demo_idx = 0;
    uint32_t next_step_ms = SDL_GetTicks() + demo[0].delay_ms;
    bool     boot_transitioned = false;

    while (true) {
        const uint32_t now = SDL_GetTicks();

        // 1. Boot → Player transition once the first state line lands.
        //    State::app.connected_to_pi is set inside handle_line(); the
        //    boot screen exits as soon as it's true (mirrors real-firmware
        //    behaviour from main.cpp's loop).
        if (!boot_transitioned && State::app.connected_to_pi) {
            ScreenBoot::transition_to(ScreenPlayer::root());
            boot_transitioned = true;
        }

        // 2. Demo step → feed a line through the same parser the real
        //    firmware uses. After the last step, just keep ticking.
        if (demo[demo_idx].line && (int32_t)(now - next_step_ms) >= 0) {
            printf("[sim] line: %s\n", demo[demo_idx].line);
            Proto::handle_line(demo[demo_idx].line);
            demo_idx++;
            if (demo[demo_idx].line) {
                next_step_ms = now + demo[demo_idx].delay_ms;
            }
        }

        // 3. Per-frame screen updates (each screen's update() reads the
        //    Dirty bits set by handle_line() and repaints what changed).
        ScreenBoot::update();
        ScreenPlayer::update();
        ScreenStandby::update();

        // 4. LVGL pump — runs the SDL event loop + render.
        lv_timer_handler();

        // 5. Cap at ~60 fps.
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
    }

    return 0;
}
