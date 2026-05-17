#include "board/touch.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_touch_none";
}

namespace hexe::board {

void init_touch() {
  ESP_LOGI(kTag, "Touch disabled for this board profile");
}

void update_touch() {
}

bool touch_ready() {
  return false;
}

}  // namespace hexe::board
