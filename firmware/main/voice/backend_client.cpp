#include "voice/backend_client.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <string>

#include "app_state.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_transport_ws.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_backend";
constexpr size_t kAudioQueueDepth = 8;
constexpr int kTaskStackBytes = 6144;
constexpr int kTaskPriority = 4;
constexpr int kMaxChunkSamples = hexe::config::kEndpointAudioChunkSamples;
constexpr size_t kMaxBackendEventBytes = 8192;
constexpr uint32_t kBackendReadinessPollMs = 500;

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
bool g_audio_stream_finished = false;
bool g_ws_connected = false;
bool g_ws_started = false;
std::string g_session_id;
std::string g_ws_rx_buffer;

void set_audio_streaming(bool streaming) {
  hexe::state().audio_streaming = streaming;
}

bool backend_ready_for_voice() {
  const auto &state = hexe::state();
  return state.wifi_connected && state.backend_connected;
}

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
    case hexe::AppPhase::kReplying:
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

void handle_backend_event_json(const std::string &message) {
  cJSON *root = cJSON_ParseWithLength(message.c_str(), message.size());
  if (root == nullptr) {
    ESP_LOGW(kTag, "Ignoring invalid backend event JSON (%u bytes)", static_cast<unsigned>(message.size()));
    return;
  }

  cJSON *event_type = cJSON_GetObjectItem(root, "event_type");
  const char *type = cJSON_IsString(event_type) ? event_type->valuestring : "";
  cJSON *payload = cJSON_GetObjectItem(root, "payload");
  cJSON *snapshot = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "snapshot") : nullptr;
  cJSON *state_item = cJSON_IsObject(snapshot) ? cJSON_GetObjectItem(snapshot, "ux_state") : nullptr;
  const char *ux_state = cJSON_IsString(state_item) ? state_item->valuestring : "";

  auto &app_state = hexe::state();
  if (std::strcmp(type, "wake.accepted") == 0 || std::strcmp(ux_state, "listening") == 0) {
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kListening;
    }
  } else if (
      std::strcmp(type, "transcript.final") == 0 || std::strcmp(type, "response.text") == 0 ||
      std::strcmp(ux_state, "thinking") == 0) {
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kThinking;
    }
  } else if (std::strcmp(type, "tts.ready") == 0) {
    cJSON *stream_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stream_id") : nullptr;
    cJSON *content_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "content_type") : nullptr;
    cJSON *audio_url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "audio_url") : nullptr;
    hexe::voice::handle_tts_ready(
        cJSON_IsString(stream_id) ? stream_id->valuestring : nullptr,
        cJSON_IsString(content_type) ? content_type->valuestring : nullptr,
        cJSON_IsString(audio_url) ? audio_url->valuestring : nullptr);
  } else if (std::strcmp(type, "session.completed") == 0 || std::strcmp(type, "session.cancelled") == 0) {
    g_session_started = false;
    g_audio_stream_finished = false;
    set_audio_streaming(false);
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kIdle;
    }
  } else if (std::strcmp(type, "session.error") == 0) {
    cJSON *recoverable = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "recoverable") : nullptr;
    g_session_started = false;
    g_audio_stream_finished = false;
    set_audio_streaming(false);
    if (cJSON_IsBool(recoverable) && cJSON_IsTrue(recoverable)) {
      if (!app_state.muted) {
        app_state.phase = hexe::AppPhase::kIdle;
      }
    } else {
      app_state.phase = hexe::AppPhase::kError;
    }
  }

  cJSON_Delete(root);
}

void handle_websocket_data(const esp_websocket_event_data_t *data) {
  if (data == nullptr || data->data_ptr == nullptr || data->data_len <= 0) {
    return;
  }
  if (data->op_code != WS_TRANSPORT_OPCODES_TEXT && data->op_code != WS_TRANSPORT_OPCODES_CONT) {
    g_ws_rx_buffer.clear();
    return;
  }
  if (data->payload_len <= 0 || data->payload_len > static_cast<int>(kMaxBackendEventBytes)) {
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Dropping oversized backend event (%d bytes)", data->payload_len);
    return;
  }
  if (data->payload_offset == 0) {
    g_ws_rx_buffer.clear();
    g_ws_rx_buffer.reserve(data->payload_len);
  }
  if (data->payload_offset != static_cast<int>(g_ws_rx_buffer.size())) {
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Dropping out-of-order backend event chunk");
    return;
  }

  g_ws_rx_buffer.append(data->data_ptr, data->data_len);
  const int received = data->payload_offset + data->data_len;
  if (received < data->payload_len) {
    return;
  }

  handle_backend_event_json(g_ws_rx_buffer);
  g_ws_rx_buffer.clear();
}

void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
  (void)handler_args;
  (void)base;
  if (event_id == WEBSOCKET_EVENT_CONNECTED) {
    g_ws_connected = true;
    g_session_started = false;
    g_audio_stream_finished = false;
    set_audio_streaming(false);
    g_ws_rx_buffer.clear();
    ESP_LOGI(kTag, "Voice WebSocket connected");
  } else if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
    g_ws_connected = false;
    g_session_started = false;
    g_audio_stream_finished = false;
    set_audio_streaming(false);
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Voice WebSocket disconnected");
  } else if (event_id == WEBSOCKET_EVENT_ERROR) {
    g_ws_connected = false;
    set_audio_streaming(false);
    ESP_LOGW(kTag, "Voice WebSocket error");
  } else if (event_id == WEBSOCKET_EVENT_DATA) {
    handle_websocket_data(static_cast<esp_websocket_event_data_t *>(event_data));
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
  if (g_session_started || !g_ws_connected || !backend_ready_for_voice()) {
    return;
  }
  ++g_session_counter;
  g_chunk_index = 0;
  g_audio_stream_finished = false;
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
      "\"wake_source\":\"openwakeword\",\"audio_format\":{\"encoding\":\"%s\",\"sample_rate_hz\":%d,\"channels\":%d}}}",
      hexe::config::kEndpointId,
      g_session_id.c_str(),
      g_sequence++,
      hexe::config::kEndpointFirmwareVersion,
      hexe::config::kEndpointAudioEncoding,
      hexe::config::kEndpointAudioSampleRateHz,
      hexe::config::kEndpointAudioChannels);

  g_session_started = send_ws_text(payload);
  if (g_session_started) {
    set_audio_streaming(true);
    ESP_LOGI(kTag, "Started voice session %s", g_session_id.c_str());
  }
}

void send_audio_frame(const AudioFrame &frame) {
  if (!g_session_started && !frame.vad_speaking) {
    return;
  }
  ensure_session_started();
  if (g_audio_stream_finished) {
    return;
  }
  if (!g_session_started) {
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

  if (send_ws_text(payload)) {
    set_audio_streaming(true);
  } else {
    ESP_LOGW(kTag, "Failed to send audio chunk to voice WebSocket");
    set_audio_streaming(false);
  }
}

void heartbeat_task(void *arg) {
  (void)arg;
  const std::string url = heartbeat_url();

  while (true) {
    if (!hexe::state().wifi_connected) {
      hexe::state().backend_connected = false;
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

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
    const int status_code = esp_http_client_get_status_code(client);
    if (err == ESP_OK && status_code >= 200 && status_code < 300) {
      hexe::state().backend_connected = true;
    } else {
      hexe::state().backend_connected = false;
      if (err != ESP_OK) {
        ESP_LOGW(kTag, "Endpoint heartbeat failed: %s", esp_err_to_name(err));
      } else {
        ESP_LOGW(kTag, "Endpoint heartbeat failed: HTTP %d", status_code);
      }
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

  AudioFrame frame = {};
  while (true) {
    if (!backend_ready_for_voice()) {
      if (g_ws_started) {
        esp_websocket_client_stop(g_ws_client);
        g_ws_started = false;
      }
      g_ws_connected = false;
      g_session_started = false;
      g_audio_stream_finished = false;
      set_audio_streaming(false);
      xQueueReset(g_audio_queue);
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

    if (!g_ws_started) {
      ESP_LOGI(kTag, "Starting voice WebSocket after Wi-Fi and backend heartbeat are ready");
      esp_websocket_client_start(g_ws_client);
      g_ws_started = true;
    }

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
  if (g_audio_queue == nullptr || samples == nullptr || sample_count == 0 || !backend_ready_for_voice()) {
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

bool finish_audio_stream(const char *reason) {
  if (!g_session_started || g_audio_stream_finished) {
    return false;
  }
  char payload[384];
  std::snprintf(
      payload,
      sizeof(payload),
      "{\"event_type\":\"audio.end\",\"endpoint_id\":\"%s\",\"direction\":\"endpoint_to_backend\","
      "\"session_id\":\"%s\",\"sequence\":%" PRIu32 ",\"payload\":{\"reason\":\"%s\"}}",
      hexe::config::kEndpointId,
      g_session_id.c_str(),
      g_sequence++,
      reason == nullptr ? "audio_end" : reason);
  g_audio_stream_finished = send_ws_text(payload);
  if (g_audio_stream_finished) {
    set_audio_streaming(false);
    hexe::state().phase = hexe::AppPhase::kThinking;
  }
  return g_audio_stream_finished;
}

bool cancel_active_session(const char *reason) {
  if (!g_session_started) {
    return false;
  }
  char payload[384];
  std::snprintf(
      payload,
      sizeof(payload),
      "{\"event_type\":\"session.cancel\",\"endpoint_id\":\"%s\",\"direction\":\"endpoint_to_backend\","
      "\"session_id\":\"%s\",\"sequence\":%" PRIu32 ",\"payload\":{\"reason\":\"%s\"}}",
      hexe::config::kEndpointId,
      g_session_id.c_str(),
      g_sequence++,
      reason == nullptr ? "endpoint_cancelled" : reason);
  const bool sent = send_ws_text(payload);
  g_session_started = false;
  g_audio_stream_finished = false;
  set_audio_streaming(false);
  hexe::voice::stop_tts_playback();
  return sent;
}

}  // namespace hexe::voice
