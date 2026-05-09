#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"

namespace hexe::board {

struct LedRingColor {
  uint8_t red;
  uint8_t green;
  uint8_t blue;
};

constexpr uint8_t kLedRingDefaultBrightness = 24;
constexpr uint8_t kLedRingNormalBrightnessCap = 48;
constexpr uint8_t kLedRingDiagnosticBrightnessCap = 96;

void init_led_ring();
bool led_ring_available();
esp_err_t led_ring_off();
esp_err_t led_ring_set_solid(
    uint8_t red,
    uint8_t green,
    uint8_t blue,
    uint8_t brightness = kLedRingDefaultBrightness,
    bool diagnostic = false);
esp_err_t led_ring_set_visual_frame(
    const LedRingColor *visual_colors,
    size_t visual_color_count,
    uint8_t brightness = kLedRingDefaultBrightness,
    bool diagnostic = false);
void update_led_ring_patterns();
void led_ring_show_completed();
void led_ring_show_cancelled();

}  // namespace hexe::board
