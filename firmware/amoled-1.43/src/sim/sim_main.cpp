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
// Driver flow:
//   1. Initial scripted timeline kicks the display through boot → palette →
//      play track → pause → stop → standby.
//   2. After that the main loop just keeps SYS: heartbeats flowing so
//      PI OFFLINE doesn't latch.
//   3. A stdin reader thread accepts named scenarios (`:play`, `:offline`)
//      and raw protocol lines so you can drive arbitrary states by hand.
//      Type `:help` while the sim is running for the command list.
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

#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <mutex>
#include <queue>
#include <string>
#include <thread>

// POSIX socket API for the optional TCP control listener (port 7777). The web
// UI in scripts/sim_web.py sends protocol lines + scenario commands over this.
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

// ─── Global instances declared `extern` in arduino_shim.h ───────────────────

HardwareSerialShim Serial;
EspShim            ESP;

// ─── LVGL tick source ───────────────────────────────────────────────────────
static uint32_t tick_cb(void) {
    return SDL_GetTicks();
}

// ─── Scripted boot timeline ─────────────────────────────────────────────────
// One-shot lines that fire on startup to get the display past boot into a
// realistic running state. After the last entry plays, the main loop takes
// over with a SYS heartbeat and whatever the user types on stdin.

struct DemoStep {
    uint32_t    delay_ms;
    const char *line;
};

static const DemoStep demo[] = {
    {  500,  "PAL:F0CB7B"                                                                              },
    { 1500,  "TIME:14:32"                                                                              },
    {  200,  "WX:t=18|c=1|h=22|l=11"                                                                   },
    {  300,  "SYS:cp=21.5|hstereo=ok|hsub=ok|ds=1|sv=1|wi=-58|gw=1|ss=0"                               },
    {  500,  "ST:play|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:42|PO:32000|DU:240000|LV:35|TM:14:32" },
    {  500,  "FX:30,40,55,72,80,90,75,68,55,40,30,20"                                                  },
    {     0, NULL                                                                                       },
};

// ─── Stdin command queue ────────────────────────────────────────────────────
// Reader thread pushes typed lines; main loop drains them inside the LVGL
// frame. Mutex because LVGL is not thread-safe.

static std::mutex          cmd_mu;
static std::queue<std::string> cmd_queue;
static std::atomic<bool>   stop_reader{false};

static void stdin_reader() {
    std::string line;
    while (!stop_reader.load()) {
        if (!std::getline(std::cin, line)) break;
        if (line.empty()) continue;
        std::lock_guard<std::mutex> g(cmd_mu);
        cmd_queue.push(line);
    }
}

// ─── Optional TCP control listener (port 7777) ──────────────────────────────
// Accepts newline-separated commands — same grammar as stdin, both `:scenario`
// shortcuts and raw protocol lines. Used by scripts/sim_web.py so a browser
// on a different host can drive the sim. Multiple clients allowed.

static constexpr int CTRL_PORT = 7777;

static void tcp_listener() {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { perror("socket"); return; }
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(CTRL_PORT);
    if (bind(srv, (sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("bind"); close(srv); return;
    }
    if (listen(srv, 4) < 0) { perror("listen"); close(srv); return; }
    printf("[sim] TCP control listening on :%d\n", CTRL_PORT); fflush(stdout);

    while (!stop_reader.load()) {
        sockaddr_in cli{}; socklen_t cl = sizeof(cli);
        int fd = accept(srv, (sockaddr*)&cli, &cl);
        if (fd < 0) { if (errno == EINTR) continue; perror("accept"); break; }
        // Per-client read until close. Keep it lockstep — one client doing
        // a quick POST won't block another, and we don't expect high QPS.
        std::string buf;
        char chunk[256];
        ssize_t n;
        while ((n = read(fd, chunk, sizeof(chunk))) > 0) {
            buf.append(chunk, n);
            size_t p;
            while ((p = buf.find('\n')) != std::string::npos) {
                std::string line = buf.substr(0, p);
                buf.erase(0, p + 1);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                if (!line.empty()) {
                    std::lock_guard<std::mutex> g(cmd_mu);
                    cmd_queue.push(line);
                }
            }
        }
        close(fd);
    }
    close(srv);
}

static void print_help() {
    printf(
        "\n"
        "  BeatBird sim — commands\n"
        "  ----------------------------------------------------------\n"
        "  :help           show this list\n"
        "  :quit / :q      exit\n"
        "  :play           start a Spotify track playing\n"
        "  :pause          pause the current track\n"
        "  :stop           stop / no track loaded\n"
        "  :standby        force idle → standby screen (clock+weather+flap)\n"
        "  :wake           leave standby\n"
        "  :offline        SYS:sv=0 — go-librespot down → SPOTIFY OFFLINE\n"
        "  :reconnect      ss=1 — stuck-restart fired → RECONNECTING\n"
        "  :no-network     gw=0 → NO NETWORK\n"
        "  :weak-wifi      wi=-90 → WIFI WEAK\n"
        "  :healthy        sv=1 ss=0 gw=1 wi=-58 (reset all alerts)\n"
        "  :next           Spotify NEXT command (split-flap to new title)\n"
        "  :flap TEXT      push a custom standby flap line\n"
        "  ----------------------------------------------------------\n"
        "  Anything not starting with ':' is treated as a raw protocol line,\n"
        "  e.g.  ST:play|TI:Hello|AR:World|SO:spotify|VO:42|PO:0|DU:200000|LV:20\n"
        "\n"
    );
    fflush(stdout);
}

// Sends a synthetic protocol line and logs it for visibility.
static void inject(const char *line) {
    printf("[sim] >> %s\n", line);
    fflush(stdout);
    Proto::handle_line(line);
}

// Tracks last-sent flag values so a partial change (e.g. :offline) keeps the
// other fields intact. Bridge-side `_push_system_now` always pushes the full
// SYS line — mirror that.
struct SimSys {
    float cpu = 21.5f;
    int   wi  = -58;
    int   ds  = 1;
    int   sv  = 1;
    int   gw  = 1;
    int   ss  = 0;
    std::string format() const {
        char buf[160];
        snprintf(buf, sizeof(buf),
                 "SYS:cp=%.1f|hstereo=ok|hsub=ok|ds=%d|sv=%d|wi=%d|gw=%d|ss=%d",
                 cpu, ds, sv, wi, gw, ss);
        return buf;
    }
};
static SimSys sim_sys;

static void send_sys() { inject(sim_sys.format().c_str()); }

static void handle_command(const std::string &raw) {
    std::string s = raw;
    while (!s.empty() && (s.back() == '\r' || s.back() == '\n' || s.back() == ' ')) s.pop_back();
    if (s.empty()) return;

    if (s[0] != ':') { inject(s.c_str()); return; }

    if      (s == ":help" || s == ":?")             { print_help(); }
    else if (s == ":quit" || s == ":q")             { stop_reader = true; exit(0); }
    else if (s == ":play") {
        inject("ST:play|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:42|PO:32000|DU:240000|LV:35|TM:14:32");
    }
    else if (s == ":pause") {
        inject("ST:pause|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:42|PO:32000|DU:240000|LV:0|TM:14:32");
    }
    else if (s == ":stop") {
        inject("ST:stop|TI:|AR:|SO:none|VO:42|PO:0|DU:1|LV:0|TM:14:32");
    }
    else if (s == ":standby") {
        // `ST:standby` is the firmware-state token that routes screen_player's
        // update() to show_standby_mode() → ScreenStandby::show(). `ST:stop`
        // would stay on the empty player screen, which is `:stop` below.
        inject("ST:standby|TI:|AR:|SO:none|VO:42|PO:0|DU:1|LV:0|TM:14:32");
        inject("STBY:BEREIT WENN DU WILLST");
    }
    else if (s == ":wake") {
        inject("ST:play|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:42|PO:0|DU:240000|LV:30|TM:14:32");
    }
    else if (s == ":offline")    { sim_sys.sv = 0; send_sys(); }
    else if (s == ":reconnect")  { sim_sys.ss = 1; sim_sys.sv = 1; send_sys(); }
    else if (s == ":no-network") { sim_sys.gw = 0; send_sys(); }
    else if (s == ":weak-wifi")  { sim_sys.wi = -90; send_sys(); }
    else if (s == ":healthy")    { sim_sys = SimSys{}; send_sys(); }
    else if (s == ":next") {
        inject("ST:play|TI:Lights Out (Go Crazy)|AR:Modestep|SO:spotify|VO:42|PO:0|DU:260000|LV:35|TM:14:32");
    }
    // ── Shutdown sequence (long-press behaviour on hardware) ────────────────
    else if (s == ":shutdown-warn") {
        inject("ST:shutdown_warn|TI:HALTEN ZUM AUSSCHALTEN|AR:|SO:none|VO:42|PO:0|DU:1|LV:0");
    }
    else if (s == ":shutdown") {
        inject("ST:shutdown|TI:AUSSCHALTEN...|AR:|SO:none|VO:42|PO:0|DU:1|LV:0");
    }
    // ── Sources other than Spotify ───────────────────────────────────────────
    else if (s == ":bluetooth") {
        inject("ST:play|TI:Phone Audio|AR:Steff iPhone|SO:bluetooth|VO:42|PO:0|DU:0|LV:30|TM:14:32");
    }
    else if (s == ":snapcast") {
        inject("ST:play|TI:Multiroom|AR:Living Room|SO:snapcast|VO:42|PO:0|DU:0|LV:25|TM:14:32");
    }
    // ── Boot progress lines (BOOT:stage|progress) ───────────────────────────
    else if (s == ":boot-progress") {
        inject("BOOT:Initializing audio|10");
        inject("BOOT:Loading CamillaDSP|30");
        inject("BOOT:Starting Spotify|60");
        inject("BOOT:Ready|100");
    }
    // ── Volume presets — emit a fresh play-state with the new VO field ──────
    else if (s.rfind(":vol ", 0) == 0) {
        int pct = atoi(s.c_str() + 5);
        if (pct < 0) pct = 0; if (pct > 100) pct = 100;
        char buf[200];
        snprintf(buf, sizeof(buf),
                 "ST:play|TI:Hang My Heart|AR:Modestep|SO:spotify|VO:%d|PO:32000|DU:240000|LV:35|TM:14:32",
                 pct);
        inject(buf);
    }
    // ── Title override — short way to test scroll / split-flap with custom text
    else if (s.rfind(":title ", 0) == 0) {
        std::string title = s.substr(7);
        std::string line = "ST:play|TI:" + title + "|AR:Sim|SO:spotify|VO:42|PO:0|DU:240000|LV:35|TM:14:32";
        inject(line.c_str());
    }
    // ── Palette override (live accent colour swap) ──────────────────────────
    else if (s.rfind(":palette ", 0) == 0) {
        std::string line = "PAL:" + s.substr(9);
        inject(line.c_str());
    }
    // ── Weather conditions — codes match handle_weather_line() ──────────────
    else if (s.rfind(":wx-", 0) == 0) {
        int code = 0;
        if      (s == ":wx-clear")    code = 0;
        else if (s == ":wx-partly")   code = 1;
        else if (s == ":wx-cloudy")   code = 3;
        else if (s == ":wx-rain")     code = 61;
        else if (s == ":wx-snow")     code = 71;
        else if (s == ":wx-thunder")  code = 95;
        else { printf("[sim] unknown wx code in %s\n", s.c_str()); return; }
        char buf[80];
        snprintf(buf, sizeof(buf), "WX:t=18|c=%d|h=22|l=11", code);
        inject(buf);
    }
    else if (s.rfind(":flap ", 0) == 0) {
        std::string line = "STBY:" + s.substr(6);
        inject(line.c_str());
    }
    else { printf("[sim] unknown command: %s  (try :help)\n", s.c_str()); fflush(stdout); }
}

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    setvbuf(stdout, NULL, _IOLBF, 0);  // line-buffer stdout — see prompts immediately

    // ─── LVGL init ──────────────────────────────────────────────────────────
    lv_init();
    lv_tick_set_cb(tick_cb);

    lv_display_t *disp = lv_sdl_window_create(466, 466);
    if (!disp) { fprintf(stderr, "lv_sdl_window_create failed\n"); return 1; }
    lv_sdl_window_set_title(disp, "BeatBird Display Simulator");

    lv_indev_t *mouse = lv_sdl_mouse_create();
    (void)mouse;

    ScreenBoot::create();
    ScreenBoot::show();
    ScreenPlayer::create();
    ScreenStandby::create();

    Proto::send_version();
    Proto::send_boot_marker();

    print_help();
    std::thread reader(stdin_reader);
    reader.detach();
    std::thread tcp(tcp_listener);
    tcp.detach();

    uint32_t demo_idx = 0;
    uint32_t next_step_ms = SDL_GetTicks() + demo[0].delay_ms;
    uint32_t next_sys_ms  = 0;
    bool     boot_transitioned = false;
    const uint32_t SYS_HEARTBEAT_MS = 5000;

    while (true) {
        const uint32_t now = SDL_GetTicks();

        // 1. Boot → Player transition once the first state line lands.
        if (!boot_transitioned && State::app.connected_to_pi) {
            ScreenBoot::transition_to(ScreenPlayer::root());
            boot_transitioned = true;
        }

        // 2. Scripted boot timeline.
        if (demo[demo_idx].line && (int32_t)(now - next_step_ms) >= 0) {
            inject(demo[demo_idx].line);
            demo_idx++;
            if (demo[demo_idx].line) next_step_ms = now + demo[demo_idx].delay_ms;
            else                     next_sys_ms  = now + SYS_HEARTBEAT_MS;
        }

        // 3. SYS heartbeat every 5s once the scripted timeline finished —
        //    PI OFFLINE triggers at last_status_rx > 12s without one.
        if (!demo[demo_idx].line && (int32_t)(now - next_sys_ms) >= 0) {
            send_sys();
            next_sys_ms = now + SYS_HEARTBEAT_MS;
        }

        // 4. Drain stdin commands.
        for (;;) {
            std::string cmd;
            {
                std::lock_guard<std::mutex> g(cmd_mu);
                if (cmd_queue.empty()) break;
                cmd = std::move(cmd_queue.front());
                cmd_queue.pop();
            }
            handle_command(cmd);
        }

        ScreenBoot::update();
        ScreenPlayer::update();
        ScreenStandby::update();

        lv_timer_handler();
        std::this_thread::sleep_for(std::chrono::milliseconds(16));
    }
}
