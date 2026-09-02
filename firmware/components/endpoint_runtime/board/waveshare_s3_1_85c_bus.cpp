#include "board/waveshare_s3_1_85c_bus.h"

#include "board/pins.h"
#include "driver/gpio.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_ws185_bus";
constexpr uint8_t kTcaRegisterOutput = 0x01;
constexpr uint8_t kTcaRegisterConfig = 0x03;
constexpr uint8_t kTouchResetBit = 1;
constexpr uint8_t kDisplayResetBit = 2;
constexpr int kI2cTimeoutMs = 1000;
constexpr int kAudioSampleRate = 16000;
constexpr int kAudioFrameSamples = 320;

i2c_master_bus_handle_t g_i2c_bus = nullptr;
i2c_master_dev_handle_t g_io_expander = nullptr;
i2s_chan_handle_t g_i2s_tx = nullptr;
i2s_chan_handle_t g_i2s_rx = nullptr;
uint8_t g_io_expander_output = 0xFF;
bool g_i2s_initialized = false;
bool g_speaker_pa_initialized = false;

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

bool tca_write(uint8_t reg, uint8_t value) {
  if (g_io_expander == nullptr) {
    return false;
  }
  const uint8_t data[] = {reg, value};
  const esp_err_t result = i2c_master_transmit(g_io_expander, data, sizeof(data), kI2cTimeoutMs);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "TCA9554 write failed: reg=0x%02x value=0x%02x err=%s", reg, value, esp_err_to_name(result));
    return false;
  }
  return true;
}

bool init_io_expander() {
  if (g_io_expander != nullptr) {
    return true;
  }
  if (!hexe::board::waveshare_185_init_i2c()) {
    return false;
  }

  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = hexe::board::pins::kWs185IoExpanderAddress;
  device_config.scl_speed_hz = hexe::board::pins::kWs185I2cClockHz;

  esp_err_t result = i2c_master_bus_add_device(g_i2c_bus, &device_config, &g_io_expander);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(kTag, "Failed to add TCA9554 I2C device: %s", esp_err_to_name(result));
    g_io_expander = nullptr;
    return false;
  }

  const uint8_t output_mask = static_cast<uint8_t>((1U << kTouchResetBit) | (1U << kDisplayResetBit));
  const uint8_t config_value = static_cast<uint8_t>(~output_mask);
  g_io_expander_output |= output_mask;
  return tca_write(kTcaRegisterOutput, g_io_expander_output) && tca_write(kTcaRegisterConfig, config_value);
}

bool set_expander_bit(uint8_t bit, bool high) {
  if (!init_io_expander()) {
    return false;
  }
  if (high) {
    g_io_expander_output = static_cast<uint8_t>(g_io_expander_output | (1U << bit));
  } else {
    g_io_expander_output = static_cast<uint8_t>(g_io_expander_output & ~(1U << bit));
  }
  return tca_write(kTcaRegisterOutput, g_io_expander_output);
}

bool pulse_reset(uint8_t bit, uint32_t low_ms, uint32_t settle_ms) {
  if (!set_expander_bit(bit, false)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(low_ms));
  if (!set_expander_bit(bit, true)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(settle_ms));
  return true;
}
}  // namespace

namespace hexe::board {

bool waveshare_185_init_i2c() {
  if (g_i2c_bus != nullptr) {
    return true;
  }

  i2c_master_bus_config_t bus_config = {};
  bus_config.i2c_port = static_cast<i2c_port_num_t>(pins::kWs185I2cPort);
  bus_config.sda_io_num = gpio_pin(pins::kWs185I2cSda);
  bus_config.scl_io_num = gpio_pin(pins::kWs185I2cScl);
  bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
  bus_config.glitch_ignore_cnt = 7;
  bus_config.flags.enable_internal_pullup = true;

  esp_err_t result = i2c_new_master_bus(&bus_config, &g_i2c_bus);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(kTag, "Failed to initialize Waveshare I2C bus: %s", esp_err_to_name(result));
    g_i2c_bus = nullptr;
    return false;
  }
  ESP_LOGI(kTag, "Waveshare I2C bus ready on SDA=%d SCL=%d", pins::kWs185I2cSda, pins::kWs185I2cScl);
  return true;
}

i2c_master_bus_handle_t waveshare_185_i2c_bus() {
  return g_i2c_bus;
}

bool waveshare_185_reset_display() {
  return pulse_reset(kDisplayResetBit, 20, 120);
}

bool waveshare_185_reset_touch() {
  return pulse_reset(kTouchResetBit, 10, 50);
}

bool waveshare_185_init_audio_i2s() {
  if (g_i2s_initialized) {
    return true;
  }

  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(pins::kWs185AudioPort, I2S_ROLE_MASTER);
  channel_config.dma_desc_num = 6;
  channel_config.dma_frame_num = kAudioFrameSamples;
  esp_err_t result = i2s_new_channel(&channel_config, &g_i2s_tx, &g_i2s_rx);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Waveshare I2S channels: %s", esp_err_to_name(result));
    return false;
  }

  i2s_std_config_t std_config = {};
  std_config.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kAudioSampleRate);
  std_config.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
  std_config.gpio_cfg = {
      .mclk = gpio_pin(pins::kWs185AudioMclk),
      .bclk = gpio_pin(pins::kWs185AudioBclk),
      .ws = gpio_pin(pins::kWs185AudioLrclk),
      .dout = gpio_pin(pins::kWs185AudioDout),
      .din = gpio_pin(pins::kWs185AudioDin),
      .invert_flags = {},
  };

  result = i2s_channel_init_std_mode(g_i2s_tx, &std_config);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to initialize Waveshare I2S TX: %s", esp_err_to_name(result));
    return false;
  }
  result = i2s_channel_init_std_mode(g_i2s_rx, &std_config);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to initialize Waveshare I2S RX: %s", esp_err_to_name(result));
    return false;
  }

  g_i2s_initialized = true;
  ESP_LOGI(
      kTag,
      "Waveshare I2S ready: port=%d mclk=%d bclk=%d lrclk=%d din=%d dout=%d",
      pins::kWs185AudioPort,
      pins::kWs185AudioMclk,
      pins::kWs185AudioBclk,
      pins::kWs185AudioLrclk,
      pins::kWs185AudioDin,
      pins::kWs185AudioDout);
  return true;
}

i2s_chan_handle_t waveshare_185_audio_rx_channel() {
  return g_i2s_rx;
}

i2s_chan_handle_t waveshare_185_audio_tx_channel() {
  return g_i2s_tx;
}

void waveshare_185_set_speaker_pa(bool enabled) {
  if (!g_speaker_pa_initialized) {
    gpio_config_t output_config = {};
    output_config.pin_bit_mask = 1ULL << pins::kWs185PaCtrl;
    output_config.mode = GPIO_MODE_OUTPUT;
    gpio_config(&output_config);
    g_speaker_pa_initialized = true;
  }
  gpio_set_level(gpio_pin(pins::kWs185PaCtrl), enabled ? 1 : 0);
}

}  // namespace hexe::board
