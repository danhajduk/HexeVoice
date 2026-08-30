#include "board/led_ring.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

#include "board/xvf3800_control.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_led_xvf";
constexpr size_t kLedCount = 12;
std::array<uint8_t, kLedCount * 4> g_ring_frame = {};

uint8_t capped_brightness(uint8_t brightness, bool diagnostic) {
  const uint8_t cap = diagnostic ? hexe::board::kLedRingDiagnosticBrightnessCap
                                 : hexe::board::kLedRingNormalBrightnessCap;
  return std::min<uint8_t>(brightness, cap);
}
}  // namespace

namespace hexe::board {

void init_led_ring() {
  if (!xvf3800_control_init()) {
    ESP_LOGE(kTag, "XVF3800 controls unavailable; LED ring disabled");
    return;
  }
  xvf3800_led_solid(255, 120, 0, kLedRingDiagnosticBrightnessCap);
  ESP_LOGI(kTag, "XVF3800 LED ring ready through I2C LED engine");
}

bool led_ring_available() {
  return xvf3800_control_ready();
}

esp_err_t led_ring_off() {
  return xvf3800_led_off();
}

esp_err_t led_ring_set_solid(
    uint8_t red,
    uint8_t green,
    uint8_t blue,
    uint8_t brightness,
    bool diagnostic) {
  return xvf3800_led_solid(red, green, blue, capped_brightness(brightness, diagnostic));
}

esp_err_t led_ring_set_visual_frame(
    const LedRingColor *visual_colors,
    size_t visual_color_count,
    uint8_t brightness,
    bool diagnostic) {
  if (visual_colors == nullptr || visual_color_count == 0) {
    return ESP_ERR_INVALID_ARG;
  }
  for (size_t index = 0; index < kLedCount; ++index) {
    const LedRingColor &color = visual_colors[std::min(index, visual_color_count - 1)];
    g_ring_frame[index * 4] = color.red;
    g_ring_frame[(index * 4) + 1] = color.green;
    g_ring_frame[(index * 4) + 2] = color.blue;
    g_ring_frame[(index * 4) + 3] = 0;
  }
  return xvf3800_led_ring_frame(g_ring_frame.data(), g_ring_frame.size(), capped_brightness(brightness, diagnostic));
}

void update_led_ring_patterns() {
}

void led_ring_show_completed() {
  xvf3800_led_solid(80, 255, 180, kLedRingNormalBrightnessCap);
}

void led_ring_show_volume(int volume_percent) {
  const uint8_t blue = static_cast<uint8_t>(std::clamp(volume_percent, 0, 100) * 2);
  xvf3800_led_solid(40, 120, blue, kLedRingNormalBrightnessCap);
}

void led_ring_adjust_accent_hue(int delta_steps) {
  (void)delta_steps;
  xvf3800_led_solid(80, 180, 255, kLedRingNormalBrightnessCap);
}

bool led_ring_simulate_pattern(const char *pattern_name, int duration_ms) {
  (void)duration_ms;
  if (pattern_name == nullptr) {
    return false;
  }
  return xvf3800_led_solid(255, 120, 0, kLedRingDiagnosticBrightnessCap) == ESP_OK;
}

}  // namespace hexe::board
