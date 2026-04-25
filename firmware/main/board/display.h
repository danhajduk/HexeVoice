#pragma once

namespace hexe::board {

void init_display();
void show_black_frame();
void turn_on_backlight();
void render_boot_frame(int frame, const char *build_id);

}  // namespace hexe::board
