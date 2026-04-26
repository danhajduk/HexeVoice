#pragma once

namespace hexe::board {

void init_display();
void show_black_frame();
void turn_on_backlight();
void render_boot_frame(int frame, const char *build_id);
bool display_ready();
int display_width();
int display_height();
const char *display_pixel_format();

}  // namespace hexe::board
