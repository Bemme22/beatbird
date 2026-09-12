#pragma once
// =============================================================================
// faces.h — standby "faces": one glanceable decision per screen
// =============================================================================
// A face is one thing worth looking up for, rendered in the standby clock's
// geometry (small label above, one big value, small detail below). The bridge
// pushes them as FACE: lines; HA decides what they say.
//
// Why exactly ONE big value per face, and why it must be a number or a symbol:
// the panel measures 0.095 mm/px, so the 140 px standby clock subtends ~11
// arcmin at 3 m while the 40 px player title manages ~3.2 — below the ~5 arcmin
// that 20/20 vision resolves. Digits survive that because they are ten known
// shapes read as patterns; arbitrary words at the same size do not. So the big
// slot carries digits or a symbol token, and everything else on the face is for
// arm's length. (HA.md, "Display concept".)
//
// The firmware deliberately holds no opinion about WHAT earns a face: the
// derivation (rate of change, sensor comparison, state transition) lives in HA,
// which has the history and the templates. A line-based ASCII protocol has no
// business computing dew points. Notifications are not a second mechanism —
// they are entries in this same list with a high priority and a short ttl.
//
// Storage is fixed-size on purpose. This firmware sits at ~88 % of dram0_0_seg
// before anything is added, so a handful of small char arrays is the budget.
//
// ⚠️ That budget is not theoretical: the first version of this file stored an
// `id` and a `prio` it never rendered, and budgeted `bot` at 48 chars, and the
// two staging sets that resulted overflowed dram0_0_seg by 352 bytes in EVERY
// ESP32 env. The simulator cannot show this (a desktop has no dram0_0_seg), so
// a face set that fits on screen still has to be checked against the linker.
// Rule for this struct: a field that is never drawn does not get stored, and a
// field that is drawn is budgeted at the width that actually fits.
// =============================================================================

#include <stdint.h>

namespace Faces {

// Field budgets mirror the Pi side (src/beatbird/ha/faces.py), which already
// folds text to ASCII, sorts by priority, caps the count and strips the
// protocol's own delimiters — the firmware never has to unescape anything. It
// keeps no `id` at all and only one BIT of `prio`: faces are addressed by index
// and rendered in the order received, so the ranking is already in the order,
// and the only consequence priority has here is the status strip's colour.
//
// MAX_FACES is MEASURED, not chosen for looks: at 6 the link leaves 80 bytes of
// dram0_0_seg, at 7 it is 56 bytes short. 4 is what the profiles actually
// configure (and what the concept calls a rotation rather than wallpaper), and
// it buys back 272 bytes — capacity nobody uses is not free here.
constexpr int MAX_FACES = 4;
constexpr int LEN_BIG   = 11;
constexpr int LEN_UNIT  = 5;
constexpr int LEN_TOP   = 17;
// 34 chars is the measured limit of the detail line, not a round number: at
// font_sm with letter_space 2 the 32-char example in docs/protocol.md spans
// ~296 of the label's 320 px. Anything longer wraps to a second line that
// would sit against the round bezel, so the Pi truncates to what fits.
constexpr int LEN_BOT   = 35;

// What the big slot can show when a number would be meaningless. Drawn as
// geometry (see ui/face_icon.cpp), not as a glyph: the clock font is subset to
// digits, and pulling in an icon font for three shapes would cost flash and a
// second text style for nothing.
//
// An icon is stored as a byte, not as the wire's name string — 4 faces × 2
// staging sets means every byte of this struct is multiplied by 8, and the
// name is only needed while parsing one line.
enum IconId : uint8_t {
    ICON_NONE = 0,
    ICON_WASH,      // washing machine: drum in a box
    ICON_BOLT,      // energy
    ICON_WINDOW,    // ventilate
    ICON_ALERT,     // generic "look at this"
};

// Above this priority a face is treated as an exception rather than a note:
// the status strip renders it in accent_alert instead of the accent. The
// THRESHOLD lives here and the raw 0..100 value does not, because nothing on
// this side needs the number — only the answer to "is this urgent".
constexpr int PRIO_URGENT = 80;

struct Face {
    char    big [LEN_BIG];    // digits — never a word (the font has no letters)
    char    unit[LEN_UNIT];
    char    top [LEN_TOP];
    char    bot [LEN_BOT];
    IconId  icon;             // when set, it replaces `big` in the hero slot
    bool    urgent;           // prio >= PRIO_URGENT — drives the strip's colour
};

/** Map a wire name ("wash") to its id. ICON_NONE for unknown — an unknown
 *  icon must degrade to the numeric path, never to an empty screen. */
IconId icon_from_name(const char *name);

/** Feed one FACE: line body (everything after "FACE:").
 *
 *  Entries accumulate into a staging set; `end` commits it in one swap. That
 *  batch boundary matters: a face that vanished from the set is then genuinely
 *  gone, and a half-delivered batch (USB hiccup, reset mid-stream) leaves the
 *  previous set standing rather than a mixture of the two. */
void handle_line(const char *body);

/** Number of live faces (0 = standby shows only its clock). */
int count();

/** Live face by index, highest priority first; nullptr if out of range. */
const Face *at(int index);

/** True while any live face is urgent. The status strip asks this rather than
 *  walking the set — it renders from its own task at frame rate. */
bool any_urgent();

/** Seconds per face in the standby rotation, as configured in the profile.
 *  Arrives on the batch terminator, so it is never out of step with the set. */
int dwell_s();

/** Bumped on every committed batch. The standby screen compares this instead
 *  of diffing strings, so an unchanged re-push (the bridge replays the set
 *  after an ESP32 reboot) costs one integer comparison. */
uint32_t revision();

}  // namespace Faces
