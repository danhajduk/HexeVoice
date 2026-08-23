#include "system/settings.h"

#include <algorithm>
#include <cstdint>
#include <cstring>

#include "app_state.h"
#include "endpoint_config.h"
#include "esp_err.h"
#include "esp_log.h"
#include "nvs.h"

#if __has_include("secrets/wifi_secrets.h")
#include "secrets/wifi_secrets.h"
#else
namespace hexe::secrets {
constexpr const char *kWifiSsid = "";
constexpr const char *kWifiPassword = "";
}  // namespace hexe::secrets
#endif

namespace {
constexpr char kTag[] = "hexe_settings";
constexpr char kNamespace[] = "hexe_settings";
constexpr char kVolumeKey[] = "volume_percent";
constexpr char kMutedKey[] = "muted";
constexpr char kMicroVadPauseMsKey[] = "micro_vad_pause_ms";
constexpr char kEndpointIdKey[] = "endpoint_id";
constexpr char kDisplayNameKey[] = "display_name";
constexpr char kBackendHostKey[] = "backend_host";
constexpr char kHttpPortKey[] = "http_port";
constexpr char kWsPortKey[] = "ws_port";
constexpr char kUseTlsKey[] = "use_tls";
constexpr char kWifiSsidKey[] = "wifi_ssid";
constexpr char kWifiPasswordKey[] = "wifi_password";
constexpr char kProvisionedKey[] = "provisioned";
constexpr int kDefaultVolumePercent = 70;
constexpr int kDefaultMicroVadPauseMs = 190;
constexpr int kMinMicroVadPauseMs = 80;
constexpr int kMaxMicroVadPauseMs = 3000;

hexe::system::EndpointProvisioningSettings g_endpoint_settings = {};

int normalize_volume(int volume_percent) {
  return std::clamp(volume_percent, 0, 100);
}

int normalize_micro_vad_pause_ms(int pause_ms) {
  return std::clamp(pause_ms, kMinMicroVadPauseMs, kMaxMicroVadPauseMs);
}

bool valid_port(int port) {
  return port > 0 && port <= 65535;
}

void copy_string(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  const char *source = value == nullptr ? "" : value;
  std::strncpy(target, source, target_size - 1);
  target[target_size - 1] = '\0';
}

void load_endpoint_defaults() {
  copy_string(g_endpoint_settings.endpoint_id, sizeof(g_endpoint_settings.endpoint_id), hexe::config::kEndpointId);
  copy_string(g_endpoint_settings.display_name, sizeof(g_endpoint_settings.display_name), hexe::config::kEndpointId);
  copy_string(g_endpoint_settings.backend_host, sizeof(g_endpoint_settings.backend_host), hexe::config::kEndpointBackendHost);
  g_endpoint_settings.http_port = hexe::config::kEndpointHttpPort;
  g_endpoint_settings.ws_port = hexe::config::kEndpointWsPort;
  g_endpoint_settings.use_tls = hexe::config::kEndpointUseTls;
  copy_string(g_endpoint_settings.wifi_ssid, sizeof(g_endpoint_settings.wifi_ssid), hexe::secrets::kWifiSsid);
  copy_string(g_endpoint_settings.wifi_password, sizeof(g_endpoint_settings.wifi_password), hexe::secrets::kWifiPassword);
  g_endpoint_settings.configured = false;
}

void load_string(nvs_handle_t handle, const char *key, char *target, size_t target_size) {
  size_t length = target_size;
  const esp_err_t err = nvs_get_str(handle, key, target, &length);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    return;
  }
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to read persisted %s: %s", key, esp_err_to_name(err));
  }
}

void load_endpoint_provisioning(nvs_handle_t handle) {
  load_string(handle, kEndpointIdKey, g_endpoint_settings.endpoint_id, sizeof(g_endpoint_settings.endpoint_id));
  load_string(handle, kDisplayNameKey, g_endpoint_settings.display_name, sizeof(g_endpoint_settings.display_name));
  load_string(handle, kBackendHostKey, g_endpoint_settings.backend_host, sizeof(g_endpoint_settings.backend_host));
  load_string(handle, kWifiSsidKey, g_endpoint_settings.wifi_ssid, sizeof(g_endpoint_settings.wifi_ssid));
  load_string(handle, kWifiPasswordKey, g_endpoint_settings.wifi_password, sizeof(g_endpoint_settings.wifi_password));

  int32_t persisted_port = 0;
  esp_err_t err = nvs_get_i32(handle, kHttpPortKey, &persisted_port);
  if (err == ESP_OK && valid_port(persisted_port)) {
    g_endpoint_settings.http_port = persisted_port;
  } else if (err != ESP_ERR_NVS_NOT_FOUND && err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to read persisted HTTP port: %s", esp_err_to_name(err));
  }

  persisted_port = 0;
  err = nvs_get_i32(handle, kWsPortKey, &persisted_port);
  if (err == ESP_OK && valid_port(persisted_port)) {
    g_endpoint_settings.ws_port = persisted_port;
  } else if (err != ESP_ERR_NVS_NOT_FOUND && err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to read persisted WebSocket port: %s", esp_err_to_name(err));
  }

  uint8_t persisted_tls = 0;
  err = nvs_get_u8(handle, kUseTlsKey, &persisted_tls);
  if (err == ESP_OK) {
    g_endpoint_settings.use_tls = persisted_tls != 0;
  } else if (err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to read persisted TLS flag: %s", esp_err_to_name(err));
  }

  uint8_t provisioned = 0;
  err = nvs_get_u8(handle, kProvisionedKey, &provisioned);
  if (err == ESP_OK) {
    g_endpoint_settings.configured = provisioned != 0;
  } else if (err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to read persisted provisioning flag: %s", esp_err_to_name(err));
  }
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
  load_endpoint_defaults();

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

  load_endpoint_provisioning(handle);

  nvs_close(handle);
  ESP_LOGI(
      kTag,
      "Endpoint settings loaded: volume=%d muted=%s micro_vad_pause_ms=%d endpoint=%s backend=%s:%d",
      app_state.output_volume_percent,
      app_state.muted ? "true" : "false",
      app_state.micro_vad_pause_ms,
      g_endpoint_settings.endpoint_id,
      g_endpoint_settings.backend_host,
      g_endpoint_settings.http_port);
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

const EndpointProvisioningSettings &endpoint_provisioning_settings() {
  return g_endpoint_settings;
}

const char *endpoint_id() {
  return g_endpoint_settings.endpoint_id;
}

const char *endpoint_display_name() {
  return g_endpoint_settings.display_name;
}

const char *endpoint_backend_host() {
  return g_endpoint_settings.backend_host;
}

int endpoint_http_port() {
  return g_endpoint_settings.http_port;
}

int endpoint_ws_port() {
  return g_endpoint_settings.ws_port;
}

bool endpoint_use_tls() {
  return g_endpoint_settings.use_tls;
}

const char *wifi_ssid() {
  return g_endpoint_settings.wifi_ssid;
}

const char *wifi_password() {
  return g_endpoint_settings.wifi_password;
}

bool provisioning_configured() {
  return g_endpoint_settings.configured;
}

bool save_endpoint_provisioning(const EndpointProvisioningSettings &settings) {
  if (settings.endpoint_id[0] == '\0' || settings.backend_host[0] == '\0' || !valid_port(settings.http_port) ||
      !valid_port(settings.ws_port)) {
    ESP_LOGW(kTag, "Rejecting invalid endpoint provisioning settings");
    return false;
  }

  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open NVS for endpoint provisioning: %s", esp_err_to_name(err));
    return false;
  }

  err = nvs_set_str(handle, kEndpointIdKey, settings.endpoint_id);
  if (err == ESP_OK) {
    err = nvs_set_str(handle, kDisplayNameKey, settings.display_name);
  }
  if (err == ESP_OK) {
    err = nvs_set_str(handle, kBackendHostKey, settings.backend_host);
  }
  if (err == ESP_OK) {
    err = nvs_set_i32(handle, kHttpPortKey, settings.http_port);
  }
  if (err == ESP_OK) {
    err = nvs_set_i32(handle, kWsPortKey, settings.ws_port);
  }
  if (err == ESP_OK) {
    err = nvs_set_u8(handle, kUseTlsKey, settings.use_tls ? 1 : 0);
  }
  if (err == ESP_OK) {
    err = nvs_set_str(handle, kWifiSsidKey, settings.wifi_ssid);
  }
  if (err == ESP_OK) {
    err = nvs_set_str(handle, kWifiPasswordKey, settings.wifi_password);
  }
  if (err == ESP_OK) {
    err = nvs_set_u8(handle, kProvisionedKey, 1);
  }
  if (err == ESP_OK) {
    err = nvs_commit(handle);
  }
  nvs_close(handle);

  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to persist endpoint provisioning: %s", esp_err_to_name(err));
    return false;
  }

  g_endpoint_settings = settings;
  g_endpoint_settings.configured = true;
  ESP_LOGI(kTag, "Endpoint provisioning saved for %s at %s:%d", endpoint_id(), endpoint_backend_host(), endpoint_http_port());
  return true;
}

void reset_endpoint_provisioning() {
  nvs_handle_t handle = 0;
  esp_err_t err = nvs_open(kNamespace, NVS_READWRITE, &handle);
  if (err == ESP_OK) {
    nvs_erase_key(handle, kEndpointIdKey);
    nvs_erase_key(handle, kDisplayNameKey);
    nvs_erase_key(handle, kBackendHostKey);
    nvs_erase_key(handle, kHttpPortKey);
    nvs_erase_key(handle, kWsPortKey);
    nvs_erase_key(handle, kUseTlsKey);
    nvs_erase_key(handle, kWifiSsidKey);
    nvs_erase_key(handle, kWifiPasswordKey);
    nvs_erase_key(handle, kProvisionedKey);
    err = nvs_commit(handle);
    nvs_close(handle);
  }
  if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to reset endpoint provisioning: %s", esp_err_to_name(err));
  }
  load_endpoint_defaults();
}

}  // namespace hexe::system
