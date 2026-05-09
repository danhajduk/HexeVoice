#include "system/settings.h"

#include <algorithm>
#include <cstdint>

#include "app_state.h"
#include "esp_err.h"
#include "esp_log.h"
#include "nvs.h"

namespace {
constexpr char kTag[] = "hexe_settings";
constexpr char kNamespace[] = "hexe_settings";
constexpr char kVolumeKey[] = "volume_percent";
constexpr char kMutedKey[] = "muted";
constexpr char kMicroVadPauseMsKey[] = "micro_vad_pause_ms";
constexpr int kDefaultVolumePercent = 70;
constexpr int kDefaultMicroVadPauseMs = 190;
constexpr int kMinMicroVadPauseMs = 80;
constexpr int kMaxMicroVadPauseMs = 1000;

int normalize_volume(int volume_percent) {
  return std::clamp(volume_percent, 0, 100);
}

int normalize_micro_vad_pause_ms(int pause_ms) {
  return std::clamp(pause_ms, kMinMicroVadPauseMs, kMaxMicroVadPauseMs);
}

void save_i32(const char *key, int32_t value) {
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open NVS for %s: %s", key, esp_err_to_name(err));
    return;
  }

  err = nvs_set_i32(handle, key, value);
  if (err == ESP_OK) {
    err = nvs_commit(handle);
  }
  nvs_close(handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to persist %s: %s", key, esp_err_to_name(err));
  }
}

void save_u8(const char *key, uint8_t value) {
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open NVS for %s: %s", key, esp_err_to_name(err));
    return;
  }

  err = nvs_set_u8(handle, key, value);
  if (err == ESP_OK) {
    err = nvs_commit(handle);
  }
  nvs_close(handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to persist %s: %s", key, esp_err_to_name(err));
  }
}
}

namespace hexe::system {

void init_settings() {
  auto &app_state = hexe::state();
  app_state.output_volume_percent = kDefaultVolumePercent;
  app_state.muted = false;
  app_state.micro_vad_pause_ms = kDefaultMicroVadPauseMs;

  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READONLY, &handle);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGI(kTag, "No persisted endpoint settings found; using defaults");
    return;
  }
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open persisted endpoint settings: %s", esp_err_to_name(err));
    return;
  }

  int32_t persisted_volume = kDefaultVolumePercent;
  err = nvs_get_i32(handle, kVolumeKey, &persisted_volume);
  if (err == ESP_OK) {
    app_state.output_volume_percent = normalize_volume(persisted_volume);
  } else if (err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to read persisted volume: %s", esp_err_to_name(err));
  }

  uint8_t persisted_muted = 0;
  err = nvs_get_u8(handle, kMutedKey, &persisted_muted);
  if (err == ESP_OK) {
    app_state.muted = persisted_muted != 0;
    app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : app_state.phase;
  } else if (err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to read persisted mute state: %s", esp_err_to_name(err));
  }

  int32_t persisted_micro_vad_pause_ms = kDefaultMicroVadPauseMs;
  err = nvs_get_i32(handle, kMicroVadPauseMsKey, &persisted_micro_vad_pause_ms);
  if (err == ESP_OK) {
    app_state.micro_vad_pause_ms = normalize_micro_vad_pause_ms(persisted_micro_vad_pause_ms);
  } else if (err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to read persisted micro VAD pause: %s", esp_err_to_name(err));
  }

  nvs_close(handle);
  ESP_LOGI(
      kTag,
      "Endpoint settings loaded: volume=%d muted=%s micro_vad_pause_ms=%d",
      app_state.output_volume_percent,
      app_state.muted ? "true" : "false",
      app_state.micro_vad_pause_ms);
}

void set_muted(bool muted) {
  auto &app_state = hexe::state();
  app_state.muted = muted;
  save_u8(kMutedKey, muted ? 1 : 0);
}

void set_output_volume_percent(int volume_percent) {
  const int clamped = normalize_volume(volume_percent);
  hexe::state().output_volume_percent = clamped;
  save_i32(kVolumeKey, clamped);
}

int micro_vad_pause_ms() {
  const int pause_ms = hexe::state().micro_vad_pause_ms;
  return normalize_micro_vad_pause_ms(pause_ms == 0 ? kDefaultMicroVadPauseMs : pause_ms);
}

void set_micro_vad_pause_ms(int pause_ms) {
  const int clamped = normalize_micro_vad_pause_ms(pause_ms);
  hexe::state().micro_vad_pause_ms = clamped;
  save_i32(kMicroVadPauseMsKey, clamped);
}

}  // namespace hexe::system
