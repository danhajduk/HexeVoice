#include "board/wifi.h"

#include <cstring>

#include "app_state.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "secrets/wifi_secrets.h"

namespace {
constexpr char kTag[] = "hexe_wifi";
bool g_wifi_initialized = false;

bool has_wifi_credentials() {
  return hexe::secrets::kWifiSsid[0] != '\0';
}

void update_rssi_from_ap_info() {
  wifi_ap_record_t ap_info = {};
  if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
    hexe::state().wifi_rssi = ap_info.rssi;
  }
}

void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
  (void) arg;
  (void) event_data;

  auto &state = hexe::state();

  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    ESP_LOGI(kTag, "Wi-Fi started, connecting to configured network");
    state.phase = hexe::AppPhase::kWiFiConnecting;
    ESP_ERROR_CHECK(esp_wifi_connect());
    return;
  }

  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    state.wifi_connected = false;
    state.backend_connected = false;
    state.voice_ws_connected = false;
    state.wifi_rssi = -100;
    state.phase = hexe::AppPhase::kWiFiConnecting;
    ESP_LOGW(kTag, "Wi-Fi disconnected, retrying");
    ESP_ERROR_CHECK(esp_wifi_connect());
    return;
  }

  if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    const auto *event = static_cast<const ip_event_got_ip_t *>(event_data);
    state.wifi_connected = true;
    state.phase = hexe::AppPhase::kBackendConnecting;
    update_rssi_from_ap_info();
    ESP_LOGI(
        kTag, "Connected to Wi-Fi with IP " IPSTR, IP2STR(&event->ip_info.ip));
  }
}
}

namespace hexe::board {

void init_wifi() {
  auto &state = hexe::state();
  state.wifi_connected = false;
  state.phase = hexe::AppPhase::kWiFiConnecting;

  if (!has_wifi_credentials()) {
    ESP_LOGW(kTag, "Wi-Fi credentials are empty in firmware/main/secrets/wifi_secrets.h");
    return;
  }

  if (!g_wifi_initialized) {
    ESP_ERROR_CHECK(esp_netif_init());

    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
      ESP_ERROR_CHECK(err);
    }

    esp_netif_create_default_wifi_sta();

    const wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, nullptr));
    g_wifi_initialized = true;
  }

  wifi_config_t wifi_config = {};
  std::strncpy(
      reinterpret_cast<char *>(wifi_config.sta.ssid), hexe::secrets::kWifiSsid, sizeof(wifi_config.sta.ssid) - 1);
  std::strncpy(
      reinterpret_cast<char *>(wifi_config.sta.password),
      hexe::secrets::kWifiPassword,
      sizeof(wifi_config.sta.password) - 1);
  wifi_config.sta.threshold.authmode = WIFI_AUTH_OPEN;
  wifi_config.sta.pmf_cfg.capable = true;
  wifi_config.sta.pmf_cfg.required = false;

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
  ESP_ERROR_CHECK(esp_wifi_start());

  ESP_LOGI(kTag, "Wi-Fi init complete, waiting for connection to SSID '%s'", hexe::secrets::kWifiSsid);
}

void refresh_wifi_status() {
  auto &state = hexe::state();
  if (!state.wifi_connected) {
    return;
  }

  update_rssi_from_ap_info();
}

}  // namespace hexe::board
