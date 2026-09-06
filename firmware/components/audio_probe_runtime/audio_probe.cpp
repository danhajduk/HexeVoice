#include "audio_probe.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "endpoint_config.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"
#include "nvs.h"
#include "nvs_flash.h"

#if __has_include("secrets/wifi_secrets.h")
#include "secrets/wifi_secrets.h"
#else
namespace hexe::secrets {
constexpr const char *kWifiSsid = "";
constexpr const char *kWifiPassword = "";
}  // namespace hexe::secrets
#endif

namespace {
constexpr char kTag[] = "hexe_audio_probe";
constexpr char kNvsNamespace[] = "hexe_settings";
constexpr char kEndpointIdKey[] = "endpoint_id";
constexpr char kBackendHostKey[] = "backend_host";
constexpr char kHttpPortKey[] = "http_port";
constexpr char kWifiSsidKey[] = "wifi_ssid";
constexpr char kWifiPasswordKey[] = "wifi_password";
constexpr EventBits_t kWifiConnectedBit = BIT0;
constexpr EventBits_t kWifiFailedBit = BIT1;
constexpr size_t kSmallProbeBytes = 9600;
constexpr size_t kLargeProbeBytes = 245760;
constexpr size_t kSendChunkBytes = 1024;
constexpr int kWifiMaxRetries = 20;
constexpr int kSocketTimeoutMs = 5000;

struct ProbeSettings {
  char endpoint_id[64];
  char backend_host[96];
  int http_port;
  char wifi_ssid[33];
  char wifi_password[65];
};

EventGroupHandle_t g_wifi_event_group = nullptr;
int g_wifi_retry_count = 0;
char g_ip_address[16] = "0.0.0.0";

void copy_string(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  const char *source = value == nullptr ? "" : value;
  std::strncpy(target, source, target_size - 1);
  target[target_size - 1] = '\0';
}

bool valid_port(int port) {
  return port > 0 && port <= 65535;
}

void load_nvs_string(nvs_handle_t handle, const char *key, char *target, size_t target_size) {
  size_t length = target_size;
  const esp_err_t err = nvs_get_str(handle, key, target, &length);
  if (err == ESP_ERR_NVS_NOT_FOUND) {
    return;
  }
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to read %s from NVS: %s", key, esp_err_to_name(err));
  }
}

ProbeSettings load_settings() {
  ProbeSettings settings = {};
  copy_string(settings.endpoint_id, sizeof(settings.endpoint_id), hexe::config::kEndpointId);
  copy_string(settings.backend_host, sizeof(settings.backend_host), hexe::config::kEndpointBackendHost);
  settings.http_port = hexe::config::kEndpointHttpPort;
  copy_string(settings.wifi_ssid, sizeof(settings.wifi_ssid), hexe::secrets::kWifiSsid);
  copy_string(settings.wifi_password, sizeof(settings.wifi_password), hexe::secrets::kWifiPassword);

  nvs_handle_t handle = 0;
  const esp_err_t open_result = nvs_open(kNvsNamespace, NVS_READONLY, &handle);
  if (open_result == ESP_OK) {
    load_nvs_string(handle, kEndpointIdKey, settings.endpoint_id, sizeof(settings.endpoint_id));
    load_nvs_string(handle, kBackendHostKey, settings.backend_host, sizeof(settings.backend_host));
    load_nvs_string(handle, kWifiSsidKey, settings.wifi_ssid, sizeof(settings.wifi_ssid));
    load_nvs_string(handle, kWifiPasswordKey, settings.wifi_password, sizeof(settings.wifi_password));
    int32_t persisted_http_port = 0;
    const esp_err_t port_result = nvs_get_i32(handle, kHttpPortKey, &persisted_http_port);
    if (port_result == ESP_OK && valid_port(persisted_http_port)) {
      settings.http_port = persisted_http_port;
    } else if (port_result != ESP_ERR_NVS_NOT_FOUND && port_result != ESP_OK) {
      ESP_LOGW(kTag, "Failed to read HTTP port from NVS: %s", esp_err_to_name(port_result));
    }
    nvs_close(handle);
  } else if (open_result != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to open endpoint settings NVS: %s", esp_err_to_name(open_result));
  }

  ESP_LOGI(
      kTag,
      "Probe settings loaded: endpoint=%s backend=%s:%d wifi_ssid=%s",
      settings.endpoint_id,
      settings.backend_host,
      settings.http_port,
      settings.wifi_ssid[0] == '\0' ? "<empty>" : settings.wifi_ssid);
  return settings;
}

void wifi_event_handler(void *arg, esp_event_base_t event_base, int32_t event_id, void *event_data) {
  (void)arg;
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
    ESP_LOGI(kTag, "Wi-Fi started, connecting");
    esp_wifi_connect();
    return;
  }
  if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
    if (g_wifi_retry_count++ < kWifiMaxRetries) {
      ESP_LOGW(kTag, "Wi-Fi disconnected, retrying attempt=%d", g_wifi_retry_count);
      esp_wifi_connect();
    } else {
      ESP_LOGE(kTag, "Wi-Fi failed after %d retries", g_wifi_retry_count);
      xEventGroupSetBits(g_wifi_event_group, kWifiFailedBit);
    }
    return;
  }
  if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
    const auto *event = static_cast<const ip_event_got_ip_t *>(event_data);
    std::snprintf(g_ip_address, sizeof(g_ip_address), IPSTR, IP2STR(&event->ip_info.ip));
    ESP_LOGI(kTag, "Wi-Fi connected ip=%s", g_ip_address);
    xEventGroupSetBits(g_wifi_event_group, kWifiConnectedBit);
  }
}

bool connect_wifi(const ProbeSettings &settings) {
  if (settings.wifi_ssid[0] == '\0') {
    ESP_LOGE(kTag, "Wi-Fi SSID is empty; provision the endpoint once or add firmware Wi-Fi secrets");
    return false;
  }

  g_wifi_event_group = xEventGroupCreate();
  if (g_wifi_event_group == nullptr) {
    ESP_LOGE(kTag, "Failed to create Wi-Fi event group");
    return false;
  }

  ESP_ERROR_CHECK(esp_netif_init());
  esp_err_t err = esp_event_loop_create_default();
  if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
    ESP_ERROR_CHECK(err);
  }
  esp_netif_create_default_wifi_sta();

  wifi_init_config_t wifi_init_config = WIFI_INIT_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(esp_wifi_init(&wifi_init_config));
  ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, nullptr));
  ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, nullptr));

  wifi_config_t wifi_config = {};
  copy_string(reinterpret_cast<char *>(wifi_config.sta.ssid), sizeof(wifi_config.sta.ssid), settings.wifi_ssid);
  copy_string(
      reinterpret_cast<char *>(wifi_config.sta.password),
      sizeof(wifi_config.sta.password),
      settings.wifi_password);
  wifi_config.sta.threshold.authmode = WIFI_AUTH_OPEN;
  wifi_config.sta.pmf_cfg.capable = true;
  wifi_config.sta.pmf_cfg.required = false;

  ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
  ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
  ESP_ERROR_CHECK(esp_wifi_start());

  const EventBits_t bits = xEventGroupWaitBits(
      g_wifi_event_group,
      kWifiConnectedBit | kWifiFailedBit,
      pdFALSE,
      pdFALSE,
      pdMS_TO_TICKS(45000));
  if ((bits & kWifiConnectedBit) != 0) {
    return true;
  }
  ESP_LOGE(kTag, "Wi-Fi connection timed out or failed");
  return false;
}

int connect_socket(const ProbeSettings &settings, int *last_errno) {
  if (last_errno != nullptr) {
    *last_errno = 0;
  }

  char port[8] = {};
  std::snprintf(port, sizeof(port), "%d", settings.http_port);

  addrinfo hints = {};
  hints.ai_family = AF_INET;
  hints.ai_socktype = SOCK_STREAM;
  addrinfo *result = nullptr;
  int err = getaddrinfo(settings.backend_host, port, &hints, &result);
  if (err != 0 || result == nullptr) {
    ESP_LOGE(kTag, "DNS lookup failed for %s:%s err=%d", settings.backend_host, port, err);
    if (last_errno != nullptr) {
      *last_errno = errno;
    }
    return -1;
  }

  int sock = -1;
  for (addrinfo *item = result; item != nullptr; item = item->ai_next) {
    sock = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
    if (sock < 0) {
      if (last_errno != nullptr) {
        *last_errno = errno;
      }
      continue;
    }
    timeval timeout = {};
    timeout.tv_sec = kSocketTimeoutMs / 1000;
    timeout.tv_usec = (kSocketTimeoutMs % 1000) * 1000;
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    if (connect(sock, item->ai_addr, item->ai_addrlen) == 0) {
      break;
    }
    if (last_errno != nullptr) {
      *last_errno = errno;
    }
    close(sock);
    sock = -1;
  }
  freeaddrinfo(result);
  return sock;
}

bool send_all(int sock, const char *data, size_t size, size_t *written, int *last_errno) {
  size_t offset = 0;
  while (offset < size) {
    const ssize_t result = send(sock, data + offset, size - offset, 0);
    if (result < 0) {
      if (last_errno != nullptr) {
        *last_errno = errno;
      }
      if (written != nullptr) {
        *written += offset;
      }
      return false;
    }
    if (result == 0) {
      if (last_errno != nullptr) {
        *last_errno = 0;
      }
      if (written != nullptr) {
        *written += offset;
      }
      return false;
    }
    offset += static_cast<size_t>(result);
  }
  if (written != nullptr) {
    *written += offset;
  }
  return true;
}

bool send_body(int sock, const char *data, size_t size, bool stage_body, size_t *written, int *last_errno) {
  std::array<char, kSendChunkBytes> stage = {};
  size_t offset = 0;
  while (offset < size) {
    const size_t chunk = std::min(kSendChunkBytes, size - offset);
    const char *chunk_data = data + offset;
    if (stage_body) {
      std::memcpy(stage.data(), data + offset, chunk);
      chunk_data = stage.data();
    }
    if (!send_all(sock, chunk_data, chunk, written, last_errno)) {
      return false;
    }
    offset += chunk;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  return true;
}

bool read_response(int sock, int *status_code, size_t *response_bytes) {
  std::array<char, 256> buffer = {};
  bool parsed_status = false;
  if (status_code != nullptr) {
    *status_code = 0;
  }
  if (response_bytes != nullptr) {
    *response_bytes = 0;
  }

  while (true) {
    const ssize_t result = recv(sock, buffer.data(), buffer.size() - 1, 0);
    if (result < 0) {
      if (errno == EAGAIN || errno == EWOULDBLOCK) {
        return parsed_status;
      }
      return false;
    }
    if (result == 0) {
      return parsed_status;
    }
    if (response_bytes != nullptr) {
      *response_bytes += static_cast<size_t>(result);
    }
    if (!parsed_status && result >= 12 && std::memcmp(buffer.data(), "HTTP/", 5) == 0) {
      if (status_code != nullptr) {
        *status_code = std::atoi(buffer.data() + 9);
      }
      parsed_status = true;
    }
  }
}

void fill_pcm_pattern(char *data, size_t size) {
  for (size_t index = 0; index + 1 < size; index += 2) {
    const int16_t sample = static_cast<int16_t>(((index / 2) % 160) * 120 - 9600);
    data[index] = static_cast<char>(sample & 0xff);
    data[index + 1] = static_cast<char>((sample >> 8) & 0xff);
  }
}

bool post_probe(
    const ProbeSettings &settings,
    const char *source,
    const char *data,
    size_t size,
    bool stage_body) {
  int last_errno = 0;
  const int sock = connect_socket(settings, &last_errno);
  if (sock < 0) {
    ESP_LOGE(kTag, "Probe %s socket connect failed errno=%d", source, last_errno);
    return false;
  }

  char path[192] = {};
  std::snprintf(
      path,
      sizeof(path),
      "/api/voice/audio/probe?endpoint_id=%s&source=%s",
      settings.endpoint_id,
      source);
  char header[384] = {};
  const int header_length = std::snprintf(
      header,
      sizeof(header),
      "POST %s HTTP/1.1\r\n"
      "Host: %s:%d\r\n"
      "Content-Type: application/octet-stream\r\n"
      "Content-Length: %u\r\n"
      "Connection: close\r\n"
      "\r\n",
      path,
      settings.backend_host,
      settings.http_port,
      static_cast<unsigned>(size));
  if (header_length <= 0 || static_cast<size_t>(header_length) >= sizeof(header)) {
    ESP_LOGE(kTag, "Probe %s HTTP header overflow", source);
    close(sock);
    return false;
  }

  ESP_LOGI(kTag, "Probe %s upload starting bytes=%u staged=%s", source, static_cast<unsigned>(size), stage_body ? "true" : "false");
  size_t written = 0;
  bool ok = send_all(sock, header, static_cast<size_t>(header_length), &written, &last_errno);
  if (ok) {
    ok = send_body(sock, data, size, stage_body, &written, &last_errno);
  }

  shutdown(sock, SHUT_WR);
  int status_code = 0;
  size_t response_bytes = 0;
  const bool response_ok = read_response(sock, &status_code, &response_bytes);
  close(sock);

  ESP_LOGI(
      kTag,
      "Probe %s upload finished ok=%s written=%u expected=%u errno=%d status=%d response_bytes=%u",
      source,
      ok ? "true" : "false",
      static_cast<unsigned>(written),
      static_cast<unsigned>(static_cast<size_t>(header_length) + size),
      last_errno,
      status_code,
      static_cast<unsigned>(response_bytes));
  return ok && response_ok && status_code >= 200 && status_code < 300;
}

void run_probe_sequence(const ProbeSettings &settings) {
  char *internal_audio = static_cast<char *>(heap_caps_malloc(kSmallProbeBytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  if (internal_audio == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate internal probe buffer bytes=%u free_internal=%u", static_cast<unsigned>(kSmallProbeBytes), static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL)));
  } else {
    fill_pcm_pattern(internal_audio, kSmallProbeBytes);
    post_probe(settings, "internal-staged", internal_audio, kSmallProbeBytes, true);
    heap_caps_free(internal_audio);
  }

  char *psram_audio = static_cast<char *>(heap_caps_malloc(kLargeProbeBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (psram_audio == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate PSRAM probe buffer bytes=%u free_psram=%u", static_cast<unsigned>(kLargeProbeBytes), static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    return;
  }

  fill_pcm_pattern(psram_audio, kLargeProbeBytes);
  post_probe(settings, "psram-direct", psram_audio, kSmallProbeBytes, false);
  post_probe(settings, "psram-staged-small", psram_audio, kSmallProbeBytes, true);
  post_probe(settings, "psram-staged-large", psram_audio, kLargeProbeBytes, true);
  heap_caps_free(psram_audio);
}
}  // namespace

namespace hexe::audio_probe {

void run() {
  esp_err_t nvs_result = nvs_flash_init();
  if (nvs_result == ESP_ERR_NVS_NO_FREE_PAGES || nvs_result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    ESP_ERROR_CHECK(nvs_flash_erase());
    nvs_result = nvs_flash_init();
  }
  ESP_ERROR_CHECK(nvs_result);

  const ProbeSettings settings = load_settings();
  if (!connect_wifi(settings)) {
    ESP_LOGE(kTag, "Audio probe stopped because Wi-Fi is unavailable");
    return;
  }

  run_probe_sequence(settings);
  ESP_LOGI(kTag, "Audio probe sequence complete; idling");
  while (true) {
    vTaskDelay(pdMS_TO_TICKS(10000));
  }
}

}  // namespace hexe::audio_probe
