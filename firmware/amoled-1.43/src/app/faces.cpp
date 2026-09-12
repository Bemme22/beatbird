// =============================================================================
// app/faces.cpp — standby face store (see include/faces.h for the why)
// =============================================================================
#include "faces.h"

#include <stdlib.h>
#include <string.h>

namespace Faces {

// Two fixed sets: one being filled, one on screen. No heap — this firmware is
// already at ~88 % of dram0_0_seg, and a face set is small and bounded anyway.
static Face    s_live[MAX_FACES];
static Face    s_stage[MAX_FACES];
static int     s_live_count  = 0;
static int     s_stage_count = 0;
static int     s_dwell_s     = 8;
static uint32_t s_revision   = 0;

// ─── Field parsing ──────────────────────────────────────────────────────────
// Body form: id=luften|prio=40|big=3.4|unit=K|top=LUEFTEN|bot=aussen 9.8
// Values may contain spaces but never '|' — the Pi strips those, so a plain
// scan to the next '|' is sufficient and there is no escaping to get wrong.

static bool field(const char *body, const char *key, char *out, size_t out_size)
{
    const size_t key_len = strlen(key);
    const char  *p       = body;

    while (p && *p) {
        // Match only at a field boundary, so "top=" cannot be found inside
        // a value like "bot=rooftop=3".
        if (!strncmp(p, key, key_len) && p[key_len] == '=') {
            p += key_len + 1;
            const char *end = strchr(p, '|');
            size_t len = end ? (size_t)(end - p) : strlen(p);
            if (len >= out_size) len = out_size - 1;
            memcpy(out, p, len);
            out[len] = '\0';
            return true;
        }
        p = strchr(p, '|');
        if (p) p++;
    }
    out[0] = '\0';
    return false;
}

static void add_staged(const char *body)
{
    if (s_stage_count >= MAX_FACES) return;   // Pi caps this too; belt and braces

    Face &f = s_stage[s_stage_count];
    memset(&f, 0, sizeof(f));

    // The id is required on the wire (it is what makes a face addressable for
    // the publisher) but nothing here renders it, so it is validated on the
    // stack and dropped rather than carried in the set. Same for prio: the Pi
    // has already sorted the batch, so the order of arrival IS the priority.
    char scratch[8];
    if (!field(body, "id", scratch, sizeof(scratch)) || !scratch[0]) return;
    // A face with nothing far-readable on it is exactly what the concept rules
    // out, so drop it here rather than render an empty hero slot.
    if (!field(body, "big", f.big, LEN_BIG) || !f.big[0]) return;

    field(body, "unit", f.unit, LEN_UNIT);
    field(body, "top",  f.top,  LEN_TOP);
    field(body, "bot",  f.bot,  LEN_BOT);

    s_stage_count++;
}

static void commit(const char *body)
{
    char num[8];
    if (field(body, "dwell", num, sizeof(num))) {
        int d = atoi(num);
        if (d >= 2 && d <= 120) s_dwell_s = d;
    }
    memcpy(s_live, s_stage, sizeof(s_live));
    s_live_count  = s_stage_count;
    s_stage_count = 0;
    s_revision++;
}

// ─── Public API ─────────────────────────────────────────────────────────────

void handle_line(const char *body)
{
    if (!body) return;
    if (!strncmp(body, "end", 3)) commit(body);
    else                          add_staged(body);
}

int count() { return s_live_count; }

const Face *at(int index)
{
    if (index < 0 || index >= s_live_count) return nullptr;
    return &s_live[index];
}

int      dwell_s()  { return s_dwell_s; }
uint32_t revision() { return s_revision; }

}  // namespace Faces
