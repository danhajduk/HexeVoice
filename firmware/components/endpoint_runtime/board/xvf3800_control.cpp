#include "board/xvf3800_control.h"

#include "board/pins.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_xvf3800";
constexpr i2c_port_num_t kI2cPort = hexe::board::pins::kXvf3800I2cPort;
constexpr gpio_num_t kI2cSda = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2cSda);
constexpr gpio_num_t kI2cScl = static_cast<gpio_num_t>(hexe::board::pins::kXvf3800I2cScl);
constexpr uint32_t kI2cClockHz = static_cast<uint32_t>(hexe::board::pins::kXvf3800I2cClockHz);
constexpr uint8_t kXvf3800Address = static_cast<uint8_t>(hexe::board::pins::kXvf3800I2cAddress);
constexpr uint32_t kI2cTimeoutMs = 1000;
constexpr uint8_t kReadCommandBit = 0x80;
constexpr uint8_t kStatusOk = 0;
constexpr uint8_t kApplicationServicerResid = 48;
constexpr uint8_t kApplicationVersionCommand = 0;
constexpr uint8_t kGpoServicerResid = 20;
constexpr uint8_t kGpoWriteValueCommand = 1;
constexpr uint8_t kLedEffectCommand = 12;
constexpr uint8_t kLedBrightnessCommand = 13;
constexpr uint8_t kLedColorCommand = 16;
constexpr uint8_t kLedRingColorCommand = 19;
constexpr uint8_t kIoConfigServicerResid = 36;
constexpr uint8_t kGpiReadValuesCommand = 6;
constexpr uint8_t kX0D30MuteMicAndLed = 30;
constexpr uint8_t kLedEffectOff = 0;
constexpr uint8_t kLedEffectSolid = 3;
constexpr size_t kLedRingRgbBytes = 12 * 4;

i2c_master_bus_handle_t g_i2c_bus = nullptr;
i2c_master_dev_handle_t g_xvf_device = nullptr;
SemaphoreHandle_t g_i2c_lock = nullptr;
bool g_ready = false;

bool transmit_locked(const uint8_t *data, size_t size) {
  const esp_err_t result = i2c_master_transmit(g_xvf_device, data, size, pdMS_TO_TICKS(kI2cTimeoutMs));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "XVF3800 I2C write failed: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool receive_locked(uint8_t *data, size_t size) {
  const esp_err_t result = i2c_master_receive(g_xvf_device, data, size, pdMS_TO_TICKS(kI2cTimeoutMs));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "XVF3800 I2C read failed: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool write_command(uint8_t resid, uint8_t command, const uint8_t *payload, size_t payload_size) {
  if (!hexe::board::xvf3800_control_init() || payload_size > 64) {
    return false;
  }
  uint8_t request[67] = {resid, command, static_cast<uint8_t>(payload_size)};
  for (size_t index = 0; index < payload_size; ++index) {
    request[index + 3] = payload[index];
  }
  if (xSemaphoreTake(g_i2c_lock, pdMS_TO_TICKS(kI2cTimeoutMs)) != pdTRUE) {
    return false;
  }
  const bool ok = transmit_locked(request, payload_size + 3);
  xSemaphoreGive(g_i2c_lock);
  return ok;
}

bool read_command(uint8_t resid, uint8_t command, uint8_t *payload, size_t payload_size) {
  if (!hexe::board::xvf3800_control_init() || payload == nullptr || payload_size > 64) {
    return false;
  }
  const uint8_t request[] = {
      resid,
      static_cast<uint8_t>(command | kReadCommandBit),
      static_cast<uint8_t>(payload_size + 1),
  };
  uint8_t response[65] = {};
  if (xSemaphoreTake(g_i2c_lock, pdMS_TO_TICKS(kI2cTimeoutMs)) != pdTRUE) {
    return false;
  }
  const bool ok = transmit_locked(request, sizeof(request)) && receive_locked(response, payload_size + 1);
  xSemaphoreGive(g_i2c_lock);
  if (!ok || response[0] != kStatusOk) {
    return false;
  }
  for (size_t index = 0; index < payload_size; ++index) {
    payload[index] = response[index + 1];
  }
  return true;
}

uint32_t rgb(uint8_t red, uint8_t green, uint8_t blue) {
  return static_cast<uint32_t>(red) | (static_cast<uint32_t>(green) << 8) | (static_cast<uint32_t>(blue) << 16);
}
}  // namespace

namespace hexe::board {

bool xvf3800_control_init() {
  if (g_ready) {
    return true;
  }
  if (g_i2c_lock == nullptr) {
    g_i2c_lock = xSemaphoreCreateMutex();
    if (g_i2c_lock == nullptr) {
      ESP_LOGE(kTag, "Failed to create XVF3800 I2C lock");
      return false;
    }
  }
  if (g_i2c_bus == nullptr) {
    i2c_master_bus_config_t bus_config = {};
    bus_config.i2c_port = kI2cPort;
    bus_config.sda_io_num = kI2cSda;
    bus_config.scl_io_num = kI2cScl;
    bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_config.glitch_ignore_cnt = 7;
    bus_config.flags.enable_internal_pullup = true;

    const esp_err_t result = i2c_new_master_bus(&bus_config, &g_i2c_bus);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to create XVF3800 I2C bus: %s", esp_err_to_name(result));
      return false;
    }
  }
  if (g_xvf_device == nullptr) {
    i2c_device_config_t device_config = {};
    device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    device_config.device_address = kXvf3800Address;
    device_config.scl_speed_hz = kI2cClockHz;
    const esp_err_t result = i2c_master_bus_add_device(g_i2c_bus, &device_config, &g_xvf_device);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to add XVF3800 I2C device: %s", esp_err_to_name(result));
      return false;
    }
  }
  uint8_t version[3] = {};
  const uint8_t request[] = {
      kApplicationServicerResid,
      static_cast<uint8_t>(kApplicationVersionCommand | kReadCommandBit),
      static_cast<uint8_t>(sizeof(version) + 1),
  };
  uint8_t response[sizeof(version) + 1] = {};
  if (xSemaphoreTake(g_i2c_lock, pdMS_TO_TICKS(kI2cTimeoutMs)) == pdTRUE) {
    g_ready = transmit_locked(request, sizeof(request)) && receive_locked(response, sizeof(response)) && response[0] == kStatusOk;
    xSemaphoreGive(g_i2c_lock);
  }
  for (size_t index = 0; index < sizeof(version); ++index) {
    version[index] = response[index + 1];
  }
  if (g_ready) {
    ESP_LOGI(kTag, "XVF3800 firmware version %u.%u.%u", version[0], version[1], version[2]);
  } else {
    ESP_LOGE(kTag, "XVF3800 did not respond on I2C; verify I2S firmware and wiring");
  }
  return g_ready;
}

bool xvf3800_control_ready() {
  return g_ready;
}

bool xvf3800_read_version(uint8_t version[3]) {
  return read_command(kApplicationServicerResid, kApplicationVersionCommand, version, 3);
}

bool xvf3800_read_gpi_values(uint8_t values[3]) {
  return read_command(kIoConfigServicerResid, kGpiReadValuesCommand, values, 3);
}

bool xvf3800_set_mute(bool muted) {
  const uint8_t payload[] = {kX0D30MuteMicAndLed, static_cast<uint8_t>(muted ? 1 : 0)};
  return write_command(kGpoServicerResid, kGpoWriteValueCommand, payload, sizeof(payload));
}

esp_err_t xvf3800_led_off() {
  const uint8_t effect[] = {kLedEffectOff};
  return write_command(kGpoServicerResid, kLedEffectCommand, effect, sizeof(effect)) ? ESP_OK : ESP_FAIL;
}

esp_err_t xvf3800_led_solid(uint8_t red, uint8_t green, uint8_t blue, uint8_t brightness) {
  const uint8_t brightness_payload[] = {brightness};
  const uint32_t color = rgb(red, green, blue);
  const uint8_t color_payload[] = {
      static_cast<uint8_t>(color & 0xFF),
      static_cast<uint8_t>((color >> 8) & 0xFF),
      static_cast<uint8_t>((color >> 16) & 0xFF),
      0,
  };
  const uint8_t effect[] = {kLedEffectSolid};
  if (!write_command(kGpoServicerResid, kLedBrightnessCommand, brightness_payload, sizeof(brightness_payload)) ||
      !write_command(kGpoServicerResid, kLedColorCommand, color_payload, sizeof(color_payload)) ||
      !write_command(kGpoServicerResid, kLedEffectCommand, effect, sizeof(effect))) {
    return ESP_FAIL;
  }
  return ESP_OK;
}

esp_err_t xvf3800_led_ring_frame(const uint8_t *rgb_bytes, size_t rgb_byte_count, uint8_t brightness) {
  if (rgb_bytes == nullptr || rgb_byte_count < kLedRingRgbBytes) {
    return ESP_ERR_INVALID_ARG;
  }
  const uint8_t brightness_payload[] = {brightness};
  if (!write_command(kGpoServicerResid, kLedBrightnessCommand, brightness_payload, sizeof(brightness_payload)) ||
      !write_command(kGpoServicerResid, kLedRingColorCommand, rgb_bytes, kLedRingRgbBytes)) {
    return ESP_FAIL;
  }
  return ESP_OK;
}

}  // namespace hexe::board
