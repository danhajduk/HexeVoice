#include "board/xvf3800_audio_bus.h"

#include "board/pins.h"
#include "driver/i2s_std.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

namespace {
constexpr char kTag[] = "hexe_xvf_i2s";
constexpr int kSampleRate = 16000;
constexpr int kFrameSamples = 320;
constexpr int kI2sPort = hexe::board::pins::kXvf3800I2sPort;
constexpr gpio_num_t kBclk = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2sBclk);
constexpr gpio_num_t kLrclk = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2sLrclk);
constexpr gpio_num_t kDin = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2sDin);
constexpr gpio_num_t kDout = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2sDout);

i2s_chan_handle_t g_tx_channel = nullptr;
i2s_chan_handle_t g_rx_channel = nullptr;
bool g_tx_enabled = false;
bool g_rx_enabled = false;

esp_err_t set_rx_enabled(bool enabled) {
  if (g_rx_channel == nullptr || g_rx_enabled == enabled) {
    return ESP_OK;
  }
  const esp_err_t result = enabled ? i2s_channel_enable(g_rx_channel) : i2s_channel_disable(g_rx_channel);
  if (result == ESP_OK) {
    g_rx_enabled = enabled;
  }
  return result;
}

esp_err_t set_tx_enabled(bool enabled) {
  if (g_tx_channel == nullptr || g_tx_enabled == enabled) {
    return ESP_OK;
  }
  const esp_err_t result = enabled ? i2s_channel_enable(g_tx_channel) : i2s_channel_disable(g_tx_channel);
  if (result == ESP_OK) {
    g_tx_enabled = enabled;
  }
  return result;
}
}  // namespace

namespace hexe::board {

bool xvf3800_audio_bus_init() {
  if (g_rx_channel != nullptr && g_tx_channel != nullptr) {
    return true;
  }
  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(kI2sPort, I2S_ROLE_MASTER);
  channel_config.dma_desc_num = 6;
  channel_config.dma_frame_num = kFrameSamples;
  esp_err_t result = i2s_new_channel(&channel_config, &g_tx_channel, &g_rx_channel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create XVF3800 I2S channels: %s", esp_err_to_name(result));
    return false;
  }

  i2s_std_config_t std_config = {};
  std_config.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSampleRate);
  std_config.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
  std_config.gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = kBclk,
      .ws = kLrclk,
      .dout = kDout,
      .din = kDin,
      .invert_flags = {},
  };

  result = i2s_channel_init_std_mode(g_rx_channel, &std_config);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to initialize XVF3800 I2S RX: %s", esp_err_to_name(result));
    return false;
  }
  result = i2s_channel_init_std_mode(g_tx_channel, &std_config);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to initialize XVF3800 I2S TX: %s", esp_err_to_name(result));
    return false;
  }
  result = set_rx_enabled(true);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to enable XVF3800 I2S RX: %s", esp_err_to_name(result));
    return false;
  }
  ESP_LOGI(kTag, "XVF3800 I2S ready: bclk=%d lrclk=%d din=%d dout=%d", kBclk, kLrclk, kDin, kDout);
  return true;
}

bool xvf3800_audio_rx_ready() {
  return g_rx_channel != nullptr && g_rx_enabled;
}

bool xvf3800_audio_tx_ready() {
  return g_tx_channel != nullptr;
}

esp_err_t xvf3800_audio_rx_read(void *buffer, size_t size, size_t *bytes_read, uint32_t timeout_ms) {
  if (g_rx_channel == nullptr) {
    return ESP_ERR_INVALID_STATE;
  }
  return i2s_channel_read(g_rx_channel, buffer, size, bytes_read, pdMS_TO_TICKS(timeout_ms));
}

esp_err_t xvf3800_audio_rx_pause() {
  return set_rx_enabled(false);
}

esp_err_t xvf3800_audio_rx_resume() {
  return set_rx_enabled(true);
}

esp_err_t xvf3800_audio_tx_write(const void *buffer, size_t size, size_t *bytes_written, uint32_t timeout_ms) {
  if (g_tx_channel == nullptr) {
    return ESP_ERR_INVALID_STATE;
  }
  esp_err_t result = set_tx_enabled(true);
  if (result != ESP_OK) {
    return result;
  }
  return i2s_channel_write(g_tx_channel, buffer, size, bytes_written, pdMS_TO_TICKS(timeout_ms));
}

esp_err_t xvf3800_audio_tx_stop() {
  return set_tx_enabled(false);
}

}  // namespace hexe::board
