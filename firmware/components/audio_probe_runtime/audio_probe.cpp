#include "audio_probe.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "cJSON.h"
#include "endpoint_config.h"
#include "board_profile_pins.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
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
constexpr char kWsPortKey[] = "ws_port";
constexpr char kUseTlsKey[] = "use_tls";
constexpr char kWifiSsidKey[] = "wifi_ssid";
constexpr char kWifiPasswordKey[] = "wifi_password";
constexpr char kVoiceEventSchemaVersion[] = "hexevoice.voice.event.v1";
constexpr EventBits_t kWifiConnectedBit = BIT0;
constexpr EventBits_t kWifiFailedBit = BIT1;
constexpr size_t kSmallProbeBytes = 9600;
constexpr size_t kLargeProbeBytes = 245760;
constexpr size_t kMicProbeSamples = 32000;
constexpr size_t kMicProbeBytes = kMicProbeSamples * sizeof(int16_t);
constexpr size_t kSendChunkBytes = 1024;
constexpr size_t kMaxBackendEventBytes = 4096;
constexpr int kCommandQueueDepth = 4;
constexpr int kWifiMaxRetries = 20;
constexpr int kSocketTimeoutMs = 5000;
constexpr int kControlWsNetworkTimeoutMs = 1000;
constexpr int kControlWsSendTimeoutMs = 1200;
constexpr int kControlWsClientTaskStackBytes = 4096;
constexpr int kControlWsClientBufferBytes = 2048;
constexpr int kControlWsPingIntervalSec = 0;
constexpr int kControlWsPingPongTimeoutSec = 0;
constexpr int kSampleRate = 16000;
constexpr size_t kFrameSamples = 320;

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

constexpr gpio_num_t kMicBclk = gpio_pin(hexe::board::pins::kVoicePeMicBclk);
constexpr gpio_num_t kMicLrclk = gpio_pin(hexe::board::pins::kVoicePeMicLrclk);
constexpr gpio_num_t kMicDin = gpio_pin(hexe::board::pins::kVoicePeMicDin);
constexpr gpio_num_t kVoiceKitReset = gpio_pin(hexe::board::pins::kVoicePeVoiceKitReset);
constexpr i2c_port_num_t kVoiceKitI2cPort = hexe::board::pins::kVoicePeI2cPort;
constexpr gpio_num_t kVoiceKitI2cSda = gpio_pin(hexe::board::pins::kVoicePeI2cSda);
constexpr gpio_num_t kVoiceKitI2cScl = gpio_pin(hexe::board::pins::kVoicePeI2cScl);
constexpr int kMicI2sPort = hexe::board::pins::kVoicePeMicPort;
constexpr uint8_t kVoiceKitI2cAddress = static_cast<uint8_t>(hexe::board::pins::kVoicePeVoiceKitI2cAddress);
constexpr uint32_t kVoiceKitI2cClockHz = static_cast<uint32_t>(hexe::board::pins::kVoicePeI2cClockHz);
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

struct ProbeSettings {
  char endpoint_id[64];
  char backend_host[96];
  int http_port;
  int ws_port;
  bool use_tls;
  char wifi_ssid[33];
  char wifi_password[65];
};

struct CommandRequest {
  char request_id[96];
  char command_type[64];
};

EventGroupHandle_t g_wifi_event_group = nullptr;
int g_wifi_retry_count = 0;
char g_ip_address[16] = "0.0.0.0";
i2s_chan_handle_t g_rx_channel = nullptr;
i2c_master_bus_handle_t g_voice_kit_i2c_bus = nullptr;
i2c_master_dev_handle_t g_voice_kit_i2c_device = nullptr;
std::array<int32_t, kFrameSamples * 2> g_raw_samples = {};
QueueHandle_t g_command_queue = nullptr;
SemaphoreHandle_t g_ws_send_lock = nullptr;
esp_websocket_client_handle_t g_ws_client = nullptr;
bool g_ws_started = false;
bool g_ws_connected = false;
uint32_t g_sequence = 1;
std::string g_ws_rx_buffer;

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
  settings.ws_port = hexe::config::kEndpointWsPort;
  settings.use_tls = hexe::config::kEndpointUseTls;
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
    int32_t persisted_ws_port = 0;
    const esp_err_t ws_port_result = nvs_get_i32(handle, kWsPortKey, &persisted_ws_port);
    if (ws_port_result == ESP_OK && valid_port(persisted_ws_port)) {
      settings.ws_port = persisted_ws_port;
    } else if (ws_port_result != ESP_ERR_NVS_NOT_FOUND && ws_port_result != ESP_OK) {
      ESP_LOGW(kTag, "Failed to read WS port from NVS: %s", esp_err_to_name(ws_port_result));
    }
    uint8_t persisted_use_tls = settings.use_tls ? 1 : 0;
    const esp_err_t tls_result = nvs_get_u8(handle, kUseTlsKey, &persisted_use_tls);
    if (tls_result == ESP_OK) {
      settings.use_tls = persisted_use_tls != 0;
    } else if (tls_result != ESP_ERR_NVS_NOT_FOUND) {
      ESP_LOGW(kTag, "Failed to read TLS flag from NVS: %s", esp_err_to_name(tls_result));
    }
    nvs_close(handle);
  } else if (open_result != ESP_ERR_NVS_NOT_FOUND) {
    ESP_LOGW(kTag, "Failed to open endpoint settings NVS: %s", esp_err_to_name(open_result));
  }

  ESP_LOGI(
      kTag,
      "Probe settings loaded: endpoint=%s backend=%s http_port=%d ws_port=%d tls=%s wifi_ssid=%s",
      settings.endpoint_id,
      settings.backend_host,
      settings.http_port,
      settings.ws_port,
      settings.use_tls ? "true" : "false",
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

uint32_t estimate_level(const int16_t *samples, size_t count) {
  uint64_t total = 0;
  for (size_t index = 0; index < count; ++index) {
    const int32_t sample = samples[index];
    total += sample < 0 ? static_cast<uint32_t>(-sample) : static_cast<uint32_t>(sample);
  }
  return count == 0 ? 0 : static_cast<uint32_t>(total / count);
}

int16_t voice_channel_sample(int32_t left, int32_t right) {
  (void)right;
  return static_cast<int16_t>(std::clamp<int32_t>(left >> 16, -32768, 32767));
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

bool init_voice_kit_i2c() {
  i2c_master_bus_config_t bus_config = {};
  bus_config.i2c_port = kVoiceKitI2cPort;
  bus_config.sda_io_num = kVoiceKitI2cSda;
  bus_config.scl_io_num = kVoiceKitI2cScl;
  bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
  bus_config.glitch_ignore_cnt = 7;
  bus_config.flags.enable_internal_pullup = true;

  esp_err_t result = i2c_new_master_bus(&bus_config, &g_voice_kit_i2c_bus);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Voice Kit I2C bus: %s", esp_err_to_name(result));
    return false;
  }

  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = kVoiceKitI2cAddress;
  device_config.scl_speed_hz = kVoiceKitI2cClockHz;

  result = i2c_master_bus_add_device(g_voice_kit_i2c_bus, &device_config, &g_voice_kit_i2c_device);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to add Voice Kit I2C device: %s", esp_err_to_name(result));
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
  gpio_config_t output_config = {};
  output_config.pin_bit_mask = 1ULL << kVoiceKitReset;
  output_config.mode = GPIO_MODE_OUTPUT;
  gpio_config(&output_config);

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
  return true;
}

bool start_microphone_stream() {
  i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(kMicI2sPort, I2S_ROLE_SLAVE);
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
  ESP_LOGI(kTag, "Voice PE microphone probe stream initialized");
  return true;
}

size_t capture_microphone_pcm(int16_t *samples, size_t max_samples, uint32_t *level) {
  size_t captured_samples = 0;
  uint64_t level_total = 0;
  uint32_t level_frames = 0;
  while (captured_samples < max_samples) {
    size_t bytes_read = 0;
    const esp_err_t result = i2s_channel_read(
        g_rx_channel,
        g_raw_samples.data(),
        g_raw_samples.size() * sizeof(g_raw_samples[0]),
        &bytes_read,
        pdMS_TO_TICKS(500));
    if (result != ESP_OK || bytes_read == 0) {
      ESP_LOGW(kTag, "Voice PE microphone probe read failed: %s bytes=%u", esp_err_to_name(result), static_cast<unsigned>(bytes_read));
      break;
    }

    const size_t stereo_frames = std::min(bytes_read / (sizeof(int32_t) * 2), kFrameSamples);
    const size_t writable_frames = std::min(stereo_frames, max_samples - captured_samples);
    for (size_t index = 0; index < writable_frames; ++index) {
      samples[captured_samples + index] = voice_channel_sample(g_raw_samples[index * 2], g_raw_samples[(index * 2) + 1]);
    }
    level_total += estimate_level(samples + captured_samples, writable_frames);
    ++level_frames;
    captured_samples += writable_frames;
  }

  if (level != nullptr) {
    *level = level_frames == 0 ? 0 : static_cast<uint32_t>(level_total / level_frames);
  }
  return captured_samples;
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

bool append_json_escaped(std::string &target, const char *value) {
  const char *source = value == nullptr ? "" : value;
  for (const char *cursor = source; *cursor != '\0'; ++cursor) {
    const unsigned char ch = static_cast<unsigned char>(*cursor);
    if (ch == '"' || ch == '\\') {
      target.push_back('\\');
      target.push_back(static_cast<char>(ch));
    } else if (ch >= 0x20) {
      target.push_back(static_cast<char>(ch));
    } else {
      return false;
    }
  }
  return true;
}

std::string websocket_url(const ProbeSettings &settings) {
  char buffer[224] = {};
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d/api/voice/ws?endpoint_id=%s",
      settings.use_tls ? "wss" : "ws",
      settings.backend_host,
      settings.ws_port,
      settings.endpoint_id);
  return std::string(buffer);
}

void append_event_header(std::string &target, const ProbeSettings &settings, const char *event_type) {
  char prefix[384] = {};
  const long long now_us = static_cast<long long>(esp_timer_get_time());
  std::snprintf(
      prefix,
      sizeof(prefix),
      "{\"event_type\":\"%s\",\"event_id\":\"evt_%s_%u_%lld\","
      "\"schema_version\":\"%s\",\"endpoint_id\":\"%s\","
      "\"direction\":\"endpoint_to_backend\",\"session_id\":null,"
      "\"sequence\":%u,\"timestamp\":\"1970-01-01T00:00:00Z\",\"payload\":",
      event_type,
      settings.endpoint_id,
      static_cast<unsigned>(g_sequence),
      now_us,
      kVoiceEventSchemaVersion,
      settings.endpoint_id,
      static_cast<unsigned>(g_sequence));
  target.append(prefix);
}

bool send_ws_text(const std::string &payload) {
  if (g_ws_client == nullptr || !g_ws_connected) {
    ESP_LOGW(kTag, "Audio probe command WebSocket send skipped; socket is not connected");
    return false;
  }
  if (g_ws_send_lock != nullptr) {
    xSemaphoreTake(g_ws_send_lock, portMAX_DELAY);
  }
  const int written = esp_websocket_client_send_text(
      g_ws_client,
      payload.c_str(),
      static_cast<int>(payload.size()),
      pdMS_TO_TICKS(kControlWsSendTimeoutMs));
  if (g_ws_send_lock != nullptr) {
    xSemaphoreGive(g_ws_send_lock);
  }
  if (written != static_cast<int>(payload.size())) {
    ESP_LOGW(kTag, "Audio probe command WebSocket send failed written=%d expected=%u", written, static_cast<unsigned>(payload.size()));
    return false;
  }
  return true;
}

void send_command_ack(
    const ProbeSettings &settings,
    const char *request_id,
    const char *command_type,
    const char *status,
    const char *message) {
  if (request_id == nullptr || request_id[0] == '\0') {
    return;
  }
  std::string envelope;
  envelope.reserve(640);
  append_event_header(envelope, settings, "command.ack");
  envelope.append("{\"request_id\":\"");
  append_json_escaped(envelope, request_id);
  envelope.append("\",\"command_type\":\"");
  append_json_escaped(envelope, command_type == nullptr ? "unknown" : command_type);
  envelope.append("\",\"status\":\"");
  append_json_escaped(envelope, status == nullptr ? "succeeded" : status);
  envelope.append("\",\"message\":\"");
  append_json_escaped(envelope, message == nullptr ? "" : message);
  envelope.append("\"}}");
  if (send_ws_text(envelope)) {
    ESP_LOGI(kTag, "Audio probe command ack sent request_id=%s command=%s status=%s", request_id, command_type, status);
  }
  ++g_sequence;
}

void send_command_error(
    const ProbeSettings &settings,
    const char *request_id,
    const char *command_type,
    const char *code,
    const char *message) {
  if (request_id == nullptr || request_id[0] == '\0') {
    return;
  }
  std::string envelope;
  envelope.reserve(640);
  append_event_header(envelope, settings, "command.error");
  envelope.append("{\"request_id\":\"");
  append_json_escaped(envelope, request_id);
  envelope.append("\",\"command_type\":\"");
  append_json_escaped(envelope, command_type == nullptr ? "unknown" : command_type);
  envelope.append("\",\"code\":\"");
  append_json_escaped(envelope, code == nullptr ? "command_failed" : code);
  envelope.append("\",\"message\":\"");
  append_json_escaped(envelope, message == nullptr ? "Command failed" : message);
  envelope.append("\",\"recoverable\":true}}");
  if (send_ws_text(envelope)) {
    ESP_LOGW(kTag, "Audio probe command error sent request_id=%s command=%s code=%s", request_id, command_type, code);
  }
  ++g_sequence;
}

const char *payload_request_id(cJSON *payload) {
  cJSON *request_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "request_id") : nullptr;
  return cJSON_IsString(request_id) ? request_id->valuestring : "";
}

void enqueue_listen_command(cJSON *payload) {
  if (g_command_queue == nullptr) {
    return;
  }
  const char *request_id = payload_request_id(payload);
  if (request_id[0] == '\0') {
    ESP_LOGW(kTag, "Audio probe endpoint.listen missing request_id");
    return;
  }
  CommandRequest request = {};
  copy_string(request.request_id, sizeof(request.request_id), request_id);
  copy_string(request.command_type, sizeof(request.command_type), "endpoint.listen");
  if (xQueueSend(g_command_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Audio probe command queue full; dropping endpoint.listen request_id=%s", request_id);
  }
}

void handle_backend_event_json(const ProbeSettings &settings, const std::string &message) {
  cJSON *root = cJSON_ParseWithLength(message.data(), message.size());
  if (root == nullptr) {
    ESP_LOGW(kTag, "Audio probe received invalid backend event JSON bytes=%u", static_cast<unsigned>(message.size()));
    return;
  }
  cJSON *type = cJSON_GetObjectItem(root, "event_type");
  cJSON *payload = cJSON_GetObjectItem(root, "payload");
  if (!cJSON_IsString(type)) {
    ESP_LOGW(kTag, "Audio probe received backend event without event_type");
    cJSON_Delete(root);
    return;
  }

  ESP_LOGI(kTag, "Audio probe command WebSocket event type=%s", type->valuestring);
  if (std::strcmp(type->valuestring, "endpoint.listen") == 0) {
    send_command_ack(settings, payload_request_id(payload), "endpoint.listen", "accepted", "OK");
    enqueue_listen_command(payload);
  } else if (std::strncmp(type->valuestring, "endpoint.", 9) == 0) {
    send_command_error(
        settings,
        payload_request_id(payload),
        type->valuestring,
        "unsupported_command",
        "Audio probe only supports endpoint.listen");
  }
  cJSON_Delete(root);
}

void handle_websocket_data(const ProbeSettings &settings, const esp_websocket_event_data_t *data) {
  if (data == nullptr || data->data_ptr == nullptr || data->data_len <= 0) {
    return;
  }
  if (data->op_code != WS_TRANSPORT_OPCODES_TEXT && data->op_code != WS_TRANSPORT_OPCODES_CONT) {
    g_ws_rx_buffer.clear();
    return;
  }
  if (data->payload_len <= 0 || data->payload_len > static_cast<int>(kMaxBackendEventBytes)) {
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Dropping oversized audio probe command event bytes=%d", data->payload_len);
    return;
  }
  if (data->payload_offset == 0) {
    g_ws_rx_buffer.clear();
    g_ws_rx_buffer.reserve(data->payload_len);
  }
  if (data->payload_offset != static_cast<int>(g_ws_rx_buffer.size())) {
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Dropping out-of-order audio probe command event chunk");
    return;
  }

  g_ws_rx_buffer.append(data->data_ptr, data->data_len);
  const int received = data->payload_offset + data->data_len;
  if (received < data->payload_len) {
    return;
  }

  handle_backend_event_json(settings, g_ws_rx_buffer);
  g_ws_rx_buffer.clear();
}

void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
  (void)base;
  const auto *settings = static_cast<const ProbeSettings *>(handler_args);
  if (event_id == WEBSOCKET_EVENT_CONNECTED) {
    g_ws_connected = true;
    g_ws_rx_buffer.clear();
    ESP_LOGI(kTag, "Audio probe command WebSocket connected");
  } else if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
    g_ws_connected = false;
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Audio probe command WebSocket disconnected");
  } else if (event_id == WEBSOCKET_EVENT_ERROR) {
    g_ws_connected = false;
    ESP_LOGW(kTag, "Audio probe command WebSocket error");
  } else if (event_id == WEBSOCKET_EVENT_DATA && settings != nullptr) {
    handle_websocket_data(*settings, static_cast<esp_websocket_event_data_t *>(event_data));
  }
}

bool start_command_websocket(const ProbeSettings &settings) {
  if (g_command_queue == nullptr) {
    g_command_queue = xQueueCreate(kCommandQueueDepth, sizeof(CommandRequest));
    if (g_command_queue == nullptr) {
      ESP_LOGE(kTag, "Failed to create audio probe command queue");
      return false;
    }
  }
  if (g_ws_send_lock == nullptr) {
    g_ws_send_lock = xSemaphoreCreateMutex();
    if (g_ws_send_lock == nullptr) {
      ESP_LOGE(kTag, "Failed to create audio probe command WebSocket send lock");
      return false;
    }
  }
  if (g_ws_client == nullptr) {
    const std::string uri = websocket_url(settings);
    esp_websocket_client_config_t config = {};
    config.uri = uri.c_str();
    config.reconnect_timeout_ms = hexe::config::kEndpointReconnectBackoffMs;
    config.network_timeout_ms = kControlWsNetworkTimeoutMs;
    config.task_name = "hexe_probe_cmd_ws";
    config.task_stack = kControlWsClientTaskStackBytes;
    config.task_prio = 5;
    config.buffer_size = kControlWsClientBufferBytes;
    config.ping_interval_sec = kControlWsPingIntervalSec;
    config.pingpong_timeout_sec = kControlWsPingPongTimeoutSec;
    config.disable_pingpong_discon = true;
    config.keep_alive_enable = true;
    config.keep_alive_idle = 10;
    config.keep_alive_interval = 5;
    config.keep_alive_count = 3;
    g_ws_client = esp_websocket_client_init(&config);
    if (g_ws_client == nullptr) {
      ESP_LOGE(kTag, "Failed to initialize audio probe command WebSocket client uri=%s", uri.c_str());
      return false;
    }
    esp_websocket_register_events(g_ws_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, const_cast<ProbeSettings *>(&settings));
    ESP_LOGI(kTag, "Audio probe command WebSocket initialized uri=%s", uri.c_str());
  }
  if (!g_ws_started) {
    const esp_err_t result = esp_websocket_client_start(g_ws_client);
    if (result != ESP_OK) {
      ESP_LOGW(kTag, "Audio probe command WebSocket start failed: %s", esp_err_to_name(result));
      return false;
    }
    g_ws_started = true;
  }
  return true;
}

void run_generated_probe_sequence(const ProbeSettings &settings) {
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

bool ensure_microphone_ready() {
  if (g_rx_channel != nullptr) {
    return true;
  }
  if (!init_voice_kit() || !start_microphone_stream()) {
    ESP_LOGE(kTag, "Skipping PE microphone probe because microphone initialization failed");
    return false;
  }
  return true;
}

bool run_microphone_probe(const ProbeSettings &settings, const char *source) {
  if (!ensure_microphone_ready()) {
    return false;
  }
  int16_t *mic_audio = static_cast<int16_t *>(heap_caps_malloc(kMicProbeBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (mic_audio == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate microphone probe buffer bytes=%u free_psram=%u", static_cast<unsigned>(kMicProbeBytes), static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    return false;
  }
  uint32_t level = 0;
  const size_t captured_samples = capture_microphone_pcm(mic_audio, kMicProbeSamples, &level);
  ESP_LOGI(
      kTag,
      "Voice PE microphone probe captured samples=%u bytes=%u level=%u",
      static_cast<unsigned>(captured_samples),
      static_cast<unsigned>(captured_samples * sizeof(int16_t)),
      static_cast<unsigned>(level));
  bool uploaded = false;
  if (captured_samples > 0) {
    uploaded = post_probe(settings, source, reinterpret_cast<const char *>(mic_audio), captured_samples * sizeof(int16_t), true);
  }
  heap_caps_free(mic_audio);
  return uploaded;
}

void run_probe_sequence(const ProbeSettings &settings) {
  run_generated_probe_sequence(settings);
  run_microphone_probe(settings, "pe-mic-staged");
}

void handle_command_loop(const ProbeSettings &settings) {
  if (g_command_queue == nullptr) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    return;
  }
  CommandRequest request = {};
  while (xQueueReceive(g_command_queue, &request, pdMS_TO_TICKS(1000)) == pdTRUE) {
    ESP_LOGI(kTag, "Audio probe executing command=%s request_id=%s", request.command_type, request.request_id);
    if (std::strcmp(request.command_type, "endpoint.listen") == 0) {
      const bool ok = run_microphone_probe(settings, "pe-mic-command-staged");
      if (ok) {
        send_command_ack(settings, request.request_id, request.command_type, "succeeded", "Probe microphone upload completed");
      } else {
        send_command_error(settings, request.request_id, request.command_type, "probe_upload_failed", "Probe microphone upload failed");
      }
    }
  }
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

  start_command_websocket(settings);
  run_probe_sequence(settings);
  ESP_LOGI(kTag, "Audio probe sequence complete; idling");
  while (true) {
    if (!g_ws_started) {
      start_command_websocket(settings);
    }
    handle_command_loop(settings);
  }
}

}  // namespace hexe::audio_probe
