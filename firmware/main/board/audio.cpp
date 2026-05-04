#include "board/audio.h"

#include <cstddef>
#include <cstdint>

#include "app_state.h"
#include "bsp/esp-box-3.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_audio";
constexpr int kSampleRate = 16000;
constexpr int kBitsPerSample = 16;
constexpr int kChannelCount = 1;
constexpr size_t kFrameSamples = 320;
constexpr size_t kFrameBytes = kFrameSamples * sizeof(int16_t);
constexpr uint32_t kFrameDurationMs = static_cast<uint32_t>((kFrameSamples * 1000) / kSampleRate);
constexpr uint32_t kVadStartEnergyThreshold = 900;
constexpr uint32_t kVadContinueEnergyThreshold = 500;
constexpr uint32_t kVadSilenceHoldMs = 2500;
constexpr uint32_t kVadSilenceHoldFrames = kVadSilenceHoldMs / kFrameDurationMs;

esp_codec_dev_handle_t g_mic_codec = nullptr;
TaskHandle_t g_vad_task = nullptr;
SemaphoreHandle_t g_mic_mutex = nullptr;
bool g_vad_turn_active = false;
bool g_mic_paused_for_playback = false;

uint32_t estimate_level(const int16_t *samples, size_t count) {
  uint64_t total = 0;
  for (size_t i = 0; i < count; ++i) {
    const int32_t sample = samples[i];
    total += sample < 0 ? static_cast<uint32_t>(-sample) : static_cast<uint32_t>(sample);
  }

  return count == 0 ? 0 : static_cast<uint32_t>(total / count);
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

void vad_task(void *arg) {
  (void)arg;

  int16_t samples[kFrameSamples];
  uint32_t silent_frames = 0;

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

    if (g_mic_paused_for_playback) {
      xSemaphoreGive(g_mic_mutex);
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }

    int read_result = esp_codec_dev_read(g_mic_codec, samples, static_cast<int>(kFrameBytes));
    xSemaphoreGive(g_mic_mutex);
    if (read_result != 0) {
      ESP_LOGW(kTag, "Microphone read failed: %d", read_result);
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }

    const uint32_t level = estimate_level(samples, kFrameSamples);
    const bool was_speaking = hexe::state().vad_speaking;
    const uint32_t threshold = was_speaking ? kVadContinueEnergyThreshold : kVadStartEnergyThreshold;
    const bool frame_has_voice = level >= threshold;
    hexe::voice::submit_audio_frame(samples, kFrameSamples, level, frame_has_voice);

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
}

namespace hexe::board {

bool open_microphone_stream() {
  esp_codec_dev_sample_info_t fs = {
      .bits_per_sample = kBitsPerSample,
      .channel = kChannelCount,
      .channel_mask = 0,
      .sample_rate = kSampleRate,
      .mclk_multiple = 0,
  };

  int result = esp_codec_dev_open(g_mic_codec, &fs);
  if (result != 0) {
    ESP_LOGE(kTag, "Failed to open microphone stream: %d", result);
    return false;
  }

  result = esp_codec_dev_set_in_gain(g_mic_codec, 36.0f);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to set microphone gain: %d", result);
  }

  hexe::state().vad_enabled = true;
  return true;
}

void init_audio() {
  ESP_ERROR_CHECK(bsp_audio_init(nullptr));

  g_mic_mutex = xSemaphoreCreateMutex();
  if (g_mic_mutex == nullptr) {
    ESP_LOGE(kTag, "Failed to create microphone mutex");
    return;
  }

  g_mic_codec = bsp_audio_codec_microphone_init();
  if (g_mic_codec == nullptr) {
    ESP_LOGE(kTag, "Failed to initialize microphone codec");
    return;
  }

  if (!open_microphone_stream()) {
    return;
  }

  BaseType_t task_result = xTaskCreate(vad_task, "hexe_vad", 4096, nullptr, 5, &g_vad_task);
  if (task_result != pdPASS) {
    ESP_LOGE(kTag, "Failed to create VAD task");
    return;
  }

  ESP_LOGI(kTag, "Audio and VAD initialized at %d Hz mono", kSampleRate);
}

void update_audio() {
}

bool pause_microphone_for_playback() {
  if (g_mic_codec == nullptr || g_mic_mutex == nullptr) {
    return false;
  }

  if (xSemaphoreTake(g_mic_mutex, pdMS_TO_TICKS(500)) != pdTRUE) {
    ESP_LOGW(kTag, "Timed out waiting to pause microphone for playback");
    return false;
  }

  if (g_mic_paused_for_playback) {
    return true;
  }

  const int result = esp_codec_dev_close(g_mic_codec);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to pause microphone stream: %d", result);
    xSemaphoreGive(g_mic_mutex);
    return false;
  }

  auto &app_state = hexe::state();
  app_state.vad_enabled = false;
  app_state.vad_speaking = false;
  app_state.vad_level = 0;
  g_mic_paused_for_playback = true;
  ESP_LOGI(kTag, "Microphone paused for playback");
  return true;
}

void resume_microphone_after_playback() {
  if (g_mic_codec == nullptr || g_mic_mutex == nullptr || !g_mic_paused_for_playback) {
    return;
  }

  g_mic_paused_for_playback = false;
  if (open_microphone_stream()) {
    ESP_LOGI(kTag, "Microphone resumed after playback");
  }
  xSemaphoreGive(g_mic_mutex);
}

}  // namespace hexe::board
