#include "board/touch.h"

#include "board/pins.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_touch_ws185";
}

namespace hexe::board {

void init_touch() {
  ESP_LOGW(
      kTag,
      "PLACEHOLDER: Waveshare CST816S touch adapter not implemented yet; INT GPIO=%d, I2C address=0x%02x",
      pins::kWs185TouchInterrupt,
      pins::kWs185TouchAddress);
}

void update_touch() {
}

bool touch_ready() {
  return false;
}

}  // namespace hexe::board
