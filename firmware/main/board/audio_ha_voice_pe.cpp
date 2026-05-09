#include "board/audio.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

#include "app_state.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_audio_vpe";
constexpr int kSampleRate = 16000;
constexpr size_t kFrameSamples = 320;
constexpr uint32_t kFrameDurationMs = static_cast<uint32_t>((kFrameSamples * 1000) / kSampleRate);
constexpr uint32_t kVadStartEnergyThreshold = 900;
constexpr uint32_t kVadContinueEnergyThreshold = 500;
constexpr uint32_t kVadSilenceHoldMs = 2500;
constexpr uint32_t kVadSilenceHoldFrames = kVadSilenceHoldMs / kFrameDurationMs;
constexpr uint32_t kVadTaskStackBytes = 8192;
constexpr uint32_t kMicReadTimeoutLogEvery = 200;

constexpr gpio_num_t kMicBclk = GPIO_NUM_13;
constexpr gpio_num_t kMicLrclk = GPIO_NUM_14;
constexpr gpio_num_t kMicDin = GPIO_NUM_15;
constexpr gpio_num_t kSpeakerAmp = GPIO_NUM_47;
constexpr gpio_num_t kVoiceKitReset = GPIO_NUM_4;
constexpr gpio_num_t kVoiceKitI2cSda = GPIO_NUM_5;
constexpr gpio_num_t kVoiceKitI2cScl = GPIO_NUM_6;
constexpr uint8_t kVoiceKitI2cAddress = 0x42;
constexpr uint32_t kVoiceKitI2cClockHz = 400000;
constexpr uint32_t kVoiceKitBootDelayMs = 3000;
constexpr uint32_t kVoiceKitI2cTimeoutMs = 1000;
constexpr uint8_t kVoiceKitCtrlDone = 0;
constexpr uint8_t kDfuServicerResid = 240;
constexpr uint8_t kConfigurationServicerResid = 241;
constexpr uint8_t kReadCommandBit = 0x80;
constexpr uint8_t kDfuGetVersionCommand = 88;
constexpr uint8_t kChannel0PipelineStage = 0x30;
constexpr uint8_t kChannel1PipelineStage = 0x40;
constexpr uint8_t kPipelineAgc = 4;
constexpr uint8_t kPipelineNs = 3;

i2s_chan_handle_t g_rx_channel = nullptr;
i2c_master_bus_handle_t g_voice_kit_i2c_bus = nullptr;
i2c_master_dev_handle_t g_voice_kit_i2c_device = nullptr;
TaskHandle_t g_vad_task = nullptr;
SemaphoreHandle_t g_mic_mutex = nullptr;
bool g_vad_turn_active = false;
bool g_mic_paused_for_playback = false;
bool g_voice_kit_ready = false;
uint32_t g_mic_read_timeout_count = 0;
std::array<int32_t, kFrameSamples * 2> g_raw_samples = {};
std::array<int16_t, kFrameSamples> g_mono_samples = {};

uint32_t estimate_level(const int16_t *samples, size_t count) {
  uint64_t total = 0;
  for (size_t index = 0; index < count; ++index) {
    const int32_t sample = samples[index];
    total += sample < 0 ? static_cast<uint32_t>(-sample) : static_cast<uint32_t>(sample);
  }
  return count == 0 ? 0 : static_cast<uint32_t>(total / count);
}

int16_t fold_stereo_sample(int32_t left, int32_t right) {
  const int32_t mono = ((left >> 16) + (right >> 16)) / 2;
  return static_cast<int16_t>(std::clamp<int32_t>(mono, -32768, 32767));
}

bool init_voice_kit_i2c() {
  if (g_voice_kit_i2c_device != nullptr) {
    return true;
  }

  if (g_voice_kit_i2c_bus == nullptr) {
    i2c_master_bus_config_t bus_config = {};
    bus_config.i2c_port = I2C_NUM_0;
    bus_config.sda_io_num = kVoiceKitI2cSda;
    bus_config.scl_io_num = kVoiceKitI2cScl;
    bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
    bus_config.glitch_ignore_cnt = 7;
    bus_config.flags.enable_internal_pullup = true;

    const esp_err_t result = i2c_new_master_bus(&bus_config, &g_voice_kit_i2c_bus);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to create Voice Kit I2C bus: %s", esp_err_to_name(result));
      return false;
    }
  }

  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = kVoiceKitI2cAddress;
  device_config.scl_speed_hz = kVoiceKitI2cClockHz;

  const esp_err_t result = i2c_master_bus_add_device(g_voice_kit_i2c_bus, &device_config, &g_voice_kit_i2c_device);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to add Voice Kit I2C device: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool voice_kit_write(const uint8_t *data, size_t size) {
  const esp_err_t result = i2c_master_transmit(
      g_voice_kit_i2c_device,
      data,
      size,
      pdMS_TO_TICKS(kVoiceKitI2cTimeoutMs));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Voice Kit I2C write failed: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool voice_kit_read(uint8_t *data, size_t size) {
  const esp_err_t result = i2c_master_receive(
      g_voice_kit_i2c_device,
      data,
      size,
      pdMS_TO_TICKS(kVoiceKitI2cTimeoutMs));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Voice Kit I2C read failed: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool read_voice_kit_version() {
  const uint8_t request[] = {
      kDfuServicerResid,
      static_cast<uint8_t>(kDfuGetVersionCommand | kReadCommandBit),
      4,
  };
  uint8_t response[4] = {};

  if (!voice_kit_write(request, sizeof(request)) || !voice_kit_read(response, sizeof(response))) {
    return false;
  }
  if (response[0] != kVoiceKitCtrlDone) {
    ESP_LOGW(kTag, "Voice Kit version response not ready: status=%u", response[0]);
    return false;
  }

  ESP_LOGI(kTag, "Voice Kit XMOS firmware version %u.%u.%u", response[1], response[2], response[3]);
  return true;
}

bool write_voice_kit_pipeline_stage(uint8_t channel_register, uint8_t stage) {
  const uint8_t request[] = {
      kConfigurationServicerResid,
      channel_register,
      1,
      stage,
  };
  return voice_kit_write(request, sizeof(request));
}

bool init_voice_kit() {
  if (!init_voice_kit_i2c()) {
    return false;
  }

  gpio_set_level(kVoiceKitReset, 1);
  vTaskDelay(pdMS_TO_TICKS(1));
  gpio_set_level(kVoiceKitReset, 0);
  vTaskDelay(pdMS_TO_TICKS(kVoiceKitBootDelayMs));

  if (!read_voice_kit_version()) {
    ESP_LOGE(kTag, "Voice Kit did not respond after reset; microphone I2S clocks are unavailable");
    return false;
  }

  if (!write_voice_kit_pipeline_stage(kChannel0PipelineStage, kPipelineAgc) ||
      !write_voice_kit_pipeline_stage(kChannel1PipelineStage, kPipelineNs)) {
    ESP_LOGE(kTag, "Failed to configure Voice Kit microphone pipeline");
    return false;
  }

  g_voice_kit_ready = true;
  return true;
}

void apply_vad_state(bool speaking, uint32_t level) {
  auto &app_state = hexe::state();
  app_state.vad_enabled = true;
  app_state.vad_level = static_cast<int>(level);

  if (app_state.muted || app_state.ota_active) {
    app_state.vad_speaking = false;
    return;
  }

  const bool state_changed = app_state.vad_speaking != speaking;
  app_state.vad_speaking = speaking;
  if (!state_changed) {
    return;
  }

  if (speaking) {
    g_vad_turn_active = true;
    ESP_LOGI(kTag, "VAD speech detected (level=%lu)", static_cast<unsigned long>(level));
  } else if (g_vad_turn_active) {
    g_vad_turn_active = false;
    if (hexe::voice::finish_audio_stream("vad_silence")) {
      if (app_state.phase == hexe::AppPhase::kListening) {
        app_state.phase = hexe::AppPhase::kThinking;
      }
    } else if (app_state.phase == hexe::AppPhase::kListening) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
    ESP_LOGI(kTag, "VAD silence detected (level=%lu)", static_cast<unsigned long>(level));
  }
}

bool start_microphone_stream() {
  if (g_rx_channel != nullptr) {
    return i2s_channel_enable(g_rx_channel) == ESP_OK;
  }

  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_SLAVE);
  channel_config.dma_desc_num = 6;
  channel_config.dma_frame_num = kFrameSamples;
  esp_err_t result = i2s_new_channel(&channel_config, nullptr, &g_rx_channel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Voice PE I2S RX channel: %s", esp_err_to_name(result));
    return false;
  }

  i2s_std_config_t std_config = {};
  std_config.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSampleRate);
  std_config.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
  std_config.gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = kMicBclk,
      .ws = kMicLrclk,
      .dout = I2S_GPIO_UNUSED,
      .din = kMicDin,
      .invert_flags = {},
  };

  result = i2s_channel_init_std_mode(g_rx_channel, &std_config);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to initialize Voice PE I2S RX mode: %s", esp_err_to_name(result));
    return false;
  }

  result = i2s_channel_enable(g_rx_channel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to enable Voice PE microphone stream: %s", esp_err_to_name(result));
    return false;
  }
  hexe::state().vad_enabled = true;
  return true;
}

void vad_task(void *arg) {
  (void)arg;

  uint32_t silent_frames = kVadSilenceHoldFrames;

  while (true) {
    if (hexe::state().ota_active) {
      auto &app_state = hexe::state();
      app_state.vad_enabled = false;
      app_state.vad_speaking = false;
      app_state.vad_level = 0;
      app_state.audio_streaming = false;
      g_vad_turn_active = false;
      silent_frames = kVadSilenceHoldFrames;
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    if (g_mic_mutex == nullptr || xSemaphoreTake(g_mic_mutex, portMAX_DELAY) != pdTRUE) {
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    if (g_mic_paused_for_playback || g_rx_channel == nullptr) {
      xSemaphoreGive(g_mic_mutex);
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    size_t bytes_read = 0;
    const esp_err_t result = i2s_channel_read(
        g_rx_channel,
        g_raw_samples.data(),
        g_raw_samples.size() * sizeof(g_raw_samples[0]),
        &bytes_read,
        pdMS_TO_TICKS(100));
    xSemaphoreGive(g_mic_mutex);
    if (result != ESP_OK || bytes_read == 0) {
      if (result == ESP_ERR_TIMEOUT && bytes_read == 0) {
        ++g_mic_read_timeout_count;
        if ((g_mic_read_timeout_count % kMicReadTimeoutLogEvery) == 0) {
          ESP_LOGD(kTag, "Voice PE microphone read timeout count=%lu", static_cast<unsigned long>(g_mic_read_timeout_count));
        }
      } else {
        ESP_LOGW(kTag, "Voice PE microphone read failed: %s bytes=%u", esp_err_to_name(result), static_cast<unsigned>(bytes_read));
      }
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    const size_t stereo_frames = std::min(bytes_read / (sizeof(int32_t) * 2), kFrameSamples);
    for (size_t index = 0; index < stereo_frames; ++index) {
      g_mono_samples[index] = fold_stereo_sample(g_raw_samples[index * 2], g_raw_samples[(index * 2) + 1]);
    }

    const uint32_t level = estimate_level(g_mono_samples.data(), stereo_frames);
    const bool was_speaking = hexe::state().vad_speaking;
    const uint32_t threshold = was_speaking ? kVadContinueEnergyThreshold : kVadStartEnergyThreshold;
    const bool frame_has_voice = level >= threshold;
    hexe::voice::submit_audio_frame(g_mono_samples.data(), stereo_frames, level, frame_has_voice);

    if (frame_has_voice) {
      silent_frames = 0;
      apply_vad_state(true, level);
    } else {
      if (silent_frames < kVadSilenceHoldFrames) {
        ++silent_frames;
      }
      apply_vad_state(silent_frames < kVadSilenceHoldFrames, level);
    }
  }
}
}  // namespace

namespace hexe::board {

void init_audio() {
  gpio_config_t output_config = {};
  output_config.pin_bit_mask = (1ULL << kSpeakerAmp) | (1ULL << kVoiceKitReset);
  output_config.mode = GPIO_MODE_OUTPUT;
  gpio_config(&output_config);
  gpio_set_level(kSpeakerAmp, 1);

  if (!init_voice_kit()) {
    return;
  }

  g_mic_mutex = xSemaphoreCreateMutex();
  if (g_mic_mutex == nullptr) {
    ESP_LOGE(kTag, "Failed to create Voice PE microphone mutex");
    return;
  }
  if (!start_microphone_stream()) {
    return;
  }
  if (xTaskCreate(vad_task, "hexe_vpe_vad", kVadTaskStackBytes, nullptr, 5, &g_vad_task) != pdPASS) {
    ESP_LOGE(kTag, "Failed to create Voice PE VAD task");
    return;
  }
  ESP_LOGI(kTag, "Home Assistant Voice PE microphone initialized on I2S GPIO13/14/15");
}

void update_audio() {
}

bool audio_input_ready() {
  return g_voice_kit_ready && g_rx_channel != nullptr && !g_mic_paused_for_playback;
}

bool audio_output_ready() {
  return false;
}

bool pause_microphone_for_playback() {
  if (g_rx_channel == nullptr || g_mic_mutex == nullptr) {
    return false;
  }
  if (xSemaphoreTake(g_mic_mutex, pdMS_TO_TICKS(500)) != pdTRUE) {
    return false;
  }
  if (!g_mic_paused_for_playback) {
    i2s_channel_disable(g_rx_channel);
    g_mic_paused_for_playback = true;
    hexe::state().vad_enabled = false;
    hexe::state().vad_speaking = false;
  }
  return true;
}

void resume_microphone_after_playback() {
  if (g_rx_channel == nullptr || g_mic_mutex == nullptr) {
    return;
  }
  if (g_mic_paused_for_playback) {
    i2s_channel_enable(g_rx_channel);
    g_mic_paused_for_playback = false;
    hexe::state().vad_enabled = true;
  }
  xSemaphoreGive(g_mic_mutex);
}

}  // namespace hexe::board
