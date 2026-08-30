#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"

namespace hexe::board {

bool xvf3800_control_init();
bool xvf3800_control_ready();
bool xvf3800_read_version(uint8_t version[3]);
bool xvf3800_read_gpi_values(uint8_t values[3]);
bool xvf3800_set_mute(bool muted);
esp_err_t xvf3800_led_off();
esp_err_t xvf3800_led_solid(uint8_t red, uint8_t green, uint8_t blue, uint8_t brightness);
esp_err_t xvf3800_led_ring_frame(const uint8_t *rgb_bytes, size_t rgb_byte_count, uint8_t brightness);

}  // namespace hexe::board
