#include "voice/backend_client.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <string>

#include "app_state.h"
#include "endpoint_config.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"

namespace {
constexpr char kTag[] = "hexe_backend";
constexpr size_t kAudioQueueDepth = 8;
constexpr int kTaskStackBytes = 6144;
constexpr int kTaskPriority = 4;
constexpr int kMaxChunkSamples = hexe::config::kEndpointAudioChunkSamples;

struct AudioFrame {
  std::array<int16_t, kMaxChunkSamples> samples;
  size_t sample_count;
  uint32_t level;
  bool vad_speaking;
};

QueueHandle_t g_audio_queue = nullptr;
esp_websocket_client_handle_t g_ws_client = nullptr;
TaskHandle_t g_heartbeat_task = nullptr;
TaskHandle_t g_ws_task = nullptr;
uint32_t g_chunk_index = 0;
uint32_t g_session_counter = 0;
uint32_t g_sequence = 0;
bool g_session_started = false;
bool g_ws_connected = false;
std::string g_session_id;

const char *scheme_http() {
  return hexe::config::kEndpointUseTls ? "https" : "http";
}

const char *scheme_ws() {
  return hexe::config::kEndpointUseTls ? "wss" : "ws";
}

const char *device_state() {
  const auto &state = hexe::state();
  switch (state.phase) {
    case hexe::AppPhase::kListening:
      return "listening";
    case hexe::AppPhase::kThinking:
      return "thinking";
    case hexe::AppPhase::kSpeaking:
      return "speaking";
    case hexe::AppPhase::kError:
      return "offline";
    default:
      return "idle";
  }
}

std::string heartbeat_url() {
  char buffer[192];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d%s",
      scheme_http(),
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointHttpPort,
      hexe::config::kEndpointHeartbeatPath);
  return std::string(buffer);
}

std::string websocket_url() {
  char buffer[192];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d%s",
      scheme_ws(),
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointWsPort,
      hexe::config::kEndpointVoiceWsPath);
  return std::string(buffer);
}

std::string base64_audio(const int16_t *samples, size_t sample_count) {
  const auto *bytes = reinterpret_cast<const unsigned char *>(samples);
  const size_t byte_count = sample_count * sizeof(int16_t);
  size_t encoded_len = 0;
  mbedtls_base64_encode(nullptr, 0, &encoded_len, bytes, byte_count);
  std::string encoded(encoded_len, '\0');
  int result = mbedtls_base64_encode(
      reinterpret_cast<unsigned char *>(encoded.data()),
      encoded.size(),
      &encoded_len,
      bytes,
      byte_count);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to base64 encode audio chunk: %d", result);
    return std::string();
  }
  encoded.resize(encoded_len);
  return encoded;
}

void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
  (void)handler_args;
  (void)base;
  (void)event_data;

  if (event_id == WEBSOCKET_EVENT_CONNECTED) {
    g_ws_connected = true;
    g_session_started = false;
    ESP_LOGI(kTag, "Voice WebSocket connected");
  } else if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
    g_ws_connected = false;
    g_session_started = false;
    ESP_LOGW(kTag, "Voice WebSocket disconnected");
  } else if (event_id == WEBSOCKET_EVENT_ERROR) {
    g_ws_connected = false;
    ESP_LOGW(kTag, "Voice WebSocket error");
  }
}

bool send_ws_text(const std::string &message) {
  if (g_ws_client == nullptr || !g_ws_connected) {
    return false;
  }
  const int written = esp_websocket_client_send_text(g_ws_client, message.c_str(), message.size(), pdMS_TO_TICKS(1000));
  return written >= 0;
}

void ensure_session_started() {
  if (g_session_started || !g_ws_connected) {
    return;
  }
  ++g_session_counter;
  g_chunk_index = 0;
  char session_buffer[96];
  std::snprintf(
      session_buffer,
      sizeof(session_buffer),
      "%s-%" PRIu32,
      hexe::config::kEndpointId,
      g_session_counter);
  g_session_id = session_buffer;

  char payload[768];
  std::snprintf(
      payload,
      sizeof(payload),
      "{\"event_type\":\"session.start\",\"endpoint_id\":\"%s\",\"direction\":\"endpoint_to_backend\","
      "\"session_id\":\"%s\",\"sequence\":%" PRIu32 ",\"payload\":{\"firmware_version\":\"%s\","
      "\"wake_source\":\"unknown\",\"audio_format\":{\"encoding\":\"%s\",\"sample_rate_hz\":%d,\"channels\":%d}}}",
      hexe::config::kEndpointId,
      g_session_id.c_str(),
      g_sequence++,
      hexe::config::kEndpointFirmwareVersion,
      hexe::config::kEndpointAudioEncoding,
      hexe::config::kEndpointAudioSampleRateHz,
      hexe::config::kEndpointAudioChannels);

  g_session_started = send_ws_text(payload);
  if (g_session_started) {
    ESP_LOGI(kTag, "Started voice session %s", g_session_id.c_str());
  }
}

void send_audio_frame(const AudioFrame &frame) {
  ensure_session_started();
  if (!g_session_started) {
    ESP_LOGW(kTag, "Dropping audio frame because voice session is not connected");
    return;
  }

  const std::string encoded = base64_audio(frame.samples.data(), frame.sample_count);
  if (encoded.empty()) {
    return;
  }

  std::string payload;
  payload.reserve(encoded.size() + 512);
  char prefix[512];
  std::snprintf(
      prefix,
      sizeof(prefix),
      "{\"event_type\":\"audio.chunk\",\"endpoint_id\":\"%s\",\"direction\":\"endpoint_to_backend\","
      "\"session_id\":\"%s\",\"sequence\":%" PRIu32 ",\"payload\":{\"chunk_index\":%" PRIu32 ","
      "\"audio_format\":{\"encoding\":\"%s\",\"sample_rate_hz\":%d,\"channels\":%d},\"payload_base64\":\"",
      hexe::config::kEndpointId,
      g_session_id.c_str(),
      g_sequence++,
      g_chunk_index++,
      hexe::config::kEndpointAudioEncoding,
      hexe::config::kEndpointAudioSampleRateHz,
      hexe::config::kEndpointAudioChannels);
  payload.append(prefix);
  payload.append(encoded);
  payload.append("\",\"is_final\":false}}");

  if (!send_ws_text(payload)) {
    ESP_LOGW(kTag, "Failed to send audio chunk to voice WebSocket");
  }
}

void heartbeat_task(void *arg) {
  (void)arg;
  const std::string url = heartbeat_url();

  while (true) {
    char body[384];
    std::snprintf(
        body,
        sizeof(body),
        "{\"endpoint_id\":\"%s\",\"device_state\":\"%s\",\"session_id\":%s,\"firmware_version\":\"%s\"}",
        hexe::config::kEndpointId,
        device_state(),
        g_session_started ? ("\"" + g_session_id + "\"").c_str() : "null",
        hexe::config::kEndpointFirmwareVersion);

    esp_http_client_config_t config = {};
    config.url = url.c_str();
    config.method = HTTP_METHOD_POST;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
      ESP_LOGW(kTag, "Failed to initialize heartbeat HTTP client");
      vTaskDelay(pdMS_TO_TICKS(hexe::config::kEndpointHeartbeatIntervalMs));
      continue;
    }

    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body, std::strlen(body));
    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
      ESP_LOGW(kTag, "Endpoint heartbeat failed: %s", esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);
    vTaskDelay(pdMS_TO_TICKS(hexe::config::kEndpointHeartbeatIntervalMs));
  }
}

void websocket_task(void *arg) {
  (void)arg;
  const std::string uri = websocket_url();
  esp_websocket_client_config_t config = {};
  config.uri = uri.c_str();
  config.reconnect_timeout_ms = hexe::config::kEndpointReconnectBackoffMs;
  g_ws_client = esp_websocket_client_init(&config);
  if (g_ws_client == nullptr) {
    ESP_LOGE(kTag, "Failed to initialize voice WebSocket client");
    vTaskDelete(nullptr);
    return;
  }
  esp_websocket_register_events(g_ws_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, nullptr);
  esp_websocket_client_start(g_ws_client);

  AudioFrame frame = {};
  while (true) {
    if (xQueueReceive(g_audio_queue, &frame, pdMS_TO_TICKS(250)) == pdTRUE) {
      send_audio_frame(frame);
    }
  }
}
}  // namespace

namespace hexe::voice {

void init_backend_client() {
  if (g_audio_queue != nullptr) {
    return;
  }

  g_audio_queue = xQueueCreate(kAudioQueueDepth, sizeof(AudioFrame));
  if (g_audio_queue == nullptr) {
    ESP_LOGE(kTag, "Failed to create bounded audio transport queue");
    return;
  }

  xTaskCreate(heartbeat_task, "hexe_backend_hb", kTaskStackBytes, nullptr, kTaskPriority, &g_heartbeat_task);
  xTaskCreate(websocket_task, "hexe_voice_ws", kTaskStackBytes, nullptr, kTaskPriority, &g_ws_task);
  ESP_LOGI(
      kTag,
      "Backend client configured for %s:%d voice path %s",
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointWsPort,
      hexe::config::kEndpointVoiceWsPath);
}

bool submit_audio_frame(const int16_t *samples, size_t sample_count, uint32_t level, bool vad_speaking) {
  if (g_audio_queue == nullptr || samples == nullptr || sample_count == 0) {
    return false;
  }

  AudioFrame frame = {};
  frame.sample_count = std::min(sample_count, frame.samples.size());
  std::copy(samples, samples + frame.sample_count, frame.samples.begin());
  frame.level = level;
  frame.vad_speaking = vad_speaking;

  if (xQueueSend(g_audio_queue, &frame, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping audio frame because transport queue is full");
    return false;
  }
  return true;
}

}  // namespace hexe::voice
