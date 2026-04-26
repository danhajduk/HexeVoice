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
constexpr int kDefaultVolumePercent = 70;

int normalize_volume(int volume_percent) {
  return std::clamp(volume_percent, 0, 100);
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

  nvs_close(handle);
  ESP_LOGI(
      kTag,
      "Endpoint settings loaded: volume=%d muted=%s",
      app_state.output_volume_percent,
      app_state.muted ? "true" : "false");
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

}  // namespace hexe::system
