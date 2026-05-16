// =============================================================================
// ui/screens/screen_player.cpp — Active player + standby layouts
// =============================================================================
#include "screens/screen_player.h"
#include "state.h"
#include "theme.h"
#include "proto.h"

#include <Arduino.h>
#include <lvgl.h>
#include <math.h>
#include <stdio.h>

namespace ScreenPlayer {

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ─── LVGL objects ───────────────────────────────────────────────────────────

static lv_obj_t *scr           = nullptr;

// Custom-draw layers (back→front)
static lv_obj_t *vol_layer     = nullptr;   // 24-dot vol ring
static lv_obj_t *prog_layer    = nullptr;   // 60-dot progress stipple
static lv_obj_t *energy_layer  = nullptr;   // 12-dot smile

// Player widgets
static lv_obj_t *source_marker = nullptr;
static lv_obj_t *lbl_source    = nullptr;
static lv_obj_t *lbl_title     = nullptr;
static lv_obj_t *lbl_artist    = nullptr;
static lv_obj_t *state_icon    = nullptr;   // custom-drawn geometric glyph
static lv_obj_t *lbl_volume    = nullptr;

// Standby widgets
static lv_obj_t *lbl_clock     = nullptr;
static lv_obj_t *standby_dot   = nullptr;
static lv_anim_t anim_standby;

static bool     created            = false;
static bool     in_standby         = false;
static bool     standby_anim_alive = false;
static uint32_t last_energy_render = 0;
static uint32_t last_gesture_ms    = 0;

// Precomputed dot positions / unit vectors
static int   vol_x[24],    vol_y[24];
static int   prog_x[60],   prog_y[60];
static float energy_cos[12], energy_sin[12];

// ─── Helpers ────────────────────────────────────────────────────────────────

static lv_color_t source_color(State::Source s) {
    switch (s) {
        case State::SRC_SPOTIFY:   return Theme::Color::SRC_SPOTIFY;
        case State::SRC_BLUETOOTH: return Theme::Color::SRC_BT;
        case State::SRC_TOSLINK:   return Theme::Color::SRC_TOSLINK;
        case State::SRC_SNAPCAST:  return Theme::Color::SRC_SNAPCAST;
        default:                   return Theme::Color::SRC_NONE;
    }
}

static const char *source_label_text(State::Source s) {
    switch (s) {
        case State::SRC_SPOTIFY:   return "SPOTIFY";
        case State::SRC_BLUETOOTH: return "BLUETOOTH";
        case State::SRC_TOSLINK:   return "TV";
        case State::SRC_SNAPCAST:  return "MULTIROOM";
        default:                   return "";
    }
}

static const char *state_glyph(State::PlayState s) {
    // Unicode geometric glyphs — sharp-cornered, render crisp in Departure Mono.
    switch (s) {
        case State::PLAY_PLAYING: return "\xE2\x96\xB6";   // ▶
        case State::PLAY_PAUSED:  return "\xE2\x8F\xB8";   // ⏸
        case State::PLAY_STOPPED: return "\xE2\x96\xA0";   // ■
        default:                  return "";
    }
}

static void precompute_geometry() {
    // Volume: 24 dots, full 360°, start at 12 o'clock, clockwise.
    for (int i = 0; i < 24; i++) {
        float a = -(float)M_PI / 2.0f + (i / 24.0f) * 2.0f * (float)M_PI;
        vol_x[i] = Theme::CENTER + (int)roundf(cosf(a) * Theme::VOL_RING_R);
        vol_y[i] = Theme::CENTER + (int)roundf(sinf(a) * Theme::VOL_RING_R);
    }
    // Progress: 60 segments, 135° start, 270° sweep (gap = bottom 90°).
    for (int i = 0; i < 60; i++) {
        float a_deg = (float)Theme::PROG_ARC_START_DEG +
                      (i / 59.0f) * (float)Theme::PROG_ARC_SWEEP_DEG;
        float a = a_deg * (float)M_PI / 180.0f;
        prog_x[i] = Theme::CENTER + (int)roundf(cosf(a) * Theme::PROG_RING_R);
        prog_y[i] = Theme::CENTER + (int)roundf(sinf(a) * Theme::PROG_RING_R);
    }
    // Energy: 12 dots, 140° smile centred on bottom (90°).
    // Iterate angle DOWN from 160° to 20° so dot[0] sits on the LEFT (= bass).
    for (int i = 0; i < 12; i++) {
        float a_deg = 160.0f - (i / 11.0f) * 140.0f;
        float a = a_deg * (float)M_PI / 180.0f;
        energy_cos[i] = cosf(a);
        energy_sin[i] = sinf(a);
    }
}

static void draw_dot(lv_layer_t *layer, int cx, int cy, int r,
                     lv_color_t color, lv_opa_t opa) {
    if (opa == LV_OPA_TRANSP || r <= 0) return;
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    dsc.bg_color = color;
    dsc.bg_opa   = opa;
    dsc.radius   = LV_RADIUS_CIRCLE;
    lv_area_t a;
    a.x1 = cx - r;  a.y1 = cy - r;
    a.x2 = cx + r;  a.y2 = cy + r;
    lv_draw_rect(layer, &dsc, &a);
}

// ─── Draw callbacks ─────────────────────────────────────────────────────────

static void vol_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    int vol = State::app.volume;
    int lit = (vol * 24 + 50) / 100;
    for (int i = 0; i < 24; i++) {
        if (i == 0) continue;   // top dot — covered by source_marker
        bool is_lit = (i < lit);
        lv_color_t c = is_lit ? Theme::accent : Theme::accent_dim;
        int r        = is_lit ? Theme::VOL_DOT_R : Theme::VOL_DOT_R_DIM;
        lv_opa_t  o  = is_lit ? LV_OPA_COVER : (lv_opa_t)160;
        draw_dot(layer, vol_x[i], vol_y[i], r, c, o);
    }
}

static void prog_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    int prog_pct = 0;
    if (State::app.dur_ms > 1) {
        uint32_t pos = State::app.pos_ms;
        if (pos > State::app.dur_ms) pos = State::app.dur_ms;
        prog_pct = (int)((uint64_t)pos * 100u / State::app.dur_ms);
    }
    int lit = (prog_pct * 60 + 50) / 100;
    for (int i = 0; i < 60; i++) {
        bool is_lit  = (i < lit);
        lv_color_t c = is_lit ? Theme::accent : Theme::Color::TEXT_FAINT;
        lv_opa_t   o = is_lit ? LV_OPA_COVER : (lv_opa_t)120;
        draw_dot(layer, prog_x[i], prog_y[i], 2, c, o);
    }
}

static void energy_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);

    uint8_t dot_e[12] = {0};

    if (State::app.spectrum_bands > 0) {
        // FX bands available — proportional bass→treble mapping (left→right).
        // dot[0] = lowest band (bass), dot[11] = highest band (treble).
        int N = State::app.spectrum_bands;
        for (int i = 0; i < 12; i++) {
            int b0 = (i * N) / 12;
            int b1 = ((i + 1) * N) / 12;
            if (b1 <= b0) b1 = b0 + 1;
            int sum = 0, cnt = 0;
            for (int b = b0; b < b1 && b < N; b++) {
                sum += State::app.spectrum[b];
                cnt++;
            }
            dot_e[i] = (uint8_t)(cnt > 0 ? sum / cnt : 0);
        }
    } else {
        // Fallback: pulsing wave from overall energy with phase offset
        uint32_t t = millis();
        float E = (State::app.state == State::PLAY_PLAYING) ? State::app.energy : 0.0f;
        for (int i = 0; i < 12; i++) {
            float wob = sinf(t * 0.004f + i * 0.55f) * 0.3f + 0.7f;
            float ev  = E * wob;
            if (ev > 1.0f) ev = 1.0f;
            dot_e[i] = (uint8_t)(ev * 100.0f);
        }
    }

    for (int i = 0; i < 12; i++) {
        float ev  = dot_e[i] / 100.0f;
        int defl  = (int)(ev * Theme::ENERGY_DEFLECTION_PX);
        int r     = Theme::ENERGY_RING_R + defl;
        int x     = Theme::CENTER + (int)roundf(energy_cos[i] * r);
        int y     = Theme::CENTER + (int)roundf(energy_sin[i] * r);
        int dot_r = (ev > 0.65f) ? Theme::ENERGY_DOT_R_PEAK : Theme::ENERGY_DOT_R;
        lv_opa_t o = (lv_opa_t)(110 + (int)(ev * 145.0f));
        draw_dot(layer, x, y, dot_r, Theme::accent, o);
    }
}

static void state_icon_draw_cb(lv_event_t *e) {
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_obj_t  *obj    = (lv_obj_t *)lv_event_get_target(e);
    lv_area_t coords;
    lv_obj_get_coords(obj, &coords);

    // Reference frame: 24×24 box. All shape coords are relative to top-left.
    const int x = coords.x1;
    const int y = coords.y1;

    State::PlayState st = State::app.state;
    if (st == State::PLAY_STOPPED || st == State::PLAY_STANDBY) {
        // Stopped = small filled square. Hidden during standby (caller's job).
        if (st == State::PLAY_STANDBY) return;
        lv_draw_rect_dsc_t rdsc;
        lv_draw_rect_dsc_init(&rdsc);
        rdsc.bg_color = Theme::accent_dim;
        rdsc.bg_opa   = LV_OPA_COVER;
        rdsc.radius   = 0;
        lv_area_t sq = { x + 6, y + 6, x + 17, y + 17 };
        lv_draw_rect(layer, &rdsc, &sq);
        return;
    }

    if (st == State::PLAY_PAUSED) {
        lv_draw_rect_dsc_t rdsc;
        lv_draw_rect_dsc_init(&rdsc);
        rdsc.bg_color = Theme::accent_dim;
        rdsc.bg_opa   = LV_OPA_COVER;
        rdsc.radius   = 0;
        lv_area_t bar1 = { x + 5,  y + 4, x + 9,  y + 19 };
        lv_area_t bar2 = { x + 14, y + 4, x + 18, y + 19 };
        lv_draw_rect(layer, &rdsc, &bar1);
        lv_draw_rect(layer, &rdsc, &bar2);
        return;
    }

    if (st == State::PLAY_PLAYING) {
        lv_draw_triangle_dsc_t tdsc;
        lv_draw_triangle_dsc_init(&tdsc);
        tdsc.color = Theme::accent_dim;
        tdsc.opa   = LV_OPA_COVER;
        tdsc.p[0].x = x + 6;   tdsc.p[0].y = y + 4;
        tdsc.p[1].x = x + 6;   tdsc.p[1].y = y + 19;
        tdsc.p[2].x = x + 19;  tdsc.p[2].y = y + 11;
        lv_draw_triangle(layer, &tdsc);
        return;
    }
}

// ─── Standby pulse ──────────────────────────────────────────────────────────

static void standby_pulse_cb(void *var, int32_t v) {
    lv_obj_set_style_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void start_standby_pulse() {
    if (standby_anim_alive) return;
    standby_anim_alive = true;
    lv_anim_init(&anim_standby);
    lv_anim_set_var(&anim_standby, standby_dot);
    lv_anim_set_exec_cb(&anim_standby, standby_pulse_cb);
    lv_anim_set_values(&anim_standby, 80, 255);
    lv_anim_set_time(&anim_standby, 1400);
    lv_anim_set_playback_time(&anim_standby, 1400);
    lv_anim_set_repeat_count(&anim_standby, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb(&anim_standby, lv_anim_path_ease_in_out);
    lv_anim_start(&anim_standby);
}

static void stop_standby_pulse() {
    if (!standby_anim_alive) return;
    standby_anim_alive = false;
    lv_anim_del(standby_dot, standby_pulse_cb);
    lv_obj_set_style_opa(standby_dot, LV_OPA_COVER, 0);
}

// ─── Touch handlers ─────────────────────────────────────────────────────────

static void on_tap(lv_event_t *e) {
    if (in_standby) return;
    if (millis() - last_gesture_ms < 300) return;
    Proto::send_command("PLAYPAUSE");
}

static void on_gesture(lv_event_t *e) {
    if (in_standby) return;
    lv_indev_t *indev = lv_indev_get_act();
    if (!indev) return;
    lv_dir_t dir = lv_indev_get_gesture_dir(indev);
    if (dir == LV_DIR_NONE) return;
    last_gesture_ms = millis();

    int v = State::app.volume;
    switch (dir) {
        case LV_DIR_LEFT:   Proto::send_command("NEXT"); break;
        case LV_DIR_RIGHT:  Proto::send_command("PREV"); break;
        case LV_DIR_TOP:
            v = (v + 10 > 100) ? 100 : v + 10;
            Proto::send_volume(v);
            State::set_volume(v);
            break;
        case LV_DIR_BOTTOM:
            v = (v - 10 < 0) ? 0 : v - 10;
            Proto::send_volume(v);
            State::set_volume(v);
            break;
        default: break;
    }
}

// Long press → status screen (deferred until status modernisation).

// ─── Mode switching ─────────────────────────────────────────────────────────

static void show_player_mode() {
    if (!in_standby && lbl_title && !lv_obj_has_flag(lbl_title, LV_OBJ_FLAG_HIDDEN))
        return;
    in_standby = false;
    auto S = [](lv_obj_t *o) { if (o) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN); };
    auto H = [](lv_obj_t *o) { if (o) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); };
    S(vol_layer); S(prog_layer); S(energy_layer);
    S(source_marker); S(lbl_source);
    S(lbl_title); S(lbl_artist); S(state_icon ); S(lbl_volume);
    H(lbl_clock); H(standby_dot);
    stop_standby_pulse();
}

static void show_standby_mode() {
    if (in_standby) return;
    in_standby = true;
    auto H = [](lv_obj_t *o) { if (o) lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); };
    auto S = [](lv_obj_t *o) { if (o) lv_obj_clear_flag(o, LV_OBJ_FLAG_HIDDEN); };
    H(vol_layer); H(prog_layer); H(energy_layer);
    H(source_marker); H(lbl_source);
    H(lbl_title); H(lbl_artist); H(state_icon ); H(lbl_volume);
    S(lbl_clock); S(standby_dot);
    start_standby_pulse();
}

// ─── Construction ───────────────────────────────────────────────────────────

void create() {
    if (created) return;
    created = true;
    precompute_geometry();

    // ── Screen container ────────────────────────────────────────────────────
    scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, Theme::Color::BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_style_pad_all(scr, 0, 0);
    lv_obj_set_style_border_width(scr, 0, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(scr, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(scr, on_tap,     LV_EVENT_SHORT_CLICKED, NULL);
    lv_obj_add_event_cb(scr, on_gesture, LV_EVENT_GESTURE,       NULL);

    // ── Full-screen custom-draw layers (back→front) ─────────────────────────
    auto make_layer = [&](void (*cb)(lv_event_t *)) -> lv_obj_t * {
        lv_obj_t *o = lv_obj_create(scr);
        lv_obj_remove_style_all(o);
        lv_obj_set_size(o, Theme::CENTER * 2, Theme::CENTER * 2);
        lv_obj_set_pos(o, 0, 0);
        lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, 0);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(o, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(o, cb, LV_EVENT_DRAW_MAIN, NULL);
        return o;
    };
    vol_layer    = make_layer(vol_draw_cb);
    prog_layer   = make_layer(prog_draw_cb);
    energy_layer = make_layer(energy_draw_cb);

    // ── Source marker (sharp-edged square at 12 o'clock) ────────────────────
    source_marker = lv_obj_create(scr);
    lv_obj_remove_style_all(source_marker);
    lv_obj_set_size(source_marker,
                    Theme::SOURCE_MARKER_SIZE, Theme::SOURCE_MARKER_SIZE);
    lv_obj_set_pos(source_marker,
                   Theme::CENTER - Theme::SOURCE_MARKER_SIZE / 2,
                   Theme::SOURCE_MARKER_Y);
    lv_obj_set_style_bg_color(source_marker, Theme::Color::SRC_NONE, 0);
    lv_obj_set_style_bg_opa(source_marker, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(source_marker, 0, 0);
    lv_obj_add_flag(source_marker, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(source_marker, LV_OBJ_FLAG_CLICKABLE);

    // ── Source label (NDot mono, dim accent) ────────────────────────────────
    lbl_source = lv_label_create(scr);
    lv_label_set_text(lbl_source, "");
    lv_obj_set_style_text_color(lbl_source, Theme::accent_dim, 0);
    lv_obj_set_style_text_font(lbl_source, Theme::font_body(), 0);
    lv_obj_set_style_text_letter_space(lbl_source, Theme::LETTER_SPACE_LABEL, 0);
    lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
    lv_obj_add_flag(lbl_source, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_source, LV_OBJ_FLAG_CLICKABLE);

    // ── Title ───────────────────────────────────────────────────────────────
    lbl_title = lv_label_create(scr);
    lv_label_set_text(lbl_title, "");
    lv_obj_set_style_text_color(lbl_title, Theme::accent, 0);
    lv_obj_set_style_text_font(lbl_title, Theme::font_display_lg(), 0);
    lv_obj_set_style_text_letter_space(lbl_title, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(lbl_title, 4, 0);
    lv_obj_set_width(lbl_title, 280);
    lv_label_set_long_mode(lbl_title, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_title, LV_ALIGN_CENTER, 0, Theme::TITLE_Y_OFFSET);
    lv_obj_add_flag(lbl_title, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_title, LV_OBJ_FLAG_CLICKABLE);

    // ── Artist ──────────────────────────────────────────────────────────────
    lbl_artist = lv_label_create(scr);
    lv_label_set_text(lbl_artist, "");
    lv_obj_set_style_text_color(lbl_artist, Theme::Color::TEXT_DIM, 0);
    lv_obj_set_style_text_font(lbl_artist, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_artist, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_artist, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(lbl_artist, 260);
    lv_label_set_long_mode(lbl_artist, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_align(lbl_artist, LV_ALIGN_CENTER, 0, Theme::ARTIST_Y_OFFSET);
    lv_obj_add_flag(lbl_artist, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_artist, LV_OBJ_FLAG_CLICKABLE);

    // ── State glyph ─────────────────────────────────────────────────────────
// ── State icon (custom-drawn geometric glyph) ───────────────────────────
    state_icon = lv_obj_create(scr);
    lv_obj_remove_style_all(state_icon);
    lv_obj_set_size(state_icon, 24, 24);
    lv_obj_set_style_bg_opa(state_icon, LV_OPA_TRANSP, 0);
    lv_obj_align(state_icon, LV_ALIGN_CENTER, 0, Theme::STATE_ICON_Y);
    lv_obj_add_flag(state_icon, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(state_icon, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(state_icon, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(state_icon, state_icon_draw_cb, LV_EVENT_DRAW_MAIN, NULL);

    // ── Volume % ────────────────────────────────────────────────────────────
    lbl_volume = lv_label_create(scr);
    lv_label_set_text(lbl_volume, "");
    lv_obj_set_style_text_color(lbl_volume, Theme::accent, 0);
    lv_obj_set_style_text_font(lbl_volume, Theme::font_display_md(), 0);
    lv_obj_set_style_text_letter_space(lbl_volume, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_set_style_text_align(lbl_volume, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(lbl_volume, LV_ALIGN_CENTER, 0, Theme::VOLUME_PCT_Y);
    lv_obj_add_flag(lbl_volume, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_volume, LV_OBJ_FLAG_CLICKABLE);

    // ── Standby clock ───────────────────────────────────────────────────────
    lbl_clock = lv_label_create(scr);
    lv_label_set_text(lbl_clock, "--:--");
    lv_obj_set_style_text_color(lbl_clock, Theme::accent, 0);
    lv_obj_set_style_text_font(lbl_clock, Theme::font_clock(), 0);
    lv_obj_set_style_text_letter_space(lbl_clock, Theme::LETTER_SPACE_DISPLAY, 0);
    lv_obj_align(lbl_clock, LV_ALIGN_CENTER, 0, -10);
    lv_obj_add_flag(lbl_clock, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(lbl_clock, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(lbl_clock, LV_OBJ_FLAG_CLICKABLE);

    // ── Standby heartbeat dot ───────────────────────────────────────────────
    standby_dot = lv_obj_create(scr);
    lv_obj_remove_style_all(standby_dot);
    lv_obj_set_size(standby_dot, 10, 10);
    lv_obj_set_style_bg_color(standby_dot, Theme::accent, 0);
    lv_obj_set_style_bg_opa(standby_dot, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(standby_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_align(standby_dot, LV_ALIGN_CENTER, 0, 70);
    lv_obj_add_flag(standby_dot, LV_OBJ_FLAG_HIDDEN);
    lv_obj_add_flag(standby_dot, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_clear_flag(standby_dot, LV_OBJ_FLAG_CLICKABLE);
}

void show() {
    if (!created) create();
    lv_screen_load(scr);
    State::app.active_screen = State::SCR_PLAYER;
    State::mark_dirty(State::Dirty::ALL);
}

lv_obj_t *root() {
    if (!created) create();
    return scr;
}

bool is_visible() {
    return created && State::app.active_screen == State::SCR_PLAYER;
}

// ─── Per-frame update ───────────────────────────────────────────────────────

void update() {
    if (!created || !is_visible()) return;

    // Mode switch (standby ↔ player)
    if (State::app.state == State::PLAY_STANDBY) show_standby_mode();
    else                                          show_player_mode();

    // Accent recolour (cheap, idempotent)
    if (State::is_dirty(State::Dirty::ACCENT)) {
        lv_obj_set_style_text_color(lbl_title,   Theme::accent,     0);
        lv_obj_set_style_text_color(lbl_volume,  Theme::accent,     0);
        lv_obj_invalidate(state_icon);
        lv_obj_set_style_text_color(lbl_source,  Theme::accent_dim, 0);
        lv_obj_set_style_text_color(lbl_clock,   Theme::accent,     0);
        lv_obj_set_style_bg_color  (standby_dot, Theme::accent,     0);
        lv_obj_invalidate(vol_layer);
        lv_obj_invalidate(prog_layer);
        lv_obj_invalidate(energy_layer);
        State::clear_dirty(State::Dirty::ACCENT);
    }

    if (in_standby) {
        if (State::is_dirty(State::Dirty::CLOCK)) {
            lv_label_set_text(lbl_clock, State::app.clockStr.c_str());
            State::clear_dirty(State::Dirty::CLOCK);
        }
        return;
    }

    // ── Player updates (dirty-driven) ───────────────────────────────────────
    if (State::is_dirty(State::Dirty::TITLE)) {
        const char *t = State::app.title.length() ? State::app.title.c_str() : "—";
        lv_label_set_text(lbl_title, t);
        State::clear_dirty(State::Dirty::TITLE);
    }
    if (State::is_dirty(State::Dirty::ARTIST)) {
        lv_label_set_text(lbl_artist, State::app.artist.c_str());
        State::clear_dirty(State::Dirty::ARTIST);
    }
    if (State::is_dirty(State::Dirty::STATE)) {
        lv_obj_invalidate(state_icon);
        State::clear_dirty(State::Dirty::STATE);
    }
    if (State::is_dirty(State::Dirty::SOURCE)) {
        lv_obj_set_style_bg_color(source_marker, source_color(State::app.source), 0);
        lv_label_set_text(lbl_source, source_label_text(State::app.source));
        lv_obj_align(lbl_source, LV_ALIGN_CENTER, 0, Theme::SOURCE_LABEL_Y);
        State::clear_dirty(State::Dirty::SOURCE);
    }
    if (State::is_dirty(State::Dirty::VOLUME)) {
        char buf[8];
        snprintf(buf, sizeof(buf), "%d%%", State::app.volume);
        lv_label_set_text(lbl_volume, buf);
        lv_obj_align(lbl_volume, LV_ALIGN_CENTER, 0, Theme::VOLUME_PCT_Y);
        lv_obj_invalidate(vol_layer);
        State::clear_dirty(State::Dirty::VOLUME);
    }
    if (State::is_dirty(State::Dirty::PROGRESS)) {
        lv_obj_invalidate(prog_layer);
        State::clear_dirty(State::Dirty::PROGRESS);
    }

    // Energy ring: 60Hz cap when playing, dirty-driven otherwise.
    if (State::app.state == State::PLAY_PLAYING) {
        uint32_t now = millis();
        if (now - last_energy_render >= 16) {
            last_energy_render = now;
            lv_obj_invalidate(energy_layer);
        }
    } else if (State::is_dirty(State::Dirty::ENERGY) ||
               State::is_dirty(State::Dirty::SPECTRUM)) {
        lv_obj_invalidate(energy_layer);
        State::clear_dirty(State::Dirty::ENERGY | State::Dirty::SPECTRUM);
    }
}

}  // namespace ScreenPlayer