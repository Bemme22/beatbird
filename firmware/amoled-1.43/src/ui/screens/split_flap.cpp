// =============================================================================
// ui/screens/split_flap.cpp — Mercedes-Anzeigetafel-style label transitions
// =============================================================================
#include "screens/split_flap.h"

#ifdef ARDUINO
  #include <Arduino.h>
#else
  #include "sim/arduino_shim.h"
#endif
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
    // The label's long-mode is forced to CLIP while we animate, then
    // restored from this saved value when the flap completes. Without
    // this, a SCROLL_CIRCULAR label restarts its scroll on every
    // lv_label_set_text() inside the tick — 14× per flap. The eye reads
    // those scroll-restarts as "chaos → snap to final text" instead of a
    // smooth reveal, which was the long-to-short jitter the user saw.
    lv_label_long_mode_t saved_long_mode;
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
            // Position hasn't reached its lock-in phase yet — keep
            // cycling random glyphs so the user sees motion across the
            // whole label from frame 0. The old behaviour of "freeze
            // whatever's in buf[i]" left static characters during the
            // stagger window, which read as old text peeking through.
            a->buf[i] = FLAP_CHARS[random(0, FLAP_CHARS_LEN)];
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
        // Restore long-mode BEFORE set_text. LVGL initialises the scroll
        // animation inside set_text — if we're still in CLIP when the
        // final text lands, LVGL records "no scroll needed" and the
        // subsequent set_long_mode(SCROLL_CIRCULAR) doesn't spawn a fresh
        // animation. Result was a one-shot scroll, then a hard snap-back
        // on the natural wrap, then a perceived re-render. Order matters.
        lv_label_set_long_mode(a->label, a->saved_long_mode);
        lv_label_set_text(a->label, a->buf);
        free_slot(a);
    }
}

// ─── Public API ────────────────────────────────────────────────────────────

void set_text(lv_obj_t *label, const char *new_text) {
    if (!label || !new_text) return;

    int new_len = (int)strnlen(new_text, MAX_LEN);
    const char *old_text = lv_label_get_text(label);

    // If a flap is already running on this label, compare against its
    // settled target instead of the label's current text — the label
    // currently shows a random-glyph frame mid-cycle, which would never
    // match the new_text and would needlessly restart the animation
    // (Dirty::ALL after PAL: was the field-reported trigger).
    Anim *running = find_existing(label);
    if (running) {
        if (running->new_len == new_len &&
            memcmp(running->target, new_text, new_len) == 0) {
            return;   // already heading there
        }
    } else if (old_text && strcmp(old_text, new_text) == 0) {
        return;   // label already shows it, no flap needed
    }

    int old_len = old_text ? (int)strnlen(old_text, MAX_LEN) : 0;
    int positions = old_len > new_len ? old_len : new_len;

    if (positions == 0) {
        lv_label_set_text(label, new_text);
        return;
    }

    // Get or allocate a slot. If we run out (shouldn't happen with
    // MAX_ANIMS=4), just set the text directly.
    Anim *a = find_existing(label);
    if (a) {
        // Already animating on this label — keep saved_long_mode untouched
        // (still holds the *original* mode from before the in-flight flap)
        // and just kill the timer so we can restart with the new target.
        if (a->timer) { lv_timer_del(a->timer); a->timer = nullptr; }
    } else {
        a = alloc_slot(label);
        if (!a) {
            lv_label_set_text(label, new_text);
            return;
        }
        // Fresh slot — capture the label's current long-mode so we can
        // put it back after the flap. Then freeze it to CLIP so LVGL's
        // SCROLL_CIRCULAR doesn't restart on every per-tick set_text.
        a->saved_long_mode = lv_label_get_long_mode(label);
        lv_label_set_long_mode(label, LV_LABEL_LONG_CLIP);
    }

    a->positions = positions;
    a->new_len   = new_len;
    a->tick      = 0;

    // Direction of the staggered cycling wave:
    //  - growing / same length → left-to-right (chars build up on the left)
    //  - shrinking             → right-to-left (the long tail collapses
    //    from the right end first, the surviving left chars flap last)
    // Going left-to-right while shrinking made the tail dissolve position
    // by position from the LEFT, which the eye reads as a sequential
    // erase rather than an animation. Reversing it gives the classic
    // station-board "curtain pulled toward the left" look.
    bool reverse = (new_len < old_len);

    // Seed buf with RANDOM glyphs (not the old text). Why: when the label
    // was mid-scroll showing characters N..N+visible, switching long-mode
    // to CLIP would re-render the label from offset 0 — the user would
    // see a visible "snap" from the mid-scroll position to "start of old
    // text" before the flap cycle even begins. Painting random glyphs
    // from frame 0 masks that jump — the eye reads the random chars as
    // "the flap has started" rather than "the text repositioned".
    for (int i = 0; i < positions; i++) {
        a->buf[i]       = FLAP_CHARS[random(0, FLAP_CHARS_LEN)];
        a->target[i]    = (i < new_len) ? new_text[i] : ' ';
        a->pos_ticks[i] = 0;
        int stagger_idx = reverse ? (positions - 1 - i) : i;
        a->pos_start[i] = (int8_t)(stagger_idx * FLAP_POS_STAGGER);
    }
    a->buf[positions]    = '\0';
    a->target[positions] = '\0';

    // Paint the scrambled initial frame immediately so the cross-over from
    // old text → flap is invisible.
    lv_label_set_text(label, a->buf);

    a->timer = lv_timer_create(tick_cb, FLAP_TICK_MS, a);
}

}  // namespace SplitFlap
