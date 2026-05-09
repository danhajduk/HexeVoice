#include "board/display.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_display_none";
}

namespace hexe::board {

void init_display() {
  ESP_LOGI(kTag, "Display disabled for this board profile");
}

void show_black_frame() {
}

void turn_on_backlight() {
}

void render_boot_frame(int frame, const char *build_id) {
  (void)frame;
  (void)build_id;
}

void request_display_assets_reload() {
}

bool display_ready() {
  return false;
}

int display_width() {
  return 0;
}

int display_height() {
  return 0;
}

const char *display_pixel_format() {
  return "none";
}

}  // namespace hexe::board
