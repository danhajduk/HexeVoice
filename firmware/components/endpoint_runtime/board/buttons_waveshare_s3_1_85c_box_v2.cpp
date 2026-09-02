#include "board/buttons.h"

#include "board/pins.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_buttons_ws185";
}

namespace hexe::board {

void init_buttons() {
  ESP_LOGW(
      kTag,
      "PLACEHOLDER: Waveshare button adapter not implemented yet; BOOT GPIO=%d is documented but not bound to UI actions",
      pins::kBootButton);
}

void update_buttons() {
}

}  // namespace hexe::board
