#include "board/led_ring.h"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>

#include "app_state.h"
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
constexpr uint32_t kPatternFrameMs = 50;
constexpr uint32_t kMomentaryPatternMs = 750;
constexpr std::array<uint8_t, kLedCount> kVisualToPhysical = {
    7, 8, 9, 10, 11, 0, 1, 2, 3, 4, 5, 6};

enum class LedPattern {
  kOff,
  kBoot,
  kWifiConnecting,
  kBackendConnecting,
  kWakeListening,
  kCapturing,
  kThinking,
  kReplying,
  kOtaProgress,
  kCompleted,
  kCancelled,
  kMuted,
  kSpeakerSilent,
  kVolumeDisplay,
  kColorSelect,
  kError,
  kDisconnected,
};

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
LedPattern g_last_pattern = LedPattern::kOff;
LedPattern g_momentary_pattern = LedPattern::kOff;
TickType_t g_last_pattern_tick = 0;
TickType_t g_momentary_until_tick = 0;
uint16_t g_accent_hue_degrees = 196;
int g_affordance_percent = 0;

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

hexe::board::LedRingColor color(uint8_t red, uint8_t green, uint8_t blue) {
  return hexe::board::LedRingColor{red, green, blue};
}

hexe::board::LedRingColor hsv_to_rgb(uint16_t hue_degrees, uint8_t saturation_percent, uint8_t value_percent) {
  const uint16_t hue = hue_degrees % 360;
  const uint8_t region = hue / 60;
  const uint16_t remainder = (hue % 60) * 255 / 60;
  const uint16_t value = value_percent * 255 / 100;
  const uint16_t saturation = saturation_percent * 255 / 100;
  const uint8_t p = static_cast<uint8_t>(value * (255 - saturation) / 255);
  const uint8_t q = static_cast<uint8_t>(value * (255 - (saturation * remainder / 255)) / 255);
  const uint8_t t = static_cast<uint8_t>(value * (255 - (saturation * (255 - remainder) / 255)) / 255);
  const uint8_t v = static_cast<uint8_t>(value);

  switch (region) {
    case 0:
      return color(v, t, p);
    case 1:
      return color(q, v, p);
    case 2:
      return color(p, v, t);
    case 3:
      return color(p, q, v);
    case 4:
      return color(t, p, v);
    default:
      return color(v, p, q);
  }
}

hexe::board::LedRingColor accent_color() {
  return hsv_to_rgb(g_accent_hue_degrees, 100, 100);
}

hexe::board::LedRingColor dim_color(hexe::board::LedRingColor source, uint8_t percent) {
  return color(
      static_cast<uint8_t>(source.red * percent / 100),
      static_cast<uint8_t>(source.green * percent / 100),
      static_cast<uint8_t>(source.blue * percent / 100));
}

uint8_t pulse_value(uint32_t frame, uint8_t low, uint8_t high) {
  const uint32_t phase = frame % 24;
  const uint32_t rising = phase < 12 ? phase : 23 - phase;
  return static_cast<uint8_t>(low + ((high - low) * rising) / 11);
}

void set_pixel(
    std::array<hexe::board::LedRingColor, kLedCount> &frame,
    size_t index,
    hexe::board::LedRingColor value) {
  frame[index % kLedCount] = value;
}

void fill_voice_pattern(
    LedPattern pattern,
    uint32_t frame_index,
    const hexe::AppState &state,
    std::array<hexe::board::LedRingColor, kLedCount> &frame,
    uint8_t &brightness,
    bool &diagnostic) {
  frame.fill(color(0, 0, 0));
  brightness = hexe::board::kLedRingDefaultBrightness;
  diagnostic = false;

  const uint32_t cursor = frame_index % kLedCount;
  const hexe::board::LedRingColor accent = accent_color();
  const hexe::board::LedRingColor accent_dim = dim_color(accent, 45);
  switch (pattern) {
    case LedPattern::kOff:
      break;
    case LedPattern::kBoot:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      for (size_t index = 0; index < kLedCount; ++index) {
        if ((index + cursor) % 3 == 0) {
          set_pixel(frame, index, color(255, 190, 110));
        }
      }
      break;
    case LedPattern::kWifiConnecting:
      diagnostic = true;
      brightness = hexe::board::kLedRingDiagnosticBrightnessCap;
      set_pixel(frame, cursor / 2, color(255, 120, 0));
      break;
    case LedPattern::kBackendConnecting:
      diagnostic = true;
      brightness = hexe::board::kLedRingDiagnosticBrightnessCap;
      set_pixel(frame, cursor, color(0, 100, 255));
      set_pixel(frame, cursor + 6, color(0, 100, 255));
      break;
    case LedPattern::kWakeListening:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      set_pixel(frame, cursor, accent);
      set_pixel(frame, cursor + 11, accent_dim);
      set_pixel(frame, cursor + 6, accent);
      set_pixel(frame, cursor + 5, accent_dim);
      break;
    case LedPattern::kCapturing: {
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      const uint32_t level = static_cast<uint32_t>(std::max(state.vad_level, 0));
      const size_t lit_count = std::clamp<size_t>(2 + (level / 250), size_t{2}, kLedCount);
      for (size_t index = 0; index < lit_count; ++index) {
        const uint8_t percent = static_cast<uint8_t>(100 - std::min<size_t>(index * 4, 55));
        set_pixel(frame, index, dim_color(accent, percent));
      }
      break;
    }
    case LedPattern::kThinking: {
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      const uint8_t blue = pulse_value(frame_index, 80, 255);
      set_pixel(frame, cursor, color(120, 0, blue));
      set_pixel(frame, cursor + 6, color(120, 0, blue));
      break;
    }
    case LedPattern::kReplying:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      set_pixel(frame, kLedCount - cursor, accent);
      set_pixel(frame, kLedCount - cursor + 1, accent_dim);
      set_pixel(frame, kLedCount - cursor + 6, accent);
      set_pixel(frame, kLedCount - cursor + 7, accent_dim);
      break;
    case LedPattern::kOtaProgress: {
      diagnostic = true;
      brightness = hexe::board::kLedRingDiagnosticBrightnessCap;
      const size_t lit_count = std::clamp<size_t>(
          (state.ota_progress_percent * kLedCount + 99) / 100,
          size_t{1},
          kLedCount);
      for (size_t index = 0; index < lit_count; ++index) {
        set_pixel(frame, index, color(0, 170, 255));
      }
      set_pixel(frame, cursor, color(0, 255, 120));
      break;
    }
    case LedPattern::kCompleted: {
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      const uint8_t green = pulse_value(frame_index, 80, 255);
      frame.fill(color(0, green, 80));
      break;
    }
    case LedPattern::kCancelled:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      set_pixel(frame, 0, color(255, 120, 0));
      set_pixel(frame, 3, color(255, 120, 0));
      set_pixel(frame, 6, color(255, 120, 0));
      set_pixel(frame, 9, color(255, 120, 0));
      break;
    case LedPattern::kMuted:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      set_pixel(frame, 3, color(255, 0, 0));
      set_pixel(frame, 9, color(255, 0, 0));
      break;
    case LedPattern::kSpeakerSilent:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      set_pixel(frame, 5, color(255, 0, 0));
      set_pixel(frame, 6, color(255, 90, 0));
      set_pixel(frame, 7, color(255, 0, 0));
      break;
    case LedPattern::kVolumeDisplay: {
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      const size_t lit_count = std::clamp<size_t>(
          (g_affordance_percent * kLedCount + 99) / 100,
          size_t{0},
          kLedCount);
      for (size_t index = 0; index < lit_count; ++index) {
        set_pixel(frame, (6 + index) % kLedCount, accent);
      }
      if (g_affordance_percent <= 0) {
        set_pixel(frame, 6, color(255, 0, 0));
      }
      break;
    }
    case LedPattern::kColorSelect:
      brightness = hexe::board::kLedRingNormalBrightnessCap;
      frame.fill(accent);
      set_pixel(frame, cursor, color(255, 255, 255));
      break;
    case LedPattern::kError: {
      diagnostic = true;
      brightness = hexe::board::kLedRingDiagnosticBrightnessCap;
      const uint8_t red = pulse_value(frame_index, 120, 255);
      frame.fill(color(red, 0, 0));
      break;
    }
    case LedPattern::kDisconnected:
      diagnostic = true;
      brightness = hexe::board::kLedRingDiagnosticBrightnessCap;
      set_pixel(frame, cursor / 2, color(255, 120, 0));
      break;
  }
}

LedPattern pattern_for_state(const hexe::AppState &state) {
  if (state.phase == hexe::AppPhase::kBooting) {
    return LedPattern::kBoot;
  }
  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    return LedPattern::kOtaProgress;
  }
  if (state.muted || state.phase == hexe::AppPhase::kMuted) {
    return LedPattern::kMuted;
  }
  if (!state.wifi_connected || state.phase == hexe::AppPhase::kWiFiConnecting) {
    return LedPattern::kWifiConnecting;
  }
  if (!state.backend_connected || !state.voice_ws_connected || state.phase == hexe::AppPhase::kBackendConnecting) {
    return LedPattern::kBackendConnecting;
  }
  if (state.phase == hexe::AppPhase::kIdle && state.output_volume_percent <= 0) {
    return LedPattern::kSpeakerSilent;
  }

  switch (state.phase) {
    case hexe::AppPhase::kListening:
      return (state.vad_speaking || state.audio_streaming) ? LedPattern::kCapturing : LedPattern::kWakeListening;
    case hexe::AppPhase::kThinking:
      return LedPattern::kThinking;
    case hexe::AppPhase::kReplying:
      return LedPattern::kReplying;
    case hexe::AppPhase::kError:
      return LedPattern::kError;
    case hexe::AppPhase::kIdle:
    case hexe::AppPhase::kTimerFinished:
      return LedPattern::kOff;
    case hexe::AppPhase::kBooting:
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
    case hexe::AppPhase::kUpdating:
    case hexe::AppPhase::kMuted:
      return LedPattern::kOff;
  }
  return LedPattern::kOff;
}

void show_momentary_pattern(LedPattern pattern) {
  g_momentary_pattern = pattern;
  g_momentary_until_tick = xTaskGetTickCount() + pdMS_TO_TICKS(kMomentaryPatternMs);
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

void update_led_ring_patterns() {
  if (!g_led_ready) {
    return;
  }

  const TickType_t now = xTaskGetTickCount();
  LedPattern pattern = pattern_for_state(hexe::state());
  if (g_momentary_pattern != LedPattern::kOff) {
    if (static_cast<int32_t>(g_momentary_until_tick - now) > 0) {
      pattern = g_momentary_pattern;
    } else {
      g_momentary_pattern = LedPattern::kOff;
    }
  }

  const bool pattern_changed = pattern != g_last_pattern;
  const bool frame_due = (now - g_last_pattern_tick) >= pdMS_TO_TICKS(kPatternFrameMs);
  if (!pattern_changed && !frame_due) {
    return;
  }

  g_last_pattern_tick = now;
  if (pattern == LedPattern::kOff) {
    if (pattern_changed) {
      led_ring_off();
    }
    g_last_pattern = pattern;
    return;
  }

  std::array<LedRingColor, kLedCount> frame = {};
  uint8_t brightness = kLedRingDefaultBrightness;
  bool diagnostic = false;
  const uint32_t frame_index = now / pdMS_TO_TICKS(kPatternFrameMs);
  fill_voice_pattern(pattern, frame_index, hexe::state(), frame, brightness, diagnostic);
  if (led_ring_set_visual_frame(frame.data(), frame.size(), brightness, diagnostic) == ESP_OK) {
    g_last_pattern = pattern;
  }
}

void led_ring_show_completed() {
  show_momentary_pattern(LedPattern::kCompleted);
}

void led_ring_show_cancelled() {
  show_momentary_pattern(LedPattern::kCancelled);
}

void led_ring_show_volume(int volume_percent) {
  g_affordance_percent = std::clamp(volume_percent, 0, 100);
  show_momentary_pattern(LedPattern::kVolumeDisplay);
}

void led_ring_adjust_accent_hue(int delta_steps) {
  int hue = static_cast<int>(g_accent_hue_degrees) + (delta_steps * 18);
  while (hue < 0) {
    hue += 360;
  }
  g_accent_hue_degrees = static_cast<uint16_t>(hue % 360);
  show_momentary_pattern(LedPattern::kColorSelect);
}

}  // namespace hexe::board
