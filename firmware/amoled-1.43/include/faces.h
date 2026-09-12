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
// =============================================================================

#include <stdint.h>

namespace Faces {

// Field budgets mirror the Pi side (src/beatbird/ha/faces.py), which already
// folds text to ASCII and strips the protocol's own delimiters — the firmware
// never has to unescape anything.
constexpr int MAX_FACES = 6;
constexpr int LEN_ID    = 17;
constexpr int LEN_BIG   = 11;
constexpr int LEN_UNIT  = 5;
constexpr int LEN_TOP   = 21;
constexpr int LEN_BOT   = 49;

struct Face {
    char    id  [LEN_ID];
    char    big [LEN_BIG];    // digits or a :symbol: token — never a word
    char    unit[LEN_UNIT];
    char    top [LEN_TOP];
    char    bot [LEN_BOT];
    uint8_t prio;             // higher wins; the Pi already sorted them
};

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

/** Seconds per face in the standby rotation, as configured in the profile.
 *  Arrives on the batch terminator, so it is never out of step with the set. */
int dwell_s();

/** Bumped on every committed batch. The standby screen compares this instead
 *  of diffing strings, so an unchanged re-push (the bridge replays the set
 *  after an ESP32 reboot) costs one integer comparison. */
uint32_t revision();

}  // namespace Faces
