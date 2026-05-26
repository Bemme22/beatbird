// =============================================================================
// ui/screens/screen_boot.cpp — Boot / connection-waiting screen
// =============================================================================
#include "screens/screen_boot.h"
#include "screens/split_flap.h"
#include "theme.h"
#include "state.h"

#include <lvgl.h>
#include <stdio.h>

namespace ScreenBoot {

// ─── LVGL objects ───────────────────────────────────────────────────────────

static lv_obj_t *scr          = nullptr;
static lv_obj_t *lbl_wordmark = nullptr;
static lv_obj_t *lbl_status   = nullptr;
static lv_obj_t *lbl_subline  = nullptr;
static lv_obj_t *outer_arc    = nullptr;
static lv_anim_t anim_pulse;
static lv_anim_t anim_rotate;

static bool created     = false;
static bool transitioned = false;
static uint32_t last_progress_shown = UINT32_MAX;   // force first paint

// ─── Animation callbacks ────────────────────────────────────────────────────

static void anim_status_opa_cb(void *var, int32_t v)
{
    lv_obj_set_style_opa((lv_obj_t *)var, (lv_opa_t)v, 0);
}

static void anim_arc_rotate_cb(void *var, int32_t v)
{
    int32_t start = v % 360;
    int32_t end   = (start + 30) % 360;
    lv_arc_set_bg_angles((lv_obj_t *)var, start, end);
}

// ─── Helpers ────────────────────────────────────────────────────────────────

static void apply_accent_colors()
{
    if (lbl_wordmark) lv_obj_set_style_text_color(lbl_wordmark, Theme::accent_dim, 0);
    if (lbl_status)   lv_obj_set_style_text_color(lbl_status,   Theme::accent,     0);
    if (outer_arc)    lv_obj_set_style_arc_color (outer_arc,    Theme::accent_dim, LV_PART_MAIN);
}

static void stop_animations()
{
    // Use v8-compatible name; `lv_anim_delete` is the v9 alias.
    if (lbl_status) lv_anim_del(lbl_status, anim_status_opa_cb);
    if (outer_arc)  lv_anim_del(outer_arc,  anim_arc_rotate_cb);
}

// ─── Public API ─────────────────────────────────────────────────────────────

void create()
{
    if (created) return;
    created = true;

    // ── Screen container ────────────────────────────────────────────────────
    scr = lv_obj_create(NULL);
    lv_obj_set_style_bg_color(scr, Theme::Color::BG, 0);
    lv_obj_set_style_bg_opa  (scr, LV_OPA_COVER,     0);
    lv_obj_set_style_pad_all (scr, 0,                0);
    lv_obj_set_style_border_width(scr, 0,            0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    // ── Outer rotating arc (loading indicator) ──────────────────────────────
    outer_arc = lv_arc_create(scr);
    lv_obj_set_size(outer_arc, (Theme::CENTER * 2) - 8, (Theme::CENTER * 2) - 8);
    lv_obj_center (outer_arc);
    lv_arc_set_rotation  (outer_arc, 270);              // 0° at 12 o'clock
    lv_arc_set_bg_angles (outer_arc, 0, 30);
    lv_arc_set_value     (outer_arc, 0);

    // Background = visible 30° trail
    lv_obj_set_style_arc_color  (outer_arc, Theme::accent_dim, LV_PART_MAIN);
    lv_obj_set_style_arc_width  (outer_arc, 6,                 LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(outer_arc, true,              LV_PART_MAIN);

    // Hide indicator + knob
    lv_obj_set_style_arc_width  (outer_arc, 0,                LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa    (outer_arc, LV_OPA_TRANSP,    LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa     (outer_arc, LV_OPA_TRANSP,    LV_PART_KNOB);
    lv_obj_set_style_pad_all    (outer_arc, 0,                LV_PART_KNOB);

    lv_obj_clear_flag(outer_arc, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(outer_arc, LV_OBJ_FLAG_SCROLLABLE);

    // ── Wordmark — top ──────────────────────────────────────────────────────
    // Start empty; show() kicks a SplitFlap fill so each letter cycles
    // in from random characters. Departure Mono glyphs are already
    // dot-matrix at the pixel level, so a per-character reveal reads
    // as 'station-board flips in' — the closest Departure-Mono-based
    // proxy for Nothing's per-dot wordmark fill without dragging in a
    // separate pixel-mask renderer.
    lbl_wordmark = lv_label_create(scr);
    lv_label_set_text(lbl_wordmark, "");
    lv_obj_set_style_text_color       (lbl_wordmark, Theme::accent_dim,             0);
    lv_obj_set_style_text_font        (lbl_wordmark, Theme::font_display_lg(),      0);
    lv_obj_set_style_text_letter_space(lbl_wordmark, Theme::LETTER_SPACE_DISPLAY,   0);
    lv_obj_set_style_text_align       (lbl_wordmark, LV_TEXT_ALIGN_CENTER,          0);
    lv_obj_set_width                  (lbl_wordmark, 340);
    lv_obj_align(lbl_wordmark, LV_ALIGN_CENTER, 0, -125);

    // ── Status — centre, breathing opacity ──────────────────────────────────
    lbl_status = lv_label_create(scr);
    lv_label_set_text(lbl_status, "CONNECTING");
    lv_obj_set_style_text_color       (lbl_status, Theme::accent,                 0);
    lv_obj_set_style_text_font        (lbl_status, Theme::font_display_lg(),      0);
    lv_obj_set_style_text_letter_space(lbl_status, Theme::LETTER_SPACE_DISPLAY,   0);
    lv_obj_align(lbl_status, LV_ALIGN_CENTER, 0, -8);

    // ── Subline — below status ──────────────────────────────────────────────
    lbl_subline = lv_label_create(scr);
    lv_label_set_text(lbl_subline, "waiting for raspberry pi");
    lv_obj_set_style_text_color       (lbl_subline, Theme::Color::TEXT_DIM,        0);
    lv_obj_set_style_text_font        (lbl_subline, Theme::font_display_md(),      0);
    lv_obj_set_style_text_letter_space(lbl_subline, Theme::LETTER_SPACE_DISPLAY,   0);
    lv_obj_align(lbl_subline, LV_ALIGN_CENTER, 0, 50);

    // ── Animations ──────────────────────────────────────────────────────────
    // Using the v8-compatible API names (lv_anim_set_time / set_playback_time)
    // so the file builds against the full LVGL 9.x range without churn. They
    // remain valid aliases in 9.2/9.3 even after the rename to set_duration /
    // set_reverse_duration.

    // Status: breath 120 ↔ 255 with ease-in-out, 1.2 s each way, infinite.
    lv_anim_init           (&anim_pulse);
    lv_anim_set_var        (&anim_pulse, lbl_status);
    lv_anim_set_exec_cb    (&anim_pulse, anim_status_opa_cb);
    lv_anim_set_values     (&anim_pulse, 120, 255);
    lv_anim_set_time       (&anim_pulse, Theme::ANIM_BOOT_PULSE_MS);
    lv_anim_set_playback_time(&anim_pulse, Theme::ANIM_BOOT_PULSE_MS);
    lv_anim_set_repeat_count(&anim_pulse, LV_ANIM_REPEAT_INFINITE);
    lv_anim_set_path_cb    (&anim_pulse, lv_anim_path_ease_in_out);
    lv_anim_start          (&anim_pulse);

    // Outer arc: rotate 0 → 360 linearly, 1.8 s, infinite.
    lv_anim_init           (&anim_rotate);
    lv_anim_set_var        (&anim_rotate, outer_arc);
    lv_anim_set_exec_cb    (&anim_rotate, anim_arc_rotate_cb);
    lv_anim_set_values     (&anim_rotate, 0, 360);
    lv_anim_set_time       (&anim_rotate, Theme::ANIM_BOOT_ARC_MS);
    lv_anim_set_repeat_count(&anim_rotate, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start          (&anim_rotate);
}

void show()
{
    if (!created) create();
    transitioned = false;
    last_progress_shown = UINT32_MAX;
    lv_screen_load(scr);
    State::app.active_screen = State::SCR_BOOT;
    // Reset to empty in case the screen is being re-shown after a
    // disconnect — SplitFlap with old="" gives the full 8-position
    // reveal every time, not a partial diff against whatever was left.
    lv_label_set_text(lbl_wordmark, "");
    SplitFlap::set_text(lbl_wordmark, "BEATBIRD");
}

void update()
{
    if (!created || transitioned) return;

    // Repaint with current accent — cheap, idempotent.
    if (State::is_dirty(State::Dirty::ACCENT)) {
        apply_accent_colors();
        // Don't clear; player screen may listen too. Each consumer applies its own.
    }

    // Reflect Pi boot progress in the subline if the Pi is sending BOOT: lines.
    uint32_t p = State::app.boot_progress;
    if (p != last_progress_shown) {
        last_progress_shown = p;
        if (p == 0) {
            lv_label_set_text(lbl_subline, "waiting for raspberry pi");
        } else if (p >= 100) {
            lv_label_set_text(lbl_subline, "ready");
        } else {
            char buf[24];
            snprintf(buf, sizeof(buf), "pi booting   %u%%", (unsigned)p);
            lv_label_set_text(lbl_subline, buf);
        }
        lv_obj_align(lbl_subline, LV_ALIGN_CENTER, 0, 50);
    }
}

void transition_to(lv_obj_t *target)
{
    if (transitioned || !target) return;
    transitioned = true;
    stop_animations();
    // Briefly hold full-opacity status then fade out.
    lv_obj_set_style_opa(lbl_status, LV_OPA_COVER, 0);
    lv_screen_load_anim(target, LV_SCR_LOAD_ANIM_FADE_IN, 400, 60, false);
    State::app.active_screen = State::SCR_PLAYER;
}

bool is_active()
{
    return created && !transitioned;
}

}  // namespace ScreenBoot
