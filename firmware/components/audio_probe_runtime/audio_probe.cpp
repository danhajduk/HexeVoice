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
#include "board_profile_pins.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
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
constexpr size_t kMicProbeSamples = 32000;
constexpr size_t kMicProbeBytes = kMicProbeSamples * sizeof(int16_t);
constexpr size_t kSendChunkBytes = 1024;
constexpr int kWifiMaxRetries = 20;
constexpr int kSocketTimeoutMs = 5000;
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
  char wifi_ssid[33];
  char wifi_password[65];
};

EventGroupHandle_t g_wifi_event_group = nullptr;
int g_wifi_retry_count = 0;
char g_ip_address[16] = "0.0.0.0";
i2s_chan_handle_t g_rx_channel = nullptr;
i2c_master_bus_handle_t g_voice_kit_i2c_bus = nullptr;
i2c_master_dev_handle_t g_voice_kit_i2c_device = nullptr;
std::array<int32_t, kFrameSamples * 2> g_raw_samples = {};

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

  if (!init_voice_kit() || !start_microphone_stream()) {
    ESP_LOGE(kTag, "Skipping PE microphone probe because microphone initialization failed");
    return;
  }
  int16_t *mic_audio = static_cast<int16_t *>(heap_caps_malloc(kMicProbeBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (mic_audio == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate microphone probe buffer bytes=%u free_psram=%u", static_cast<unsigned>(kMicProbeBytes), static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    return;
  }
  uint32_t level = 0;
  const size_t captured_samples = capture_microphone_pcm(mic_audio, kMicProbeSamples, &level);
  ESP_LOGI(
      kTag,
      "Voice PE microphone probe captured samples=%u bytes=%u level=%u",
      static_cast<unsigned>(captured_samples),
      static_cast<unsigned>(captured_samples * sizeof(int16_t)),
      static_cast<unsigned>(level));
  if (captured_samples > 0) {
    post_probe(settings, "pe-mic-staged", reinterpret_cast<const char *>(mic_audio), captured_samples * sizeof(int16_t), true);
  }
  heap_caps_free(mic_audio);
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
