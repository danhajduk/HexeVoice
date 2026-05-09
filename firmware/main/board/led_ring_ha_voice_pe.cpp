#include "board/led_ring.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>

#include "driver/gpio.h"
#include "driver/rmt_encoder.h"
#include "driver/rmt_tx.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_led_ring_vpe";
constexpr gpio_num_t kLedDataGpio = GPIO_NUM_21;
constexpr gpio_num_t kLedPowerGpio = GPIO_NUM_45;
constexpr size_t kLedCount = 12;
constexpr uint32_t kRmtResolutionHz = 10 * 1000 * 1000;
constexpr uint32_t kRenderTimeoutMs = 100;
constexpr std::array<uint8_t, kLedCount> kVisualToPhysical = {
    7, 8, 9, 10, 11, 0, 1, 2, 3, 4, 5, 6};

struct LedStripEncoder {
  rmt_encoder_t base;
  rmt_encoder_handle_t bytes_encoder;
  rmt_encoder_handle_t copy_encoder;
  int state;
  rmt_symbol_word_t reset_code;
};

rmt_channel_handle_t g_led_channel = nullptr;
rmt_encoder_handle_t g_led_encoder = nullptr;
SemaphoreHandle_t g_led_mutex = nullptr;
bool g_led_ready = false;
std::array<uint8_t, kLedCount * 3> g_pixels = {};

void set_led_power(bool enabled) {
  gpio_set_level(kLedPowerGpio, enabled ? 1 : 0);
}

uint8_t capped_brightness(uint8_t brightness, bool diagnostic) {
  const uint8_t cap = diagnostic ? hexe::board::kLedRingDiagnosticBrightnessCap
                                 : hexe::board::kLedRingNormalBrightnessCap;
  return std::min<uint8_t>(brightness, cap);
}

uint8_t scale_channel(uint8_t value, uint8_t brightness) {
  return static_cast<uint8_t>((static_cast<uint16_t>(value) * brightness + 127) / 255);
}

void add_encode_state(rmt_encode_state_t &state, rmt_encode_state_t flag) {
  state = static_cast<rmt_encode_state_t>(static_cast<int>(state) | static_cast<int>(flag));
}

RMT_ENCODER_FUNC_ATTR size_t encode_led_strip(
    rmt_encoder_t *encoder,
    rmt_channel_handle_t channel,
    const void *primary_data,
    size_t data_size,
    rmt_encode_state_t *ret_state) {
  auto *led_encoder = reinterpret_cast<LedStripEncoder *>(encoder);
  rmt_encode_state_t session_state = RMT_ENCODING_RESET;
  rmt_encode_state_t state = RMT_ENCODING_RESET;
  size_t encoded_symbols = 0;

  switch (led_encoder->state) {
    case 0:
      encoded_symbols += led_encoder->bytes_encoder->encode(
          led_encoder->bytes_encoder,
          channel,
          primary_data,
          data_size,
          &session_state);
      if (session_state & RMT_ENCODING_COMPLETE) {
        led_encoder->state = 1;
      }
      if (session_state & RMT_ENCODING_MEM_FULL) {
        add_encode_state(state, RMT_ENCODING_MEM_FULL);
        break;
      }
      [[fallthrough]];
    case 1:
      encoded_symbols += led_encoder->copy_encoder->encode(
          led_encoder->copy_encoder,
          channel,
          &led_encoder->reset_code,
          sizeof(led_encoder->reset_code),
          &session_state);
      if (session_state & RMT_ENCODING_COMPLETE) {
        led_encoder->state = RMT_ENCODING_RESET;
        add_encode_state(state, RMT_ENCODING_COMPLETE);
      }
      if (session_state & RMT_ENCODING_MEM_FULL) {
        add_encode_state(state, RMT_ENCODING_MEM_FULL);
      }
      break;
    default:
      led_encoder->state = RMT_ENCODING_RESET;
      break;
  }

  *ret_state = state;
  return encoded_symbols;
}

esp_err_t delete_led_strip_encoder(rmt_encoder_t *encoder) {
  auto *led_encoder = reinterpret_cast<LedStripEncoder *>(encoder);
  if (led_encoder->bytes_encoder != nullptr) {
    rmt_del_encoder(led_encoder->bytes_encoder);
  }
  if (led_encoder->copy_encoder != nullptr) {
    rmt_del_encoder(led_encoder->copy_encoder);
  }
  free(led_encoder);
  return ESP_OK;
}

RMT_ENCODER_FUNC_ATTR esp_err_t reset_led_strip_encoder(rmt_encoder_t *encoder) {
  auto *led_encoder = reinterpret_cast<LedStripEncoder *>(encoder);
  if (led_encoder->bytes_encoder != nullptr) {
    rmt_encoder_reset(led_encoder->bytes_encoder);
  }
  if (led_encoder->copy_encoder != nullptr) {
    rmt_encoder_reset(led_encoder->copy_encoder);
  }
  led_encoder->state = RMT_ENCODING_RESET;
  return ESP_OK;
}

esp_err_t create_led_strip_encoder(rmt_encoder_handle_t *ret_encoder) {
  if (ret_encoder == nullptr) {
    return ESP_ERR_INVALID_ARG;
  }

  auto *led_encoder = static_cast<LedStripEncoder *>(rmt_alloc_encoder_mem(sizeof(LedStripEncoder)));
  if (led_encoder == nullptr) {
    return ESP_ERR_NO_MEM;
  }
  std::memset(led_encoder, 0, sizeof(LedStripEncoder));
  led_encoder->base.encode = encode_led_strip;
  led_encoder->base.del = delete_led_strip_encoder;
  led_encoder->base.reset = reset_led_strip_encoder;

  rmt_bytes_encoder_config_t bytes_config = {};
  bytes_config.bit0.level0 = 1;
  bytes_config.bit0.duration0 = static_cast<uint16_t>(0.3 * kRmtResolutionHz / 1000000);
  bytes_config.bit0.level1 = 0;
  bytes_config.bit0.duration1 = static_cast<uint16_t>(0.9 * kRmtResolutionHz / 1000000);
  bytes_config.bit1.level0 = 1;
  bytes_config.bit1.duration0 = static_cast<uint16_t>(0.9 * kRmtResolutionHz / 1000000);
  bytes_config.bit1.level1 = 0;
  bytes_config.bit1.duration1 = static_cast<uint16_t>(0.3 * kRmtResolutionHz / 1000000);
  bytes_config.flags.msb_first = 1;

  esp_err_t result = rmt_new_bytes_encoder(&bytes_config, &led_encoder->bytes_encoder);
  if (result != ESP_OK) {
    delete_led_strip_encoder(&led_encoder->base);
    return result;
  }

  rmt_copy_encoder_config_t copy_config = {};
  result = rmt_new_copy_encoder(&copy_config, &led_encoder->copy_encoder);
  if (result != ESP_OK) {
    delete_led_strip_encoder(&led_encoder->base);
    return result;
  }

  const uint32_t reset_ticks = kRmtResolutionHz / 1000000 * 50 / 2;
  led_encoder->reset_code.level0 = 0;
  led_encoder->reset_code.duration0 = reset_ticks;
  led_encoder->reset_code.level1 = 0;
  led_encoder->reset_code.duration1 = reset_ticks;

  *ret_encoder = &led_encoder->base;
  return ESP_OK;
}

esp_err_t transmit_pixels_locked(bool keep_power_enabled) {
  if (!g_led_ready) {
    return ESP_ERR_INVALID_STATE;
  }

  set_led_power(true);
  rmt_transmit_config_t transmit_config = {};
  transmit_config.loop_count = 0;

  esp_err_t result = rmt_transmit(
      g_led_channel,
      g_led_encoder,
      g_pixels.data(),
      g_pixels.size(),
      &transmit_config);
  if (result == ESP_OK) {
    result = rmt_tx_wait_all_done(g_led_channel, pdMS_TO_TICKS(kRenderTimeoutMs));
  }
  if (result != ESP_OK || !keep_power_enabled) {
    set_led_power(false);
  }
  return result;
}

esp_err_t render_frame_locked(
    const hexe::board::LedRingColor *visual_colors,
    size_t visual_color_count,
    uint8_t brightness,
    bool diagnostic) {
  if (visual_colors == nullptr || visual_color_count < kLedCount) {
    return ESP_ERR_INVALID_ARG;
  }

  const uint8_t scaled_brightness = capped_brightness(brightness, diagnostic);
  bool any_lit = false;
  g_pixels.fill(0);

  for (size_t visual_index = 0; visual_index < kLedCount; ++visual_index) {
    const hexe::board::LedRingColor color = visual_colors[visual_index];
    const size_t physical_index = kVisualToPhysical[visual_index];
    const uint8_t red = scale_channel(color.red, scaled_brightness);
    const uint8_t green = scale_channel(color.green, scaled_brightness);
    const uint8_t blue = scale_channel(color.blue, scaled_brightness);
    g_pixels[physical_index * 3 + 0] = green;
    g_pixels[physical_index * 3 + 1] = red;
    g_pixels[physical_index * 3 + 2] = blue;
    any_lit = any_lit || red != 0 || green != 0 || blue != 0;
  }

  const esp_err_t result = transmit_pixels_locked(any_lit);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Voice PE LED frame render failed: %s", esp_err_to_name(result));
    g_pixels.fill(0);
    set_led_power(false);
  }
  return result;
}

bool take_led_mutex() {
  return g_led_mutex == nullptr || xSemaphoreTake(g_led_mutex, pdMS_TO_TICKS(kRenderTimeoutMs)) == pdTRUE;
}

void give_led_mutex() {
  if (g_led_mutex != nullptr) {
    xSemaphoreGive(g_led_mutex);
  }
}
}  // namespace

namespace hexe::board {

void init_led_ring() {
  gpio_config_t power_config = {};
  power_config.pin_bit_mask = 1ULL << kLedPowerGpio;
  power_config.mode = GPIO_MODE_OUTPUT;
  power_config.pull_up_en = GPIO_PULLUP_DISABLE;
  power_config.pull_down_en = GPIO_PULLDOWN_ENABLE;
  power_config.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&power_config);
  set_led_power(false);

  g_led_mutex = xSemaphoreCreateMutex();
  if (g_led_mutex == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate Voice PE LED ring mutex");
    return;
  }

  rmt_tx_channel_config_t tx_config = {};
  tx_config.clk_src = RMT_CLK_SRC_DEFAULT;
  tx_config.gpio_num = kLedDataGpio;
  tx_config.mem_block_symbols = 64;
  tx_config.resolution_hz = kRmtResolutionHz;
  tx_config.trans_queue_depth = 1;

  esp_err_t result = rmt_new_tx_channel(&tx_config, &g_led_channel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Voice PE LED RMT channel: %s", esp_err_to_name(result));
    return;
  }

  result = create_led_strip_encoder(&g_led_encoder);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Voice PE LED RMT encoder: %s", esp_err_to_name(result));
    return;
  }

  result = rmt_enable(g_led_channel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to enable Voice PE LED RMT channel: %s", esp_err_to_name(result));
    return;
  }

  g_led_ready = true;
  led_ring_off();
  ESP_LOGI(kTag, "Home Assistant Voice PE LED ring ready: data=GPIO21 power=GPIO45 leds=12 order=GRB");
}

bool led_ring_available() {
  return g_led_ready;
}

esp_err_t led_ring_off() {
  if (!take_led_mutex()) {
    return ESP_ERR_TIMEOUT;
  }
  g_pixels.fill(0);
  const esp_err_t result = g_led_ready ? transmit_pixels_locked(false) : ESP_OK;
  give_led_mutex();
  return result;
}

esp_err_t led_ring_set_solid(
    uint8_t red,
    uint8_t green,
    uint8_t blue,
    uint8_t brightness,
    bool diagnostic) {
  std::array<LedRingColor, kLedCount> frame = {};
  frame.fill(LedRingColor{red, green, blue});
  return led_ring_set_visual_frame(frame.data(), frame.size(), brightness, diagnostic);
}

esp_err_t led_ring_set_visual_frame(
    const LedRingColor *visual_colors,
    size_t visual_color_count,
    uint8_t brightness,
    bool diagnostic) {
  if (!take_led_mutex()) {
    return ESP_ERR_TIMEOUT;
  }
  const esp_err_t result = render_frame_locked(visual_colors, visual_color_count, brightness, diagnostic);
  give_led_mutex();
  return result;
}

}  // namespace hexe::board
