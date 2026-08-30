#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"

namespace hexe::board {

bool xvf3800_audio_bus_init();
bool xvf3800_audio_rx_ready();
bool xvf3800_audio_tx_ready();
esp_err_t xvf3800_audio_rx_read(void *buffer, size_t size, size_t *bytes_read, uint32_t timeout_ms);
esp_err_t xvf3800_audio_rx_pause();
esp_err_t xvf3800_audio_rx_resume();
esp_err_t xvf3800_audio_tx_write(const void *buffer, size_t size, size_t *bytes_written, uint32_t timeout_ms);
esp_err_t xvf3800_audio_tx_stop();

}  // namespace hexe::board
