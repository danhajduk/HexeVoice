#pragma once

namespace hexe::board {

void init_display();
void show_black_frame();
void turn_on_backlight();
void render_boot_frame(int frame, const char *build_id);
void request_display_assets_reload();
bool show_next_ui_page();
bool show_previous_ui_page();
bool display_ready();
int display_width();
int display_height();
const char *display_pixel_format();
int display_last_asset_read_ms();
int display_last_flush_ms();
int display_last_render_ms();
const char *display_last_asset_filename();

}  // namespace hexe::board
