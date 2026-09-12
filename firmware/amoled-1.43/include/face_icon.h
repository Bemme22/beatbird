#pragma once
// =============================================================================
// face_icon.h — hero icons for standby faces, drawn as geometry
// =============================================================================
// See face_icon.cpp for why these are shapes rather than glyphs. Draw them
// from an LV_EVENT_DRAW_MAIN handler; `area` is the slot to fill (the icon
// centres itself in the largest square that fits).
// =============================================================================

#include <lvgl.h>

#include "faces.h"

namespace FaceIcon {

void draw(lv_layer_t *layer, const lv_area_t &area, Faces::IconId icon,
          lv_color_t color);

}  // namespace FaceIcon
