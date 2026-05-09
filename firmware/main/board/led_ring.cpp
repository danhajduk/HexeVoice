#include "board/led_ring.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_led_ring_none";
}

namespace hexe::board {

void init_led_ring() {
  ESP_LOGI(kTag, "LED ring disabled for this board profile");
}

bool led_ring_available() {
  return false;
}

esp_err_t led_ring_off() {
  return ESP_OK;
}

esp_err_t led_ring_set_solid(
    uint8_t red,
    uint8_t green,
    uint8_t blue,
    uint8_t brightness,
    bool diagnostic) {
  (void)red;
  (void)green;
  (void)blue;
  (void)brightness;
  (void)diagnostic;
  return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t led_ring_set_visual_frame(
    const LedRingColor *visual_colors,
    size_t visual_color_count,
    uint8_t brightness,
    bool diagnostic) {
  (void)visual_colors;
  (void)visual_color_count;
  (void)brightness;
  (void)diagnostic;
  return ESP_ERR_NOT_SUPPORTED;
}

}  // namespace hexe::board
