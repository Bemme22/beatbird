// =============================================================================
// ui/screens/split_flap.cpp — Mercedes-Anzeigetafel-style label transitions
// =============================================================================
#include "screens/split_flap.h"

#include <Arduino.h>
#include <lvgl.h>
#include <string.h>

namespace SplitFlap {

// Random-glyph alphabet during the cycling phase. ASCII-only — Departure
// Mono is essentially ASCII; non-ASCII would render as tofu mid-flap.
static const char *FLAP_CHARS    = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-";
static constexpr int FLAP_CHARS_LEN     = 37;
static constexpr int FLAP_TICKS_PER_POS = 5;
static constexpr uint32_t FLAP_TICK_MS  = 70;
static constexpr int FLAP_POS_STAGGER   = 1;     // tick(s) between adjacent position starts

// Maximum text length we'll animate. Anything longer is truncated.
static constexpr int MAX_LEN = 48;

struct Anim {
    lv_obj_t *label;
    lv_timer_t *timer;
    char target [MAX_LEN + 1];   // final text (padded with spaces to `positions`)
    char buf    [MAX_LEN + 1];   // current frame, rewritten every tick
    int  positions;
    int  new_len;                // real length of new text (without space padding)
    int  tick;
    int8_t pos_ticks[MAX_LEN];   // ticks spent cycling on each position
    int8_t pos_start[MAX_LEN];   // tick at which each position starts cycling
};

// Concurrent slots: title, artist, maybe boot wordmark later.
static constexpr int MAX_ANIMS = 4;
static Anim anims[MAX_ANIMS];
static bool slot_used[MAX_ANIMS];

// ─── Slot management ───────────────────────────────────────────────────────

static Anim *find_existing(lv_obj_t *label) {
    for (int i = 0; i < MAX_ANIMS; i++) {
        if (slot_used[i] && anims[i].label == label) return &anims[i];
    }
    return nullptr;
}

static Anim *alloc_slot(lv_obj_t *label) {
    for (int i = 0; i < MAX_ANIMS; i++) {
        if (!slot_used[i]) {
            slot_used[i] = true;
            anims[i].label = label;
            anims[i].timer = nullptr;
            return &anims[i];
        }
    }
    return nullptr;
}

static void free_slot(Anim *a) {
    if (a->timer) {
        lv_timer_del(a->timer);
        a->timer = nullptr;
    }
    for (int i = 0; i < MAX_ANIMS; i++) {
        if (&anims[i] == a) {
            slot_used[i] = false;
            return;
        }
    }
}

// ─── Animation tick ────────────────────────────────────────────────────────

static void tick_cb(lv_timer_t *t) {
    Anim *a = (Anim *)lv_timer_get_user_data(t);
    a->tick++;

    bool all_done = true;
    for (int i = 0; i < a->positions; i++) {
        if (a->tick < a->pos_start[i]) {
            // Position hasn't started cycling yet — keep whatever's in
            // buf[i] (i.e. the old character at that slot).
            all_done = false;
            continue;
        }
        if (a->pos_ticks[i] >= FLAP_TICKS_PER_POS) {
            // Locked to target
            a->buf[i] = a->target[i];
            continue;
        }
        // Still cycling: pick a random glyph for this frame
        a->buf[i] = FLAP_CHARS[random(0, FLAP_CHARS_LEN)];
        a->pos_ticks[i]++;
        all_done = false;
    }

    lv_label_set_text(a->label, a->buf);

    if (all_done) {
        // Final paint with the clean target — trim the trailing space
        // padding that was added when the new text is shorter than the
        // old one. Without this, the label keeps the padding visible
        // (and a circular-scroll label may even decide to scroll a string
        // that should comfortably fit).
        a->buf[a->new_len] = '\0';
        lv_label_set_text(a->label, a->buf);
        free_slot(a);
    }
}

// ─── Public API ────────────────────────────────────────────────────────────

void set_text(lv_obj_t *label, const char *new_text) {
    if (!label || !new_text) return;

    const char *old_text = lv_label_get_text(label);
    if (old_text && strcmp(old_text, new_text) == 0) return;   // no-op

    int old_len = old_text ? (int)strnlen(old_text, MAX_LEN) : 0;
    int new_len = (int)strnlen(new_text, MAX_LEN);
    int positions = old_len > new_len ? old_len : new_len;

    if (positions == 0) {
        lv_label_set_text(label, new_text);
        return;
    }

    // Get or allocate a slot. If we run out (shouldn't happen with
    // MAX_ANIMS=4), just set the text directly.
    Anim *a = find_existing(label);
    if (a) {
        // Kill the running timer; we'll restart with the new target.
        if (a->timer) { lv_timer_del(a->timer); a->timer = nullptr; }
    } else {
        a = alloc_slot(label);
        if (!a) {
            lv_label_set_text(label, new_text);
            return;
        }
    }

    a->positions = positions;
    a->new_len   = new_len;
    a->tick      = 0;

    // Seed buf with old text (padded with spaces), target with new text.
    for (int i = 0; i < positions; i++) {
        a->buf[i]       = (i < old_len) ? old_text[i] : ' ';
        a->target[i]    = (i < new_len) ? new_text[i] : ' ';
        a->pos_ticks[i] = 0;
        a->pos_start[i] = (int8_t)(i * FLAP_POS_STAGGER);
    }
    a->buf[positions]    = '\0';
    a->target[positions] = '\0';

    // Paint the initial frame so the user sees the old text re-anchored
    // before the first cycle tick.
    lv_label_set_text(label, a->buf);

    a->timer = lv_timer_create(tick_cb, FLAP_TICK_MS, a);
}

}  // namespace SplitFlap
