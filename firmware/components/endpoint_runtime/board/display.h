#pragma once

namespace hexe::board {

struct DisplayButtonHit {
  char screen_id[24] = {};
  char button_id[24] = {};
  int index = 0;
  int x = 0;
  int y = 0;
  int width = 0;
  int height = 0;
};

void init_display();
void show_black_frame();
void turn_on_backlight();
void render_boot_frame(int frame, const char *build_id);
void request_display_assets_reload();
bool show_next_ui_page();
bool show_previous_ui_page();
bool display_activity_zone_contains(int x, int y);
bool display_button_hit_test(int x, int y, DisplayButtonHit *hit);
bool display_ready();
int display_width();
int display_height();
const char *display_pixel_format();
int display_last_asset_read_ms();
int display_last_flush_ms();
int display_last_render_ms();
const char *display_last_asset_filename();

}  // namespace hexe::board
