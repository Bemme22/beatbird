// =============================================================================
// ui/face_icon.cpp — the standby faces' hero icons, drawn as geometry
// =============================================================================
// Why shapes and not a font: the big slot uses `inter_clock`, which is subset
// to "0123456789:. °-+" — it has no letters, let alone pictograms. Adding an
// icon font would mean a second typeface in flash plus a second text style,
// for three shapes that are a handful of rounded rectangles each. The player's
// spinner already draws itself this way (center_stage.cpp), so this follows a
// pattern the codebase has rather than introducing one.
//
// Everything is expressed in a 0..100 unit box and scaled by the caller, so an
// icon can sit in the 140 px hero slot today and somewhere else tomorrow
// without re-tuning every constant.
// =============================================================================

#include "face_icon.h"

#include "theme.h"

namespace FaceIcon {

// ─── Primitives ─────────────────────────────────────────────────────────────

struct Box {          // the icon's pixel frame; unit coords map into it
    int x, y, w, h;
};

static inline int sx(const Box &b, int u) { return b.x + (u * b.w) / 100; }
static inline int sy(const Box &b, int u) { return b.y + (u * b.h) / 100; }

/** Filled/outlined rounded rectangle in unit coordinates. `radius_u` of 50
 *  gives a circle for a square area. */
static void rect(lv_layer_t *layer, const Box &b,
                 int x1, int y1, int x2, int y2,
                 lv_color_t color, lv_opa_t opa, int radius_u, int border_u)
{
    lv_draw_rect_dsc_t dsc;
    lv_draw_rect_dsc_init(&dsc);
    const int r_px = (radius_u * b.w) / 100;

    if (border_u > 0) {
        dsc.bg_opa       = LV_OPA_TRANSP;
        dsc.border_color = color;
        dsc.border_opa   = opa;
        dsc.border_width = (border_u * b.w) / 100;
        if (dsc.border_width < 1) dsc.border_width = 1;
    } else {
        dsc.bg_color = color;
        dsc.bg_opa   = opa;
    }
    dsc.radius = r_px;

    lv_area_t a = { sx(b, x1), sy(b, y1), sx(b, x2), sy(b, y2) };
    lv_draw_rect(layer, &dsc, &a);
}

/** Thick line in unit coordinates, rounded ends. Rectangles alone cannot
 *  express a diagonal, and a bolt drawn from stacked bars reads as a staircase
 *  — verified in the simulator before this helper existed. */
static void line(lv_layer_t *layer, const Box &b,
                 int x1, int y1, int x2, int y2,
                 lv_color_t color, lv_opa_t opa, int width_u)
{
    lv_draw_line_dsc_t dsc;
    lv_draw_line_dsc_init(&dsc);
    dsc.color       = color;
    dsc.opa         = opa;
    dsc.width       = (width_u * b.w) / 100;
    if (dsc.width < 1) dsc.width = 1;
    dsc.round_start = 1;
    dsc.round_end   = 1;
    dsc.p1 = { (lv_value_precise_t)sx(b, x1), (lv_value_precise_t)sy(b, y1) };
    dsc.p2 = { (lv_value_precise_t)sx(b, x2), (lv_value_precise_t)sy(b, y2) };
    lv_draw_line(layer, &dsc);
}

// ─── The icons ──────────────────────────────────────────────────────────────
// Each is deliberately readable at a glance from 3 m, which means few parts and
// generous strokes — the same reason the big value is one number.

static void draw_wash(lv_layer_t *l, const Box &b, lv_color_t c)
{
    // Body, then the drum as a ring, then the door catch. A washing machine is
    // recognisable from "box with a circle in it" alone; the details are what
    // make it read as a machine rather than a camera.
    rect(l, b,  8,  4,  92, 96, c, LV_OPA_COVER, 12, 7);   // cabinet outline
    rect(l, b, 14, 14,  40, 24, c, LV_OPA_60,    50, 0);   // detergent drawer
    rect(l, b, 26, 36,  74, 84, c, LV_OPA_COVER, 50, 6);   // drum ring
    rect(l, b, 44, 54,  56, 66, c, LV_OPA_40,    50, 0);   // porthole centre
}

static void draw_bolt(lv_layer_t *l, const Box &b, lv_color_t c)
{
    // A zig-zag of three thick strokes. Built from bars first, which read as a
    // staircase — a bolt is nothing BUT its diagonals, so it needs real lines.
    line(l, b, 62,  6,  34, 48, c, LV_OPA_COVER, 13);
    line(l, b, 34, 48,  62, 48, c, LV_OPA_COVER, 13);
    line(l, b, 62, 48,  36, 94, c, LV_OPA_COVER, 13);
}

static void draw_window(lv_layer_t *l, const Box &b, lv_color_t c)
{
    // Frame with a cross mullion — the universally read "window". The first
    // attempt drew a second sash swung open beside it and looked like two
    // rectangles; "open" is what the label says, the icon only has to say
    // WHICH thing is open.
    rect(l, b, 10, 10,  90, 90, c, LV_OPA_COVER, 6, 8);    // frame
    line(l, b, 50, 14,  50, 86, c, LV_OPA_COVER, 7);       // vertical mullion
    line(l, b, 14, 50,  86, 50, c, LV_OPA_COVER, 7);       // horizontal mullion
}

static void draw_alert(lv_layer_t *l, const Box &b, lv_color_t c)
{
    // Ring with an exclamation — no triangle primitive needed, and a circle
    // sits better inside a round panel anyway.
    rect(l, b,  8,  8,  92, 92, c, LV_OPA_COVER, 50, 7);
    rect(l, b, 44, 26,  56, 62, c, LV_OPA_COVER,  6, 0);
    rect(l, b, 44, 70,  56, 82, c, LV_OPA_COVER, 50, 0);
}

// ─── Entry point ────────────────────────────────────────────────────────────

void draw(lv_layer_t *layer, const lv_area_t &area, Faces::IconId icon,
          lv_color_t color)
{
    // Keep the drawing square and centred: every icon above is designed in a
    // square unit box, and stretching them to a wide slot would shear the
    // circles into ellipses.
    const int aw   = area.x2 - area.x1;
    const int ah   = area.y2 - area.y1;
    const int side = (aw < ah) ? aw : ah;
    const Box b = { area.x1 + (aw - side) / 2, area.y1 + (ah - side) / 2,
                    side, side };

    switch (icon) {
        case Faces::ICON_WASH:   draw_wash  (layer, b, color); break;
        case Faces::ICON_BOLT:   draw_bolt  (layer, b, color); break;
        case Faces::ICON_WINDOW: draw_window(layer, b, color); break;
        case Faces::ICON_ALERT:  draw_alert (layer, b, color); break;
        default: break;   // ICON_NONE draws nothing — the caller shows digits
    }
}

}  // namespace FaceIcon
