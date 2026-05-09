#include "voice/backend_client.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <string>
#include <ctime>

#include "app_state.h"
#include "board/audio.h"
#include "board/display.h"
#include "board/led_ring.h"
#include "board/storage.h"
#include "board/touch.h"
#include "board/wifi.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_transport_ws.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "psa/crypto.h"
#include "system/clock.h"
#include "system/ota.h"
#include "system/settings.h"
#include "voice/tts_player.h"

namespace {
constexpr char kTag[] = "hexe_backend";
constexpr size_t kAudioQueueDepth = 8;
constexpr int kTaskStackBytes = 6144;
constexpr int kTaskPriority = 4;
constexpr int kMediaTaskStackBytes = 8192;
constexpr int kMediaTaskPriority = 3;
constexpr int kMediaQueueDepth = 2;
constexpr int kMediaHttpTimeoutMs = 30000;
constexpr int kMediaReadIdleRetryDelayMs = 100;
constexpr int kMediaReadMaxIdleRetries = 3;
constexpr size_t kMediaInventoryLimit = 24;
constexpr int kMaxChunkSamples = hexe::config::kEndpointAudioChunkSamples;
constexpr int kWakePredictionChunkSamples = 1280;
constexpr size_t kMaxBackendEventBytes = 8192;
constexpr uint32_t kBackendReadinessPollMs = 500;
constexpr int kClockSyncIntervalMs = 300000;
constexpr int kClockSyncHttpTimeoutMs = 5000;
constexpr size_t kMaxClockSyncBytes = 1024;
constexpr size_t kWakePrerollFrameCount = 15;
constexpr int kVoiceWsSendTimeoutMs = 3000;
constexpr int kVoiceWsSendRetryDelayMs = 50;
constexpr int kVoiceWsSendAttempts = 3;
constexpr char kVoiceEventSchemaVersion[] = "hexevoice.voice.event.v1";

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
TaskHandle_t g_media_task = nullptr;
QueueHandle_t g_media_queue = nullptr;
SemaphoreHandle_t g_ws_send_lock = nullptr;
uint32_t g_chunk_index = 0;
uint32_t g_session_counter = 0;
uint32_t g_sequence = 0;
bool g_session_started = false;
bool g_audio_stream_finished = false;
bool g_ws_connected = false;
bool g_ws_started = false;
bool g_preroll_drained = false;
bool g_media_transfer_active = false;
int64_t g_last_clock_sync_us = 0;
int g_clock_sync_interval_ms = kClockSyncIntervalMs;
std::array<AudioFrame, kWakePrerollFrameCount> g_preroll_frames = {};
size_t g_preroll_index = 0;
size_t g_preroll_count = 0;
std::array<int16_t, kWakePredictionChunkSamples> g_transport_samples = {};
size_t g_transport_sample_count = 0;
std::string g_session_id;
std::string g_ws_rx_buffer;

struct MediaTransferRequest {
  char request_id[96];
  char media_type[16];
  char filename[128];
  char destination[16];
  char download_url[256];
  char content_type[64];
  char sha256[65];
  int size_bytes;
  bool overwrite;
  bool activate;
};

struct MediaTransferActivityGuard {
  MediaTransferActivityGuard() {
    g_media_transfer_active = true;
    hexe::state().media_transfer_active = true;
  }

  ~MediaTransferActivityGuard() {
    g_media_transfer_active = false;
    hexe::state().media_transfer_active = false;
  }
};

struct HttpTextBuffer {
  std::string text;
  size_t max_bytes{0};
  bool overflow{false};
};

std::string base64_audio(const int16_t *samples, size_t sample_count);
bool send_ws_text(const std::string &message);
std::string endpoint_capabilities_json();
bool sync_backend_time(const std::string &url);
void add_media_inventory_files(cJSON *inventory, const char *key, const char *directory, bool &truncated);
bool ensure_session_started(const char *wake_source);
void append_event_header(
    std::string &message,
    const char *event_type,
    const char *session_id,
    uint32_t sequence);
std::string event_timestamp();

void set_audio_streaming(bool streaming) {
  hexe::state().audio_streaming = streaming;
}

bool backend_ready_for_voice() {
  const auto &state = hexe::state();
  return state.wifi_connected && (state.backend_connected || state.voice_ws_connected || g_ws_connected) && !state.ota_active;
}

void mark_voice_socket_disconnected() {
  g_ws_connected = false;
  auto &state = hexe::state();
  state.voice_ws_connected = false;
  g_session_started = false;
  g_audio_stream_finished = false;
  g_preroll_drained = false;
  g_transport_sample_count = 0;
  set_audio_streaming(false);
  if (!state.muted && !state.ota_active) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void remember_preroll_frame(const AudioFrame &frame) {
  g_preroll_frames[g_preroll_index] = frame;
  g_preroll_index = (g_preroll_index + 1) % g_preroll_frames.size();
  if (g_preroll_count < g_preroll_frames.size()) {
    ++g_preroll_count;
  }
}

std::string event_timestamp() {
  return hexe::system::current_utc_timestamp();
}

void append_event_header(
    std::string &message,
    const char *event_type,
    const char *session_id,
    uint32_t sequence) {
  char event_id[128];
  std::snprintf(
      event_id,
      sizeof(event_id),
      "evt_%s_%" PRIu32 "_%llu",
      event_type,
      sequence,
      static_cast<unsigned long long>(esp_timer_get_time()));

  char prefix[512];
  std::snprintf(
      prefix,
      sizeof(prefix),
      "{\"event_type\":\"%s\",\"event_id\":\"%s\",\"schema_version\":\"%s\","
      "\"endpoint_id\":\"%s\",\"direction\":\"endpoint_to_backend\",\"session_id\":",
      event_type,
      event_id,
      kVoiceEventSchemaVersion,
      hexe::config::kEndpointId);
  message.append(prefix);
  if (session_id == nullptr || session_id[0] == '\0') {
    message.append("null");
  } else {
    message.append("\"");
    message.append(session_id);
    message.append("\"");
  }

  char suffix[128];
  const std::string timestamp = event_timestamp();
  std::snprintf(
      suffix,
      sizeof(suffix),
      ",\"sequence\":%" PRIu32 ",\"timestamp\":\"%s\",\"payload\":",
      sequence,
      timestamp.c_str());
  message.append(suffix);
}

std::string audio_chunk_payload(const int16_t *samples, size_t sample_count) {
  const std::string encoded = base64_audio(samples, sample_count);
  if (encoded.empty()) {
    return std::string();
  }

  std::string payload;
  payload.reserve(encoded.size() + 768);
  const uint32_t sequence = g_sequence++;
  append_event_header(payload, "audio.chunk", g_session_id.c_str(), sequence);
  char prefix[512];
  std::snprintf(
      prefix,
      sizeof(prefix),
      "{\"chunk_index\":%" PRIu32 ","
      "\"audio_format\":{\"encoding\":\"%s\",\"sample_rate_hz\":%d,\"channels\":%d},\"payload_base64\":\"",
      g_chunk_index++,
      hexe::config::kEndpointAudioEncoding,
      hexe::config::kEndpointAudioSampleRateHz,
      hexe::config::kEndpointAudioChannels);
  payload.append(prefix);
  payload.append(encoded);
  payload.append("\",\"is_final\":false}}");
  return payload;
}

bool send_transport_chunk(const int16_t *samples, size_t sample_count) {
  if (sample_count == 0) {
    return true;
  }
  const std::string payload = audio_chunk_payload(samples, sample_count);
  if (payload.empty()) {
    return false;
  }
  if (send_ws_text(payload)) {
    set_audio_streaming(true);
    return true;
  }

  ESP_LOGW(kTag, "Failed to send audio chunk to voice WebSocket");
  set_audio_streaming(false);
  return false;
}

bool flush_transport_samples(bool force) {
  if (g_transport_sample_count == 0) {
    return true;
  }
  if (!force && g_transport_sample_count < g_transport_samples.size()) {
    return true;
  }

  const bool sent = send_transport_chunk(g_transport_samples.data(), g_transport_sample_count);
  if (sent) {
    g_transport_sample_count = 0;
  }
  return sent;
}

bool append_transport_frame(const AudioFrame &frame) {
  size_t offset = 0;
  while (offset < frame.sample_count) {
    const size_t available = g_transport_samples.size() - g_transport_sample_count;
    const size_t to_copy = std::min(available, frame.sample_count - offset);
    std::copy(
        frame.samples.begin() + offset,
        frame.samples.begin() + offset + to_copy,
        g_transport_samples.begin() + g_transport_sample_count);
    g_transport_sample_count += to_copy;
    offset += to_copy;

    if (g_transport_sample_count == g_transport_samples.size() && !flush_transport_samples(false)) {
      return false;
    }
  }
  return true;
}

bool drain_preroll_frames() {
  if (g_preroll_drained) {
    return true;
  }

  const size_t first = (g_preroll_index + g_preroll_frames.size() - g_preroll_count) % g_preroll_frames.size();
  for (size_t i = 0; i < g_preroll_count; ++i) {
    const size_t index = (first + i) % g_preroll_frames.size();
    if (!append_transport_frame(g_preroll_frames[index])) {
      return false;
    }
  }
  g_preroll_drained = true;
  return true;
}

const char *scheme_http() {
  return hexe::config::kEndpointUseTls ? "https" : "http";
}

const char *scheme_ws() {
  return hexe::config::kEndpointUseTls ? "wss" : "ws";
}

const char *firmware_version() {
  const esp_app_desc_t *app = esp_app_get_description();
  return app == nullptr ? "unknown" : app->version;
}

const char *normalized_wake_source(const char *wake_source) {
  if (wake_source == nullptr) {
    return "unknown";
  }
  if (std::strcmp(wake_source, "openwakeword") == 0 || std::strcmp(wake_source, "button") == 0 ||
      std::strcmp(wake_source, "manual") == 0) {
    return wake_source;
  }
  return "unknown";
}

const char *device_state() {
  const auto &state = hexe::state();
  switch (state.phase) {
    case hexe::AppPhase::kListening:
      return "listening";
    case hexe::AppPhase::kThinking:
    case hexe::AppPhase::kUpdating:
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

std::string time_url() {
  char buffer[192];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d/api/endpoint/time",
      scheme_http(),
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointHttpPort);
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

const char *payload_request_id(cJSON *payload);
void send_command_ack(const char *request_id, const char *command_type, const char *status, const char *message);
void send_command_error(const char *request_id, const char *command_type, const char *code, const char *message);
const char *command_type_for_event(const char *event_type);
bool is_backend_command_event(const char *event_type);
void acknowledge_command_received(const char *event_type, cJSON *payload);
bool queue_media_transfer(cJSON *payload);

void handle_backend_event_json(const std::string &message) {
  cJSON *root = cJSON_ParseWithLength(message.c_str(), message.size());
  if (root == nullptr) {
    ESP_LOGW(kTag, "Ignoring invalid backend event JSON (%u bytes)", static_cast<unsigned>(message.size()));
    return;
  }

  cJSON *event_type = cJSON_GetObjectItem(root, "event_type");
  const char *type = cJSON_IsString(event_type) ? event_type->valuestring : "";
  cJSON *event_id = cJSON_GetObjectItem(root, "event_id");
  const char *id = cJSON_IsString(event_id) ? event_id->valuestring : "";
  cJSON *schema_version = cJSON_GetObjectItem(root, "schema_version");
  const char *schema = cJSON_IsString(schema_version) ? schema_version->valuestring : "";
  cJSON *timestamp = cJSON_GetObjectItem(root, "timestamp");
  if (type[0] == '\0' || id[0] == '\0' || schema[0] == '\0' || !cJSON_IsString(timestamp)) {
    ESP_LOGW(
        kTag,
        "Ignoring malformed backend event envelope (event_id=%s, schema=%s, type=%s)",
        id[0] == '\0' ? "missing" : id,
        schema[0] == '\0' ? "missing" : schema,
        type[0] == '\0' ? "missing" : type);
    cJSON_Delete(root);
    return;
  }
  if (std::strcmp(schema, kVoiceEventSchemaVersion) != 0) {
    ESP_LOGW(kTag, "Backend event uses unsupported schema_version (event_id=%s, type=%s, schema=%s)", id, type, schema);
    cJSON_Delete(root);
    return;
  }
  cJSON *payload = cJSON_GetObjectItem(root, "payload");
  if (!cJSON_IsObject(payload)) {
    ESP_LOGW(kTag, "Backend event payload is not an object (event_id=%s, type=%s)", id, type);
    cJSON_Delete(root);
    return;
  }
  acknowledge_command_received(type, payload);
  cJSON *snapshot = cJSON_GetObjectItem(payload, "snapshot");
  cJSON *state_item = cJSON_IsObject(snapshot) ? cJSON_GetObjectItem(snapshot, "ux_state") : nullptr;
  const char *ux_state = cJSON_IsString(state_item) ? state_item->valuestring : "";

  auto &app_state = hexe::state();
  const bool wake_accepted = std::strcmp(type, "wake.accepted") == 0;
  if (wake_accepted) {
    cJSON *session_id = cJSON_GetObjectItem(root, "session_id");
    cJSON *wake = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "wake") : nullptr;
    cJSON *confidence = cJSON_IsObject(wake) ? cJSON_GetObjectItem(wake, "confidence") : nullptr;
    cJSON *model = cJSON_IsObject(wake) ? cJSON_GetObjectItem(wake, "model") : nullptr;
    if (cJSON_IsNumber(confidence)) {
      ESP_LOGI(
          kTag,
          "Wake accepted by backend (session=%s, model=%s, confidence=%.3f)",
          cJSON_IsString(session_id) ? session_id->valuestring : "unknown",
          cJSON_IsString(model) ? model->valuestring : "unknown",
          confidence->valuedouble);
    } else {
      ESP_LOGI(
          kTag,
          "Wake accepted by backend (session=%s, model=%s)",
          cJSON_IsString(session_id) ? session_id->valuestring : "unknown",
          cJSON_IsString(model) ? model->valuestring : "unknown");
    }
  }

  if (wake_accepted || std::strcmp(ux_state, "listening") == 0) {
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kListening;
    }
  } else if (std::strcmp(ux_state, "thinking") == 0) {
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kThinking;
    }
  } else if (std::strcmp(type, "session.state") == 0) {
    if (!app_state.muted) {
      if (std::strcmp(ux_state, "replying") == 0 || std::strcmp(ux_state, "speaking") == 0) {
        app_state.phase = hexe::AppPhase::kReplying;
      } else if (std::strcmp(ux_state, "error") == 0) {
        app_state.phase = hexe::AppPhase::kError;
      } else {
        app_state.phase = hexe::idle_or_connecting_phase();
      }
    }
  } else if (std::strcmp(type, "tts.ready") == 0) {
    cJSON *stream_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stream_id") : nullptr;
    cJSON *content_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "content_type") : nullptr;
    cJSON *audio_url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "audio_url") : nullptr;
    hexe::voice::handle_tts_ready(
        cJSON_IsString(stream_id) ? stream_id->valuestring : nullptr,
        cJSON_IsString(content_type) ? content_type->valuestring : nullptr,
        cJSON_IsString(audio_url) ? audio_url->valuestring : nullptr);
  } else if (std::strcmp(type, "ota.update") == 0) {
    cJSON *url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "url") : nullptr;
    cJSON *version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "version") : nullptr;
    cJSON *sha256 = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "sha256") : nullptr;
    cJSON *size_bytes = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "size_bytes") : nullptr;
    if (hexe::system::start_ota_update(
            cJSON_IsString(url) ? url->valuestring : nullptr,
            cJSON_IsString(version) ? version->valuestring : nullptr,
            cJSON_IsString(sha256) ? sha256->valuestring : nullptr,
            cJSON_IsNumber(size_bytes) ? size_bytes->valueint : 0)) {
      app_state.phase = hexe::AppPhase::kUpdating;
    }
  } else if (std::strcmp(type, "endpoint.volume") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *volume_percent = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "volume_percent") : nullptr;
    if (cJSON_IsNumber(volume_percent)) {
      hexe::voice::set_output_volume(volume_percent->valueint);
      send_command_ack(request_id, "endpoint.volume.set", "succeeded", "Volume updated");
    } else {
      ESP_LOGW(kTag, "Ignoring volume command without numeric volume_percent");
      send_command_error(request_id, "endpoint.volume.set", "invalid_payload", "volume_percent must be numeric");
    }
  } else if (std::strcmp(type, "endpoint.mute") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *muted = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "muted") : nullptr;
    if (cJSON_IsBool(muted)) {
      hexe::system::set_muted(cJSON_IsTrue(muted));
      if (app_state.muted) {
        hexe::voice::stop_tts_playback();
        hexe::voice::cancel_active_session("backend_mute_command");
      }
      app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
      send_command_ack(request_id, "endpoint.mute", "succeeded", app_state.muted ? "Muted" : "Unmuted");
    } else {
      send_command_error(request_id, "endpoint.mute", "invalid_payload", "muted must be boolean");
    }
  } else if (std::strcmp(type, "endpoint.cancel") == 0) {
    const char *request_id = payload_request_id(payload);
    hexe::voice::cancel_active_session("backend_cancel_command");
    app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
    send_command_ack(request_id, "endpoint.cancel", "succeeded", "Active session cancelled");
  } else if (std::strcmp(type, "endpoint.replay") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *stream_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stream_id") : nullptr;
    cJSON *content_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "content_type") : nullptr;
    cJSON *audio_url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "audio_url") : nullptr;
    if (cJSON_IsString(stream_id) || cJSON_IsString(audio_url)) {
      hexe::voice::handle_tts_ready(
          cJSON_IsString(stream_id) ? stream_id->valuestring : nullptr,
          cJSON_IsString(content_type) ? content_type->valuestring : nullptr,
          cJSON_IsString(audio_url) ? audio_url->valuestring : nullptr);
      send_command_ack(request_id, "endpoint.replay", "succeeded", "Replay queued");
    } else {
      send_command_error(request_id, "endpoint.replay", "invalid_payload", "Replay requires stream_id or audio_url");
    }
  } else if (std::strcmp(type, "endpoint.media.transfer") == 0) {
    queue_media_transfer(payload);
  } else if (std::strcmp(type, "endpoint.storage.reformat") == 0) {
    const char *request_id = payload_request_id(payload);
    send_command_ack(request_id, "endpoint.storage.reformat", "started", "Reformatting SD media folders");
    if (hexe::board::reformat_sd_media()) {
      send_command_ack(request_id, "endpoint.storage.reformat", "succeeded", "SD media folders recreated");
    } else {
      send_command_error(request_id, "endpoint.storage.reformat", "sd_reformat_failed", "Could not reformat SD media folders");
    }
  } else if (std::strcmp(type, "endpoint.led.simulate") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *pattern = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "pattern") : nullptr;
    cJSON *duration_ms = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "duration_ms") : nullptr;
    const char *pattern_name = cJSON_IsString(pattern) ? pattern->valuestring : "all";
    const int pattern_duration_ms = cJSON_IsNumber(duration_ms) ? duration_ms->valueint : 1200;
    if (hexe::board::led_ring_simulate_pattern(pattern_name, pattern_duration_ms)) {
      send_command_ack(request_id, "endpoint.led.simulate", "succeeded", "LED simulation started");
    } else {
      send_command_error(request_id, "endpoint.led.simulate", "invalid_payload", "Unknown LED simulation pattern");
    }
  } else if (std::strcmp(type, "session.completed") == 0 || std::strcmp(type, "session.cancelled") == 0) {
    if (std::strcmp(type, "session.cancelled") == 0) {
      hexe::board::led_ring_show_cancelled();
    } else {
      hexe::board::led_ring_show_completed();
    }
    g_session_started = false;
    g_audio_stream_finished = false;
    g_preroll_drained = false;
    g_transport_sample_count = 0;
    set_audio_streaming(false);
    if (!app_state.muted && !hexe::voice::tts_playback_active()) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
  } else if (std::strcmp(type, "session.error") == 0) {
    cJSON *recoverable = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "recoverable") : nullptr;
    g_session_started = false;
    g_audio_stream_finished = false;
    g_preroll_drained = false;
    g_transport_sample_count = 0;
    set_audio_streaming(false);
    if (cJSON_IsBool(recoverable) && cJSON_IsTrue(recoverable)) {
      if (!app_state.muted) {
        app_state.phase = hexe::idle_or_connecting_phase();
      }
    } else {
      app_state.phase = hexe::AppPhase::kError;
    }
  } else {
    ESP_LOGW(kTag, "Unhandled backend event type (event_id=%s, schema=%s, type=%s)", id, schema, type);
    if (std::strncmp(type, "endpoint.", 9) == 0) {
      send_command_error(payload_request_id(payload), type, "unsupported_command", "Endpoint command is not supported");
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
    auto &state = hexe::state();
    state.voice_ws_connected = true;
    g_session_started = false;
    g_audio_stream_finished = false;
    g_preroll_drained = false;
    g_transport_sample_count = 0;
    set_audio_streaming(false);
    g_ws_rx_buffer.clear();
    if (!state.muted && !state.ota_active) {
      state.phase = hexe::idle_or_connecting_phase();
    }
    ESP_LOGI(kTag, "Voice WebSocket connected");
  } else if (event_id == WEBSOCKET_EVENT_DISCONNECTED) {
    g_ws_started = false;
    mark_voice_socket_disconnected();
    g_ws_rx_buffer.clear();
    ESP_LOGW(kTag, "Voice WebSocket disconnected");
  } else if (event_id == WEBSOCKET_EVENT_ERROR) {
    g_ws_started = false;
    mark_voice_socket_disconnected();
    ESP_LOGW(kTag, "Voice WebSocket error");
  } else if (event_id == WEBSOCKET_EVENT_DATA) {
    handle_websocket_data(static_cast<esp_websocket_event_data_t *>(event_data));
  }
}

bool send_ws_text(const std::string &message) {
  if (g_ws_send_lock != nullptr && xSemaphoreTake(g_ws_send_lock, pdMS_TO_TICKS(2000)) != pdTRUE) {
    ESP_LOGW(kTag, "Voice WebSocket send lock timed out");
    return false;
  }
  bool sent = false;
  if (hexe::state().ota_active || g_ws_client == nullptr || !g_ws_connected ||
      !esp_websocket_client_is_connected(g_ws_client)) {
    mark_voice_socket_disconnected();
  } else {
    int written = -1;
    for (int attempt = 1; attempt <= kVoiceWsSendAttempts; ++attempt) {
      written = esp_websocket_client_send_text(
          g_ws_client,
          message.c_str(),
          message.size(),
          pdMS_TO_TICKS(kVoiceWsSendTimeoutMs));
      if (written >= 0) {
        sent = true;
        break;
      }
      if (!g_ws_connected || !esp_websocket_client_is_connected(g_ws_client)) {
        break;
      }
      ESP_LOGD(kTag, "Voice WebSocket send attempt %d failed for %u bytes", attempt, static_cast<unsigned>(message.size()));
      vTaskDelay(pdMS_TO_TICKS(kVoiceWsSendRetryDelayMs));
    }
    if (!sent) {
      ESP_LOGW(kTag, "Voice WebSocket send failed after %d attempts for %u bytes", kVoiceWsSendAttempts, static_cast<unsigned>(message.size()));
      mark_voice_socket_disconnected();
      esp_websocket_client_stop(g_ws_client);
      g_ws_started = false;
    }
  }
  if (g_ws_send_lock != nullptr) {
    xSemaphoreGive(g_ws_send_lock);
  }
  return sent;
}

std::string endpoint_capabilities_json() {
  const auto &state = hexe::state();
  const esp_app_desc_t *app = esp_app_get_description();
  const bool sd_available = hexe::board::sd_card_mounted();
  cJSON *root = cJSON_CreateObject();
  if (root == nullptr) {
    return "{}";
  }

  cJSON *touchscreen = cJSON_AddObjectToObject(root, "touchscreen");
  cJSON_AddBoolToObject(touchscreen, "available", hexe::board::touch_ready());

  cJSON *storage = cJSON_AddObjectToObject(root, "storage");
  cJSON_AddBoolToObject(storage, "sd_card_available", sd_available);
  cJSON_AddStringToObject(storage, "mount_path", hexe::board::sd_card_mount_path());
  cJSON_AddStringToObject(storage, "pictures_path", hexe::board::sd_card_pictures_path());
  cJSON_AddStringToObject(storage, "sprites_path", hexe::board::sd_card_sprites_path());
  cJSON_AddStringToObject(storage, "sounds_path", hexe::board::sd_card_sounds_path());
  cJSON_AddBoolToObject(storage, "media_reformat", sd_available);
  cJSON_AddBoolToObject(storage, "media_transfer_active", state.media_transfer_active);
  cJSON_AddStringToObject(storage, "media_transfer_status", state.media_transfer_active ? "downloading_file" : "idle");
  cJSON *inventory = cJSON_AddObjectToObject(storage, "media_inventory");
  bool inventory_truncated = false;
  add_media_inventory_files(inventory, "pictures", hexe::board::sd_card_pictures_path(), inventory_truncated);
  add_media_inventory_files(inventory, "sprites", hexe::board::sd_card_sprites_path(), inventory_truncated);
  add_media_inventory_files(inventory, "sounds", hexe::board::sd_card_sounds_path(), inventory_truncated);
  cJSON_AddBoolToObject(inventory, "truncated", inventory_truncated);

  cJSON *display = cJSON_AddObjectToObject(root, "display");
  cJSON_AddBoolToObject(display, "available", hexe::board::display_ready());
  cJSON_AddNumberToObject(display, "width", hexe::board::display_width());
  cJSON_AddNumberToObject(display, "height", hexe::board::display_height());
  cJSON_AddStringToObject(display, "pixel_format", hexe::board::display_pixel_format());
  char resolution[24];
  std::snprintf(resolution, sizeof(resolution), "%dx%d", hexe::board::display_width(), hexe::board::display_height());
  cJSON_AddStringToObject(display, "resolution", resolution);

  cJSON *audio = cJSON_AddObjectToObject(root, "audio");
  cJSON *input = cJSON_AddObjectToObject(audio, "input");
  cJSON_AddBoolToObject(input, "available", hexe::board::audio_input_ready());
  cJSON_AddStringToObject(input, "encoding", hexe::config::kEndpointAudioEncoding);
  cJSON_AddNumberToObject(input, "sample_rate_hz", hexe::config::kEndpointAudioSampleRateHz);
  cJSON_AddNumberToObject(input, "channels", hexe::config::kEndpointAudioChannels);
  cJSON *output = cJSON_AddObjectToObject(audio, "output");
  cJSON_AddBoolToObject(output, "available", hexe::board::audio_output_ready());
  cJSON_AddNumberToObject(output, "volume_percent", state.output_volume_percent);
  cJSON_AddBoolToObject(output, "muted", state.muted);

  cJSON *controls = cJSON_AddObjectToObject(root, "controls");
  cJSON_AddBoolToObject(controls, "volume", true);
  cJSON_AddBoolToObject(controls, "mute", true);
  cJSON_AddBoolToObject(controls, "cancel", true);
  cJSON_AddBoolToObject(controls, "replay", true);
  cJSON_AddBoolToObject(controls, "storage_reformat", sd_available);
  cJSON_AddBoolToObject(controls, "restart", false);
  cJSON_AddBoolToObject(controls, "reconnect", false);

  cJSON *firmware = cJSON_AddObjectToObject(root, "firmware");
  cJSON_AddStringToObject(firmware, "project_name", app == nullptr ? "unknown" : app->project_name);
  cJSON_AddStringToObject(firmware, "version", app == nullptr ? firmware_version() : app->version);
  cJSON_AddStringToObject(firmware, "build_date", app == nullptr ? "unknown" : app->date);
  cJSON_AddStringToObject(firmware, "build_time", app == nullptr ? "unknown" : app->time);
  cJSON_AddStringToObject(firmware, "idf_version", app == nullptr ? "unknown" : app->idf_ver);

  char *rendered = cJSON_PrintUnformatted(root);
  std::string result = rendered == nullptr ? "{}" : rendered;
  cJSON_free(rendered);
  cJSON_Delete(root);
  return result;
}

void add_media_inventory_files(cJSON *inventory, const char *key, const char *directory, bool &truncated) {
  cJSON *items = cJSON_AddArrayToObject(inventory, key);
  if (items == nullptr || !hexe::board::sd_card_mounted()) {
    return;
  }

  DIR *dir = opendir(directory);
  if (dir == nullptr) {
    return;
  }

  size_t count = 0;
  while (dirent *entry = readdir(dir)) {
    if (entry->d_name[0] == '.') {
      continue;
    }
    if (count >= kMediaInventoryLimit) {
      truncated = true;
      break;
    }

    char path[384];
    const int written = std::snprintf(path, sizeof(path), "%s/%s", directory, entry->d_name);
    if (written <= 0 || static_cast<size_t>(written) >= sizeof(path)) {
      truncated = true;
      continue;
    }
    struct stat info = {};
    if (stat(path, &info) != 0 || S_ISDIR(info.st_mode)) {
      continue;
    }

    cJSON *item = cJSON_CreateObject();
    if (item == nullptr) {
      truncated = true;
      break;
    }
    cJSON_AddStringToObject(item, "filename", entry->d_name);
    cJSON_AddNumberToObject(item, "size_bytes", static_cast<double>(info.st_size));
    cJSON_AddItemToArray(items, item);
    ++count;
  }
  closedir(dir);
}

const char *payload_request_id(cJSON *payload) {
  cJSON *request_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "request_id") : nullptr;
  return cJSON_IsString(request_id) ? request_id->valuestring : "";
}

const char *command_type_for_event(const char *event_type) {
  if (event_type == nullptr) {
    return "unknown";
  }
  if (std::strcmp(event_type, "endpoint.volume") == 0) {
    return "endpoint.volume.set";
  }
  return event_type;
}

bool is_backend_command_event(const char *event_type) {
  if (event_type == nullptr) {
    return false;
  }
  return std::strcmp(event_type, "ota.update") == 0 || std::strncmp(event_type, "endpoint.", 9) == 0;
}

void acknowledge_command_received(const char *event_type, cJSON *payload) {
  if (!is_backend_command_event(event_type)) {
    return;
  }
  send_command_ack(payload_request_id(payload), command_type_for_event(event_type), "accepted", "OK");
}

void send_command_ack(const char *request_id, const char *command_type, const char *status, const char *message) {
  if (request_id == nullptr || request_id[0] == '\0') {
    return;
  }
  std::string envelope;
  envelope.reserve(512);
  append_event_header(
      envelope,
      "command.ack",
      g_session_started ? g_session_id.c_str() : nullptr,
      g_sequence++);
  char payload[256];
  std::snprintf(
      payload,
      sizeof(payload),
      "{\"request_id\":\"%s\",\"command_type\":\"%s\",\"status\":\"%s\",\"message\":\"%s\"}}",
      request_id,
      command_type == nullptr ? "unknown" : command_type,
      status == nullptr ? "succeeded" : status,
      message == nullptr ? "" : message);
  envelope.append(payload);
  send_ws_text(envelope);
}

void send_command_error(const char *request_id, const char *command_type, const char *code, const char *message) {
  if (request_id == nullptr || request_id[0] == '\0') {
    return;
  }
  std::string envelope;
  envelope.reserve(512);
  append_event_header(
      envelope,
      "command.error",
      g_session_started ? g_session_id.c_str() : nullptr,
      g_sequence++);
  char payload[256];
  std::snprintf(
      payload,
      sizeof(payload),
      "{\"request_id\":\"%s\",\"command_type\":\"%s\",\"code\":\"%s\",\"message\":\"%s\",\"recoverable\":true}}",
      request_id,
      command_type == nullptr ? "unknown" : command_type,
      code == nullptr ? "command_failed" : code,
      message == nullptr ? "Command failed" : message);
  envelope.append(payload);
  send_ws_text(envelope);
}

bool is_safe_media_filename(const char *filename) {
  if (filename == nullptr || filename[0] == '\0' || filename[0] == '.' || std::strlen(filename) >= 120) {
    return false;
  }
  for (const char *cursor = filename; *cursor != '\0'; ++cursor) {
    if (*cursor == '/' || *cursor == '\\' || static_cast<unsigned char>(*cursor) < 32) {
      return false;
    }
    if (*cursor == '.' && cursor[1] == '.') {
      return false;
    }
  }
  return true;
}

const char *media_destination_dir(const char *destination) {
  if (std::strcmp(destination, "picture") == 0) {
    return hexe::board::sd_card_pictures_path();
  }
  if (std::strcmp(destination, "sprite") == 0) {
    return hexe::board::sd_card_sprites_path();
  }
  if (std::strcmp(destination, "sound") == 0) {
    return hexe::board::sd_card_sounds_path();
  }
  return nullptr;
}

void bytes_to_hex(const unsigned char *bytes, size_t byte_count, char *output, size_t output_size) {
  if (output_size < (byte_count * 2) + 1) {
    if (output_size > 0) {
      output[0] = '\0';
    }
    return;
  }
  for (size_t i = 0; i < byte_count; ++i) {
    std::snprintf(output + (i * 2), output_size - (i * 2), "%02x", bytes[i]);
  }
}

bool copy_json_string(cJSON *payload, const char *key, char *target, size_t target_size) {
  cJSON *item = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, key) : nullptr;
  if (!cJSON_IsString(item) || item->valuestring == nullptr || item->valuestring[0] == '\0') {
    return false;
  }
  std::snprintf(target, target_size, "%s", item->valuestring);
  return target[0] != '\0';
}

bool queue_media_transfer(cJSON *payload) {
  MediaTransferRequest request = {};
  if (!copy_json_string(payload, "request_id", request.request_id, sizeof(request.request_id)) ||
      !copy_json_string(payload, "media_type", request.media_type, sizeof(request.media_type)) ||
      !copy_json_string(payload, "filename", request.filename, sizeof(request.filename)) ||
      !copy_json_string(payload, "destination", request.destination, sizeof(request.destination)) ||
      !copy_json_string(payload, "download_url", request.download_url, sizeof(request.download_url)) ||
      !copy_json_string(payload, "sha256", request.sha256, sizeof(request.sha256))) {
    send_command_error(payload_request_id(payload), "endpoint.media.transfer", "invalid_payload", "Media transfer is missing required fields");
    return false;
  }

  cJSON *content_type = cJSON_GetObjectItem(payload, "content_type");
  std::snprintf(
      request.content_type,
      sizeof(request.content_type),
      "%s",
      cJSON_IsString(content_type) ? content_type->valuestring : "application/octet-stream");
  cJSON *size_bytes = cJSON_GetObjectItem(payload, "size_bytes");
  if (!cJSON_IsNumber(size_bytes) || size_bytes->valueint <= 0) {
    send_command_error(request.request_id, "endpoint.media.transfer", "invalid_payload", "size_bytes must be positive");
    return false;
  }
  request.size_bytes = size_bytes->valueint;
  cJSON *rewrite = cJSON_GetObjectItem(payload, "rewrite");
  cJSON *overwrite = cJSON_GetObjectItem(payload, "overwrite");
  cJSON *activate = cJSON_GetObjectItem(payload, "activate");
  request.overwrite = cJSON_IsBool(rewrite) ? cJSON_IsTrue(rewrite) : (!cJSON_IsBool(overwrite) || cJSON_IsTrue(overwrite));
  request.activate = !cJSON_IsBool(activate) || cJSON_IsTrue(activate);
  ESP_LOGI(
      kTag,
      "Received media transfer command request_id=%s destination=%s filename=%s size=%d url=%s",
      request.request_id,
      request.destination,
      request.filename,
      request.size_bytes,
      request.download_url);

  if (!hexe::board::sd_card_mounted()) {
    send_command_error(request.request_id, "endpoint.media.transfer", "sd_card_not_mounted", "SD card is not mounted");
    return false;
  }
  if (!is_safe_media_filename(request.filename) || media_destination_dir(request.destination) == nullptr) {
    send_command_error(request.request_id, "endpoint.media.transfer", "invalid_destination", "Media filename or destination is invalid");
    return false;
  }
  if (g_media_queue == nullptr || xQueueSend(g_media_queue, &request, 0) != pdTRUE) {
    send_command_error(request.request_id, "endpoint.media.transfer", "media_transfer_busy", "Media transfer queue is full");
    return false;
  }

  send_command_ack(request.request_id, "endpoint.media.transfer", "accepted", "Media transfer queued");
  return true;
}

bool write_media_transfer(const MediaTransferRequest &request) {
  MediaTransferActivityGuard media_activity;
  const char *directory = media_destination_dir(request.destination);
  if (directory == nullptr || !is_safe_media_filename(request.filename)) {
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "invalid_destination", "Media destination is invalid");
    return false;
  }

  if (!hexe::board::ensure_sd_media_directories()) {
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "mkdir_failed", "Could not create SD media directories");
    return false;
  }
  if (mkdir(directory, 0775) != 0 && errno != EEXIST) {
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "mkdir_failed", "Could not create media directory");
    return false;
  }

  char final_path[256] = {};
  char temp_path[280] = {};
  const int final_written = std::snprintf(final_path, sizeof(final_path), "%s/%s", directory, request.filename);
  const int temp_written = std::snprintf(temp_path, sizeof(temp_path), "%s/.%s.tmp", directory, request.filename);
  if (final_written < 0 || final_written >= static_cast<int>(sizeof(final_path)) ||
      temp_written < 0 || temp_written >= static_cast<int>(sizeof(temp_path))) {
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "invalid_destination", "Media path is too long");
    return false;
  }
  if (!request.overwrite) {
    struct stat info = {};
    if (stat(final_path, &info) == 0) {
      g_media_transfer_active = false;
      send_command_error(request.request_id, "endpoint.media.transfer", "target_exists", "Media file already exists");
      return false;
    }
  }

  send_command_ack(request.request_id, "endpoint.media.transfer", "started", "Downloading media");
  ESP_LOGI(kTag, "Starting media transfer destination=%s filename=%s url=%s", request.destination, request.filename, request.download_url);

  esp_http_client_config_t config = {};
  config.url = request.download_url;
  config.timeout_ms = kMediaHttpTimeoutMs;
  config.keep_alive_enable = true;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "http_client_failed", "Could not initialize HTTP client");
    return false;
  }

  FILE *file = std::fopen(temp_path, "wb");
  if (file == nullptr) {
    esp_http_client_cleanup(client);
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "file_open_failed", "Could not open temporary media file");
    return false;
  }

  psa_hash_operation_t hash_op = PSA_HASH_OPERATION_INIT;
  psa_status_t hash_status = psa_crypto_init();
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_setup(&hash_op, PSA_ALG_SHA_256);
  }
  esp_err_t err = esp_http_client_open(client, 0);
  int total_read = 0;
  char buffer[1024];
  if (err == ESP_OK) {
    const int content_length = esp_http_client_fetch_headers(client);
    const int status_code = esp_http_client_get_status_code(client);
    if (status_code < 200 || status_code >= 300) {
      ESP_LOGW(kTag, "Media download HTTP %d for %s", status_code, request.download_url);
      err = ESP_FAIL;
    } else if (content_length >= 0 && content_length != request.size_bytes) {
      ESP_LOGW(kTag, "Media download content length mismatch: expected=%d header=%d", request.size_bytes, content_length);
    }
  }
  if (err == ESP_OK) {
    int idle_retries = 0;
    while (true) {
      const int read = esp_http_client_read(client, buffer, sizeof(buffer));
      if (read == -ESP_ERR_HTTP_EAGAIN) {
        if (idle_retries++ < kMediaReadMaxIdleRetries) {
          ESP_LOGW(kTag, "Media download stalled waiting for data; retry %d/%d", idle_retries, kMediaReadMaxIdleRetries);
          vTaskDelay(pdMS_TO_TICKS(kMediaReadIdleRetryDelayMs));
          continue;
        }
        err = ESP_ERR_HTTP_EAGAIN;
        break;
      }
      if (read < 0) {
        err = ESP_FAIL;
        break;
      }
      idle_retries = 0;
      if (read == 0) {
        break;
      }
      if (std::fwrite(buffer, 1, read, file) != static_cast<size_t>(read)) {
        err = ESP_ERR_NO_MEM;
        break;
      }
      if (hash_status == PSA_SUCCESS) {
        hash_status = psa_hash_update(&hash_op, reinterpret_cast<const uint8_t *>(buffer), read);
      }
      total_read += read;
    }
  }

  std::fclose(file);
  esp_http_client_close(client);
  esp_http_client_cleanup(client);

  unsigned char digest[32] = {};
  size_t digest_length = 0;
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_finish(&hash_op, digest, sizeof(digest), &digest_length);
  } else {
    psa_hash_abort(&hash_op);
  }

  if (err != ESP_OK) {
    std::remove(temp_path);
    ESP_LOGW(kTag, "Media transfer download failed filename=%s err=%s bytes=%d", request.filename, esp_err_to_name(err), total_read);
    g_media_transfer_active = false;
    send_command_error(
        request.request_id,
        "endpoint.media.transfer",
        err == ESP_ERR_HTTP_EAGAIN ? "download_timeout" : "download_failed",
        err == ESP_ERR_HTTP_EAGAIN ? "Media download timed out waiting for data" : "Media download failed");
    return false;
  }
  if (total_read != request.size_bytes) {
    std::remove(temp_path);
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "size_mismatch", "Media size did not match manifest");
    return false;
  }
  if (hash_status != PSA_SUCCESS || digest_length != sizeof(digest)) {
    std::remove(temp_path);
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "checksum_failed", "Could not calculate media checksum");
    return false;
  }

  char sha_hex[65] = {};
  bytes_to_hex(digest, sizeof(digest), sha_hex, sizeof(sha_hex));
  if (std::strcmp(sha_hex, request.sha256) != 0) {
    std::remove(temp_path);
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "checksum_mismatch", "Media checksum did not match manifest");
    return false;
  }

  if (request.overwrite) {
    std::remove(final_path);
  }
  if (std::rename(temp_path, final_path) != 0) {
    std::remove(temp_path);
    g_media_transfer_active = false;
    send_command_error(request.request_id, "endpoint.media.transfer", "rename_failed", "Could not activate media file");
    return false;
  }

  ESP_LOGI(kTag, "Stored media transfer destination=%s filename=%s bytes=%d", request.destination, request.filename, total_read);
  g_media_transfer_active = false;
  send_command_ack(request.request_id, "endpoint.media.transfer", "succeeded", "Media stored on SD card");
  if (request.activate && (std::strcmp(request.destination, "picture") == 0 || std::strcmp(request.destination, "sprite") == 0)) {
    hexe::board::request_display_assets_reload();
    ESP_LOGI(kTag, "Queued display asset reload after media transfer destination=%s filename=%s", request.destination, request.filename);
  }
  if (request.activate && std::strcmp(request.destination, "sound") == 0) {
    hexe::voice::play_sd_sound(request.filename);
  }
  return true;
}

void media_transfer_task(void *arg) {
  (void)arg;
  MediaTransferRequest request = {};
  while (true) {
    if (xQueueReceive(g_media_queue, &request, portMAX_DELAY) == pdTRUE) {
      write_media_transfer(request);
    }
  }
}

bool ensure_session_started(const char *wake_source) {
  if (g_session_started) {
    return true;
  }
  if (!g_ws_connected || !backend_ready_for_voice()) {
    return false;
  }
  ++g_session_counter;
  g_chunk_index = 0;
  g_audio_stream_finished = false;
  g_preroll_drained = false;
  g_transport_sample_count = 0;
  char session_buffer[96];
  std::snprintf(
      session_buffer,
      sizeof(session_buffer),
      "%s-%" PRIu32,
      hexe::config::kEndpointId,
      g_session_counter);
  g_session_id = session_buffer;

  std::string payload;
  payload.reserve(768);
  append_event_header(payload, "session.start", g_session_id.c_str(), g_sequence++);
  char body[512];
  std::snprintf(
      body,
      sizeof(body),
      "{\"firmware_version\":\"%s\","
      "\"wake_source\":\"%s\",\"audio_format\":{\"encoding\":\"%s\",\"sample_rate_hz\":%d,\"channels\":%d}}}",
      firmware_version(),
      normalized_wake_source(wake_source),
      hexe::config::kEndpointAudioEncoding,
      hexe::config::kEndpointAudioSampleRateHz,
      hexe::config::kEndpointAudioChannels);
  payload.append(body);

  g_session_started = send_ws_text(payload);
  if (g_session_started) {
    set_audio_streaming(true);
    ESP_LOGI(kTag, "Started voice session %s wake_source=%s", g_session_id.c_str(), normalized_wake_source(wake_source));
  }
  return g_session_started;
}

void send_audio_frame(const AudioFrame &frame) {
  if (!g_session_started && !frame.vad_speaking) {
    remember_preroll_frame(frame);
    return;
  }
  ensure_session_started("openwakeword");
  if (g_audio_stream_finished) {
    return;
  }
  if (!g_session_started) {
    return;
  }

  if (!drain_preroll_frames()) {
    return;
  }
  if (!append_transport_frame(frame)) {
    return;
  }
}

esp_err_t text_http_event_handler(esp_http_client_event_t *event) {
  if (event == nullptr || event->user_data == nullptr || event->event_id != HTTP_EVENT_ON_DATA) {
    return ESP_OK;
  }
  auto *buffer = static_cast<HttpTextBuffer *>(event->user_data);
  if (event->data == nullptr || event->data_len <= 0 || buffer->overflow) {
    return ESP_OK;
  }
  if (buffer->text.size() + static_cast<size_t>(event->data_len) > buffer->max_bytes) {
    buffer->overflow = true;
    return ESP_OK;
  }
  buffer->text.append(static_cast<const char *>(event->data), static_cast<size_t>(event->data_len));
  return ESP_OK;
}

bool parse_clock_sync_payload(const std::string &payload, int64_t *server_unix_ms, int32_t *utc_offset_seconds, int *sync_interval_ms) {
  if (server_unix_ms == nullptr || utc_offset_seconds == nullptr || sync_interval_ms == nullptr || payload.empty()) {
    return false;
  }

  cJSON *root = cJSON_Parse(payload.c_str());
  if (root == nullptr) {
    return false;
  }

  cJSON *server_unix_ms_item = cJSON_GetObjectItem(root, "server_unix_ms");
  cJSON *utc_offset_item = cJSON_GetObjectItem(root, "utc_offset_seconds");
  cJSON *sync_interval_item = cJSON_GetObjectItem(root, "sync_interval_ms");
  const bool ok = cJSON_IsNumber(server_unix_ms_item) && cJSON_IsNumber(utc_offset_item);
  if (ok) {
    *server_unix_ms = static_cast<int64_t>(server_unix_ms_item->valuedouble);
    *utc_offset_seconds = static_cast<int32_t>(utc_offset_item->valueint);
    if (cJSON_IsNumber(sync_interval_item) && sync_interval_item->valueint > 0) {
      *sync_interval_ms = sync_interval_item->valueint;
    }
  }

  cJSON_Delete(root);
  return ok;
}

bool sync_backend_time(const std::string &url) {
  HttpTextBuffer response;
  response.max_bytes = kMaxClockSyncBytes;
  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_GET;
  config.timeout_ms = kClockSyncHttpTimeoutMs;
  config.event_handler = text_http_event_handler;
  config.user_data = &response;

  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    ESP_LOGW(kTag, "Failed to initialize clock sync HTTP client");
    return false;
  }

  const int64_t started_us = esp_timer_get_time();
  esp_err_t err = esp_http_client_perform(client);
  const int64_t round_trip_us = esp_timer_get_time() - started_us;
  const int status_code = esp_http_client_get_status_code(client);
  esp_http_client_cleanup(client);

  if (err != ESP_OK || status_code < 200 || status_code >= 300 || response.overflow || response.text.empty()) {
    ESP_LOGW(
        kTag,
        "Clock sync failed: err=%s status=%d overflow=%d",
        esp_err_to_name(err),
        status_code,
        response.overflow);
    return false;
  }

  int64_t server_unix_ms = 0;
  int32_t utc_offset_seconds = 0;
  int sync_interval_ms = g_clock_sync_interval_ms;
  if (!parse_clock_sync_payload(response.text, &server_unix_ms, &utc_offset_seconds, &sync_interval_ms)) {
    ESP_LOGW(kTag, "Clock sync failed: invalid time payload");
    return false;
  }

  hexe::system::sync_clock_from_server(server_unix_ms, utc_offset_seconds, round_trip_us);
  g_last_clock_sync_us = esp_timer_get_time();
  g_clock_sync_interval_ms = std::max(1000, sync_interval_ms);
  return true;
}

void heartbeat_task(void *arg) {
  (void)arg;
  const std::string url = heartbeat_url();
  const std::string clock_url = time_url();

  while (true) {
    if (!hexe::state().wifi_connected) {
      auto &state = hexe::state();
      state.backend_connected = false;
      state.voice_ws_connected = false;
      if (!state.muted && !state.ota_active) {
        state.phase = hexe::idle_or_connecting_phase();
      }
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }
    if (hexe::state().media_transfer_active) {
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

    std::string session_json = "null";
    if (g_session_started) {
      session_json = "\"" + g_session_id + "\"";
    }
    const std::string capabilities = endpoint_capabilities_json();
    std::string body;
    body.reserve(capabilities.size() + 256);
    body.append("{\"endpoint_id\":\"");
    body.append(hexe::config::kEndpointId);
    body.append("\",\"device_state\":\"");
    body.append(device_state());
    body.append("\",\"session_id\":");
    body.append(session_json);
    body.append(",\"firmware_version\":\"");
    body.append(firmware_version());
    body.append("\",\"ip_address\":\"");
    body.append(hexe::board::current_ip_address());
    char rssi_field[32];
    std::snprintf(rssi_field, sizeof(rssi_field), "\",\"rssi_dbm\":%d", hexe::state().wifi_rssi);
    body.append(rssi_field);
    body.append(",\"capabilities\":");
    body.append(capabilities);
    body.append("}");

    esp_http_client_config_t config = {};
    config.url = url.c_str();
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = 10000;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
      ESP_LOGW(kTag, "Failed to initialize heartbeat HTTP client");
      vTaskDelay(pdMS_TO_TICKS(hexe::config::kEndpointHeartbeatIntervalMs));
      continue;
    }

    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, body.c_str(), static_cast<int>(body.size()));
    esp_err_t err = esp_http_client_perform(client);
    const int status_code = esp_http_client_get_status_code(client);
    bool clock_sync_due = false;
    auto &state = hexe::state();
    if (err == ESP_OK && status_code >= 200 && status_code < 300) {
      const bool was_backend_connected = state.backend_connected;
      state.backend_connected = true;
      if (!state.muted && !state.ota_active && !state.voice_ws_connected) {
        state.phase = hexe::AppPhase::kBackendConnecting;
      }
      const int64_t now_us = esp_timer_get_time();
      clock_sync_due =
          g_last_clock_sync_us == 0 ||
          !was_backend_connected ||
          (now_us - g_last_clock_sync_us) >= (static_cast<int64_t>(g_clock_sync_interval_ms) * 1000);
    } else {
      state.backend_connected = false;
      if (!g_ws_connected) {
        state.voice_ws_connected = false;
      }
      if (!state.muted && !state.ota_active && !g_ws_connected) {
        state.phase = hexe::idle_or_connecting_phase();
      }
      if (err != ESP_OK) {
        ESP_LOGW(kTag, "Endpoint heartbeat failed: %s", esp_err_to_name(err));
      } else {
        ESP_LOGW(kTag, "Endpoint heartbeat failed: HTTP %d", status_code);
      }
    }
    esp_http_client_cleanup(client);
    if (clock_sync_due) {
      sync_backend_time(clock_url);
    }
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
    if (hexe::state().ota_active) {
      if (g_ws_started) {
        ESP_LOGI(kTag, "Stopping voice WebSocket while OTA update is active");
        esp_websocket_client_stop(g_ws_client);
        g_ws_started = false;
      }
      mark_voice_socket_disconnected();
      xQueueReset(g_audio_queue);
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

    if (!backend_ready_for_voice()) {
      if (g_ws_started) {
        esp_websocket_client_stop(g_ws_client);
        g_ws_started = false;
      }
      mark_voice_socket_disconnected();
      xQueueReset(g_audio_queue);
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

    if (g_ws_started && g_ws_connected && !esp_websocket_client_is_connected(g_ws_client)) {
      ESP_LOGW(kTag, "Voice WebSocket transport is stale, reconnecting");
      mark_voice_socket_disconnected();
      esp_websocket_client_stop(g_ws_client);
      g_ws_started = false;
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
  g_media_queue = xQueueCreate(kMediaQueueDepth, sizeof(MediaTransferRequest));
  if (g_media_queue == nullptr) {
    ESP_LOGE(kTag, "Failed to create media transfer queue");
    return;
  }
  g_ws_send_lock = xSemaphoreCreateMutex();
  if (g_ws_send_lock == nullptr) {
    ESP_LOGE(kTag, "Failed to create WebSocket send lock");
    return;
  }

  xTaskCreate(media_transfer_task, "hexe_media_xfer", kMediaTaskStackBytes, nullptr, kMediaTaskPriority, &g_media_task);
  xTaskCreate(heartbeat_task, "hexe_backend_hb", kTaskStackBytes, nullptr, kTaskPriority, &g_heartbeat_task);
  xTaskCreate(websocket_task, "hexe_voice_ws", kTaskStackBytes, nullptr, kTaskPriority, &g_ws_task);
  ESP_LOGI(
      kTag,
      "Backend client configured for %s:%d voice path %s",
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointWsPort,
      hexe::config::kEndpointVoiceWsPath);
}

bool send_tts_playback_event(
    const char *event_type,
    const char *stream_id,
    const char *audio_url,
    const char *reason,
    size_t byte_count) {
  if (event_type == nullptr || event_type[0] == '\0' || !g_ws_connected) {
    return false;
  }

  std::string envelope;
  envelope.reserve(640);
  append_event_header(
      envelope,
      event_type,
      g_session_started ? g_session_id.c_str() : nullptr,
      g_sequence++);
  char body[384];
  std::snprintf(
      body,
      sizeof(body),
      "{\"stream_id\":\"%s\",\"audio_url\":\"%s\",\"byte_count\":%zu",
      stream_id == nullptr ? "" : stream_id,
      audio_url == nullptr ? "" : audio_url,
      byte_count);
  envelope.append(body);
  if (reason != nullptr && reason[0] != '\0') {
    char failure[192];
    std::snprintf(
        failure,
        sizeof(failure),
        ",\"reason\":\"%s\",\"message\":\"%s\"",
        reason,
        reason);
    envelope.append(failure);
  }
  envelope.append("}}");
  return send_ws_text(envelope);
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

bool start_voice_session(const char *wake_source) {
  auto &app_state = hexe::state();
  if (app_state.muted || app_state.ota_active) {
    return false;
  }
  if (!backend_ready_for_voice() || !g_ws_connected) {
    app_state.phase = hexe::idle_or_connecting_phase();
    return false;
  }

  const bool started = ensure_session_started(wake_source);
  if (started) {
    app_state.phase = hexe::AppPhase::kListening;
  }
  return started;
}

bool finish_audio_stream(const char *reason) {
  if (hexe::state().ota_active || !g_session_started || g_audio_stream_finished) {
    return false;
  }
  if (!flush_transport_samples(true)) {
    return false;
  }
  std::string payload;
  payload.reserve(384);
  append_event_header(payload, "audio.end", g_session_id.c_str(), g_sequence++);
  char body[128];
  std::snprintf(
      body,
      sizeof(body),
      "{\"reason\":\"%s\"}}",
      reason == nullptr ? "audio_end" : reason);
  payload.append(body);
  g_audio_stream_finished = send_ws_text(payload);
  if (g_audio_stream_finished) {
    set_audio_streaming(false);
    hexe::state().phase = hexe::AppPhase::kThinking;
  }
  return g_audio_stream_finished;
}

bool cancel_active_session(const char *reason) {
  if (hexe::state().ota_active || !g_session_started) {
    return false;
  }
  std::string payload;
  payload.reserve(384);
  append_event_header(payload, "session.cancel", g_session_id.c_str(), g_sequence++);
  char body[128];
  std::snprintf(
      body,
      sizeof(body),
      "{\"reason\":\"%s\"}}",
      reason == nullptr ? "endpoint_cancelled" : reason);
  payload.append(body);
  const bool sent = send_ws_text(payload);
  g_session_started = false;
  g_audio_stream_finished = false;
  g_preroll_drained = false;
  g_transport_sample_count = 0;
  set_audio_streaming(false);
  hexe::voice::stop_tts_playback();
  hexe::board::led_ring_show_cancelled();
  return sent;
}

}  // namespace hexe::voice
