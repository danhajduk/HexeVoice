#include "voice/backend_client.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <dirent.h>
#include <sys/stat.h>
#include <string>
#include <ctime>
#include <utility>

#include "app_state.h"
#include "board/audio.h"
#include "board/display.h"
#include "board/led_ring.h"
#include "board_profile_pins.h"
#include "board/storage.h"
#include "board/touch.h"
#include "board/wifi.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_app_desc.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "esp_transport_ws.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "mbedtls/base64.h"
#include "psa/crypto.h"
#include "system/clock.h"
#include "system/ble_provisioning.h"
#include "system/ota.h"
#include "system/power.h"
#include "system/settings.h"
#include "system/telemetry.h"
#include "voice/assistant_client.h"
#include "voice/micro_wake_engine.h"
#include "voice/model_bundle.h"
#include "voice/stt_stream.h"
#include "voice/tts_player.h"
#include "voice/wake_word.h"

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
constexpr int kPlacementCalibrationHttpTimeoutMs = 5000;
constexpr size_t kMaxPlacementCalibrationStatusBytes = 8192;
constexpr size_t kWakePrerollFrameCount = 15;
constexpr int kVoiceWsSendTimeoutMs = 3000;
constexpr int kVoiceWsSendRetryDelayMs = 50;
constexpr int kVoiceWsSendAttempts = 3;
constexpr int64_t kPostTtsInputIgnoreUs = 800000;
constexpr int64_t kSessionResetInputIgnoreUs = 2000000;
constexpr int64_t kPreWakeStreamTimeoutUs = 10000000;
constexpr int64_t kAcceptedCaptureTimeoutUs = 15000000;
constexpr const char *kWakeElectionFallbackPolicy = "stream_after_timeout_backend_fallback";
constexpr const char *kFirmwareApplicationType = "endpoint";
constexpr const char *kFirmwareApiVersion = "hexe-firmware-main-api-v1";
constexpr const char *kModelApiVersion = "hexe-model-bundle-api-v1";
constexpr const char *kAssetApiVersion = "hexe-asset-bundle-api-v1";
constexpr const char *kCalibrationSchemaVersion = "hexe-calibration-schema-v1";
constexpr int kDiscoveryTimeoutMs = 1200;
constexpr char kDiscoverySchemaVersion[] = "hexevoice.endpoint.discovery.v1";
constexpr char kVoiceEventSchemaVersion[] = "hexevoice.voice.event.v1";

struct AudioFrame {
  std::array<int16_t, kMaxChunkSamples> samples;
  size_t sample_count;
  uint32_t level;
  uint32_t noise_floor_level;
  uint32_t speech_peak_level;
  bool vad_speaking;
  bool contains_pre_roll;
  bool micro_vad_active;
  bool micro_vad_started;
  bool micro_vad_ended;
  uint32_t micro_vad_chunk_index;
  uint32_t micro_vad_pause_ms;
};

struct PlacementAmbientAccumulator {
  uint64_t sample_count{0};
  uint64_t square_sum{0};
  uint64_t clipping_count{0};
  uint32_t frame_count{0};
  uint32_t speech_like_frame_count{0};
  uint32_t peak_abs{0};
};

struct PlacementCalibrationState {
  std::string calibration_id;
  int sample_interval_seconds{600};
  int64_t next_sample_due_us{0};
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
bool g_wake_accepted_for_session = false;
bool g_vad_speech_started_reported = false;
bool g_audio_stream_finished = false;
bool g_ws_connected = false;
bool g_ws_started = false;
bool g_discovery_attempted = false;
const char *g_discovery_status = "not_attempted";
bool g_preroll_drained = false;
bool g_wake_election_waiting = false;
int64_t g_wake_election_started_at_us = 0;
bool g_media_transfer_active = false;
int64_t g_last_clock_sync_us = 0;
int g_clock_sync_interval_ms = kClockSyncIntervalMs;
int64_t g_session_started_at_us = 0;
std::array<AudioFrame, kWakePrerollFrameCount> g_preroll_frames = {};
size_t g_preroll_index = 0;
size_t g_preroll_count = 0;
std::array<int16_t, kWakePredictionChunkSamples> g_transport_samples = {};
size_t g_transport_sample_count = 0;
bool g_transport_micro_vad_active = false;
bool g_transport_micro_vad_started = false;
bool g_transport_micro_vad_ended = false;
uint32_t g_transport_micro_vad_chunk_index = 0;
uint32_t g_transport_micro_vad_pause_ms = 0;
uint32_t g_transport_frame_level_peak = 0;
uint32_t g_transport_noise_floor_level = 0;
uint32_t g_transport_speech_peak_level = 0;
uint32_t g_transport_pre_roll_duration_ms = 0;
bool g_transport_contains_pre_roll = false;
bool g_transport_contains_speech = false;
int64_t g_post_tts_input_ignore_until_us = 0;
std::string g_session_id;
std::string g_tts_playback_session_id;
std::string g_wake_candidate_id;
std::string g_ws_rx_buffer;
portMUX_TYPE g_placement_ambient_lock = portMUX_INITIALIZER_UNLOCKED;
PlacementAmbientAccumulator g_placement_ambient = {};
PlacementCalibrationState g_placement_calibration = {};

double probability_as_unit(uint8_t probability) {
  return static_cast<double>(probability) / 255.0;
}

void add_micro_wake_runtime_diagnostics(cJSON *engine, const hexe::voice::MicroWakeRuntimeDiagnostics &runtime) {
  if (engine == nullptr) {
    return;
  }
  cJSON_AddNumberToObject(engine, "inference_count", runtime.inference_count);
  cJSON_AddNumberToObject(engine, "detection_count", runtime.detection_count);
  cJSON_AddNumberToObject(engine, "last_probability_raw", runtime.last_probability);
  cJSON_AddNumberToObject(engine, "last_probability", probability_as_unit(runtime.last_probability));
  cJSON_AddNumberToObject(engine, "last_average_probability_raw", runtime.last_average_probability);
  cJSON_AddNumberToObject(engine, "last_average_probability", probability_as_unit(runtime.last_average_probability));
  cJSON_AddNumberToObject(engine, "last_max_probability_raw", runtime.last_max_probability);
  cJSON_AddNumberToObject(engine, "last_max_probability", probability_as_unit(runtime.last_max_probability));
  cJSON_AddNumberToObject(engine, "best_average_probability_raw", runtime.best_average_probability);
  cJSON_AddNumberToObject(engine, "best_average_probability", probability_as_unit(runtime.best_average_probability));
  cJSON_AddNumberToObject(engine, "last_detection_probability_raw", runtime.last_detection_probability);
  cJSON_AddNumberToObject(engine, "last_detection_probability", probability_as_unit(runtime.last_detection_probability));
}

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
bool refresh_passive_placement_calibration();
void maybe_post_passive_placement_sample();
esp_err_t text_http_event_handler(esp_http_client_event_t *event);
void add_media_inventory_files(cJSON *inventory, const char *key, const char *directory, bool &truncated);
bool ensure_session_started(const char *wake_source);
bool send_vad_speech_started_event(uint32_t level);
bool voice_transport_ready();
bool wake_source_is_local_acceptance(const char *wake_source);
bool event_requests_followup_listen(cJSON *payload, const char *ux_state);
void resume_audio_stream_for_followup();
bool active_audio_stream_timed_out();
bool wake_election_wait_timed_out();
void reset_wake_election_state();
void reset_voice_session_state(bool clear_tts_session);
void stand_down_wake_candidate(const char *reason);
bool wake_election_result_requests_stand_down(cJSON *payload);
const char *wake_election_stand_down_reason(cJSON *payload);
void reset_transport_micro_vad();
void reset_transport_audio_metrics();
void start_input_ignore_cooldown(const char *reason, int64_t duration_us);
void start_post_tts_input_cooldown();
void start_session_reset_input_cooldown();
void clear_post_tts_input_cooldown();
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

bool voice_transport_ready() {
  return backend_ready_for_voice() && g_ws_client != nullptr && g_ws_connected &&
         esp_websocket_client_is_connected(g_ws_client);
}

void reset_wake_election_state() {
  g_wake_election_waiting = false;
  g_wake_election_started_at_us = 0;
  g_wake_candidate_id.clear();
}

void reset_voice_session_state(bool clear_tts_session) {
  g_session_started = false;
  g_wake_accepted_for_session = false;
  g_vad_speech_started_reported = false;
  g_audio_stream_finished = false;
  g_preroll_drained = false;
  g_preroll_count = 0;
  g_preroll_index = 0;
  g_transport_sample_count = 0;
  g_session_started_at_us = 0;
  reset_transport_micro_vad();
  reset_wake_election_state();
  if (clear_tts_session) {
    g_tts_playback_session_id.clear();
  }
  set_audio_streaming(false);
}

void mark_voice_socket_disconnected() {
  g_ws_connected = false;
  auto &state = hexe::state();
  state.voice_ws_connected = false;
  reset_voice_session_state(true);
  if (!state.muted && !state.ota_active) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void remember_preroll_frame(const AudioFrame &frame) {
  AudioFrame preroll_frame = frame;
  preroll_frame.contains_pre_roll = true;
  g_preroll_frames[g_preroll_index] = preroll_frame;
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
      hexe::system::endpoint_id());
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

void reset_transport_micro_vad() {
  g_transport_micro_vad_active = false;
  g_transport_micro_vad_started = false;
  g_transport_micro_vad_ended = false;
  g_transport_micro_vad_chunk_index = 0;
  g_transport_micro_vad_pause_ms = 0;
  reset_transport_audio_metrics();
}

void reset_transport_audio_metrics() {
  g_transport_frame_level_peak = 0;
  g_transport_noise_floor_level = 0;
  g_transport_speech_peak_level = 0;
  g_transport_pre_roll_duration_ms = 0;
  g_transport_contains_pre_roll = false;
  g_transport_contains_speech = false;
}

void clear_post_tts_input_cooldown() {
  g_post_tts_input_ignore_until_us = 0;
}

void start_input_ignore_cooldown(const char *reason, int64_t duration_us) {
  g_post_tts_input_ignore_until_us = esp_timer_get_time() + duration_us;
  g_preroll_count = 0;
  g_preroll_index = 0;
  g_transport_sample_count = 0;
  reset_transport_micro_vad();
  reset_transport_audio_metrics();
  ESP_LOGI(
      kTag,
      "Ignoring microphone wake/VAD input for %lld us after %s",
      static_cast<long long>(duration_us),
      reason == nullptr ? "session reset" : reason);
}

void start_post_tts_input_cooldown() {
  start_input_ignore_cooldown("TTS playback", kPostTtsInputIgnoreUs);
}

void start_session_reset_input_cooldown() {
  start_input_ignore_cooldown("voice session reset", kSessionResetInputIgnoreUs);
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
  payload.append("\",\"is_final\":false");
  if (g_transport_micro_vad_active || g_transport_micro_vad_started || g_transport_micro_vad_ended) {
    char micro_vad[256];
    std::snprintf(
        micro_vad,
        sizeof(micro_vad),
        ",\"micro_vad_chunk_index\":%" PRIu32
        ",\"micro_vad_chunk_started\":%s"
        ",\"micro_vad_chunk_final\":%s"
        ",\"micro_vad_pause_ms\":%" PRIu32,
        g_transport_micro_vad_chunk_index,
        g_transport_micro_vad_started ? "true" : "false",
        g_transport_micro_vad_ended ? "true" : "false",
        g_transport_micro_vad_pause_ms);
    payload.append(micro_vad);
  }
  if (
      g_transport_frame_level_peak > 0 ||
      g_transport_noise_floor_level > 0 ||
      g_transport_speech_peak_level > 0 ||
      g_transport_pre_roll_duration_ms > 0 ||
      g_transport_contains_pre_roll ||
      g_transport_contains_speech) {
    char metrics[320];
    std::snprintf(
        metrics,
        sizeof(metrics),
        ",\"frame_level\":%" PRIu32
        ",\"noise_floor_level\":%" PRIu32
        ",\"speech_peak_level\":%" PRIu32
        ",\"pre_roll_duration_ms\":%" PRIu32
        ",\"contains_pre_roll\":%s"
        ",\"contains_speech\":%s",
        g_transport_frame_level_peak,
        g_transport_noise_floor_level,
        g_transport_speech_peak_level,
        g_transport_pre_roll_duration_ms,
        g_transport_contains_pre_roll ? "true" : "false",
        g_transport_contains_speech ? "true" : "false");
    payload.append(metrics);
  }
  payload.append("}}");
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
    reset_transport_micro_vad();
    reset_transport_audio_metrics();
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

void merge_transport_micro_vad(const AudioFrame &frame) {
  if (frame.micro_vad_active || frame.micro_vad_started || frame.micro_vad_ended) {
    if (!g_transport_micro_vad_active || frame.micro_vad_started) {
      g_transport_micro_vad_chunk_index = frame.micro_vad_chunk_index;
    }
    g_transport_micro_vad_active = true;
    g_transport_micro_vad_started = g_transport_micro_vad_started || frame.micro_vad_started;
    g_transport_micro_vad_ended = g_transport_micro_vad_ended || frame.micro_vad_ended;
    g_transport_micro_vad_pause_ms = std::max(g_transport_micro_vad_pause_ms, frame.micro_vad_pause_ms);
  }
}

uint32_t frame_duration_ms(const AudioFrame &frame) {
  return static_cast<uint32_t>((frame.sample_count * 1000) / hexe::config::kEndpointAudioSampleRateHz);
}

void merge_transport_audio_metrics(const AudioFrame &frame) {
  g_transport_frame_level_peak = std::max(g_transport_frame_level_peak, frame.level);
  if (frame.noise_floor_level > 0) {
    g_transport_noise_floor_level = frame.noise_floor_level;
  }
  g_transport_speech_peak_level = std::max(g_transport_speech_peak_level, frame.speech_peak_level);
  g_transport_contains_pre_roll = g_transport_contains_pre_roll || frame.contains_pre_roll;
  g_transport_contains_speech = g_transport_contains_speech || frame.vad_speaking;
  if (frame.contains_pre_roll) {
    g_transport_pre_roll_duration_ms += frame_duration_ms(frame);
  }
}

bool append_transport_frame(const AudioFrame &frame) {
  merge_transport_micro_vad(frame);
  merge_transport_audio_metrics(frame);
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
    if (g_transport_sample_count == 0 && offset < frame.sample_count) {
      merge_transport_micro_vad(frame);
      merge_transport_audio_metrics(frame);
    }
  }
  if (frame.micro_vad_ended && !flush_transport_samples(true)) {
    return false;
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
  return hexe::system::endpoint_use_tls() ? "https" : "http";
}

const char *scheme_ws() {
  return hexe::system::endpoint_use_tls() ? "wss" : "ws";
}

const char *firmware_version() {
  const esp_app_desc_t *app = esp_app_get_description();
  return app == nullptr ? "unknown" : app->version;
}

const char *hardware_id() {
  static char buffer[32] = "";
  if (buffer[0] != '\0') {
    return buffer;
  }

  uint8_t mac[6] = {};
  if (esp_efuse_mac_get_default(mac) != ESP_OK) {
    std::snprintf(buffer, sizeof(buffer), "esp32s3-unknown");
    return buffer;
  }
  std::snprintf(
      buffer,
      sizeof(buffer),
      "esp32s3-%02x%02x%02x%02x%02x%02x",
      mac[0],
      mac[1],
      mac[2],
      mac[3],
      mac[4],
      mac[5]);
  return buffer;
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
  if (state.ota_active) {
    return "ota";
  }
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

const char *playback_lifecycle_state_name(hexe::PlaybackLifecycleState state) {
  switch (state) {
    case hexe::PlaybackLifecycleState::kQueued:
      return "queued";
    case hexe::PlaybackLifecycleState::kStarted:
      return "started";
    case hexe::PlaybackLifecycleState::kFinished:
      return "finished";
    case hexe::PlaybackLifecycleState::kFailed:
      return "failed";
    case hexe::PlaybackLifecycleState::kStopped:
      return "stopped";
    case hexe::PlaybackLifecycleState::kIdle:
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
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port(),
      hexe::config::kEndpointHeartbeatPath);
  return std::string(buffer);
}

std::string placement_calibrations_status_url() {
  char buffer[256];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d/api/voice/placement-calibrations?endpoint_id=%s",
      scheme_http(),
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port(),
      hexe::system::endpoint_id());
  return std::string(buffer);
}

std::string placement_calibration_sample_url(const char *calibration_id) {
  char buffer[320];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d/api/voice/placement-calibrations/%s/samples",
      scheme_http(),
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port(),
      calibration_id == nullptr ? "" : calibration_id);
  return std::string(buffer);
}

std::string time_url() {
  char buffer[192];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d/api/endpoint/time",
      scheme_http(),
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port());
  return std::string(buffer);
}

std::string websocket_url() {
  char buffer[256];
  const char *voice_ws_path = hexe::config::kEndpointVoiceWsPath;
  const char *query_separator = std::strchr(voice_ws_path, '?') == nullptr ? "?" : "&";
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d%s%sendpoint_id=%s",
      scheme_ws(),
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_ws_port(),
      voice_ws_path,
      query_separator,
      hexe::system::endpoint_id());
  return std::string(buffer);
}

void copy_discovery_string(char *target, size_t target_size, const char *value) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  const char *source = value == nullptr ? "" : value;
  std::strncpy(target, source, target_size - 1);
  target[target_size - 1] = '\0';
}

bool apply_discovery_offer(cJSON *root) {
  if (!cJSON_IsObject(root)) {
    return false;
  }
  cJSON *accepted = cJSON_GetObjectItem(root, "accepted");
  if (!cJSON_IsBool(accepted) || !cJSON_IsTrue(accepted)) {
    cJSON *reason = cJSON_GetObjectItem(root, "reason");
    g_discovery_status = "rejected";
    ESP_LOGW(kTag, "Endpoint discovery rejected: %s", cJSON_IsString(reason) ? reason->valuestring : "unknown");
    return false;
  }
  cJSON *backend_host = cJSON_GetObjectItem(root, "backend_host");
  cJSON *http_port = cJSON_GetObjectItem(root, "http_port");
  cJSON *ws_port = cJSON_GetObjectItem(root, "ws_port");
  cJSON *use_tls = cJSON_GetObjectItem(root, "use_tls");
  if (!cJSON_IsString(backend_host) || backend_host->valuestring[0] == '\0' || !cJSON_IsNumber(http_port) ||
      !cJSON_IsNumber(ws_port) || !cJSON_IsBool(use_tls)) {
    g_discovery_status = "invalid_offer";
    ESP_LOGW(kTag, "Endpoint discovery offer missing backend settings");
    return false;
  }

  hexe::system::EndpointProvisioningSettings settings = hexe::system::endpoint_provisioning_settings();
  cJSON *endpoint_id = cJSON_GetObjectItem(root, "endpoint_id");
  if (cJSON_IsString(endpoint_id) && endpoint_id->valuestring[0] != '\0') {
    copy_discovery_string(settings.endpoint_id, sizeof(settings.endpoint_id), endpoint_id->valuestring);
  }
  copy_discovery_string(settings.backend_host, sizeof(settings.backend_host), backend_host->valuestring);
  settings.http_port = http_port->valueint;
  settings.ws_port = ws_port->valueint;
  settings.use_tls = cJSON_IsTrue(use_tls);
  if (!hexe::system::save_endpoint_provisioning(settings)) {
    g_discovery_status = "persist_failed";
    return false;
  }
  g_discovery_status = "paired";
  return true;
}

bool try_endpoint_discovery() {
  if (!hexe::config::kEndpointDiscoveryEnabled || hexe::system::provisioning_configured() || g_discovery_attempted) {
    return false;
  }
  g_discovery_attempted = true;

  const int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
  if (sock < 0) {
    g_discovery_status = "socket_failed";
    ESP_LOGW(kTag, "Endpoint discovery socket creation failed");
    return false;
  }

  const int broadcast = 1;
  setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &broadcast, sizeof(broadcast));
  timeval timeout = {};
  timeout.tv_sec = kDiscoveryTimeoutMs / 1000;
  timeout.tv_usec = (kDiscoveryTimeoutMs % 1000) * 1000;
  setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  sockaddr_in destination = {};
  destination.sin_family = AF_INET;
  destination.sin_port = htons(hexe::config::kEndpointDiscoveryUdpPort);
  destination.sin_addr.s_addr = inet_addr("255.255.255.255");

  char request[512];
  std::snprintf(
      request,
      sizeof(request),
      "{\"schema_version\":\"%s\",\"endpoint_id\":\"%s\",\"hardware_id\":\"%s\",\"display_name\":\"%s\","
      "\"firmware_version\":\"%s\",\"capabilities\":{\"profile\":\"firmware\",\"identity\":{\"hardware_id\":\"%s\"},\"firmware\":{\"board_profile\":\"%s\"}}}",
      kDiscoverySchemaVersion,
      hexe::system::endpoint_id(),
      hardware_id(),
      hexe::system::endpoint_display_name(),
      firmware_version(),
      hardware_id(),
      hexe::config::kEndpointBoardProfile);
  const int sent = sendto(
      sock,
      request,
      std::strlen(request),
      0,
      reinterpret_cast<sockaddr *>(&destination),
      sizeof(destination));
  if (sent < 0) {
    g_discovery_status = "send_failed";
    ESP_LOGW(kTag, "Endpoint discovery broadcast failed");
    close(sock);
    return false;
  }

  char response[512];
  sockaddr_in source = {};
  socklen_t source_len = sizeof(source);
  const int received = recvfrom(sock, response, sizeof(response) - 1, 0, reinterpret_cast<sockaddr *>(&source), &source_len);
  close(sock);
  if (received <= 0) {
    g_discovery_status = "timed_out";
    ESP_LOGW(kTag, "Endpoint discovery timed out");
    return false;
  }
  response[received] = '\0';
  cJSON *root = cJSON_ParseWithLength(response, received);
  if (root == nullptr) {
    g_discovery_status = "invalid_json";
    ESP_LOGW(kTag, "Endpoint discovery received invalid JSON");
    return false;
  }
  const bool applied = apply_discovery_offer(root);
  cJSON_Delete(root);
  if (applied) {
    ESP_LOGI(kTag, "Endpoint discovery paired with %s:%d", hexe::system::endpoint_backend_host(), hexe::system::endpoint_http_port());
  }
  return applied;
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
void handle_endpoint_timer(cJSON *payload);
void handle_endpoint_provisioning_apply(cJSON *payload);
void handle_endpoint_provisioning_reset(cJSON *payload);

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
  const bool wake_election_result = std::strcmp(type, "wake.election.result") == 0;
  if (wake_election_result && wake_election_result_requests_stand_down(payload)) {
    stand_down_wake_candidate(wake_election_stand_down_reason(payload));
    cJSON_Delete(root);
    return;
  }
  if (wake_accepted) {
    const bool already_locally_accepted = g_wake_accepted_for_session;
    g_wake_accepted_for_session = true;
    reset_wake_election_state();
    set_audio_streaming(true);
    hexe::voice::prewarm_tts_output();
    if (!already_locally_accepted) {
      hexe::voice::play_wake_accepted_sound();
    }
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

  const bool followup_listen = event_requests_followup_listen(payload, ux_state);
  if (followup_listen) {
    resume_audio_stream_for_followup();
  }

  const bool local_wake_waiting_for_backend =
      g_wake_accepted_for_session && g_wake_election_waiting && std::strcmp(type, "session.state") == 0;
  if (wake_accepted ||
      (g_wake_accepted_for_session && std::strcmp(ux_state, "listening") == 0) ||
      (local_wake_waiting_for_backend && std::strcmp(ux_state, "idle") == 0)) {
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kListening;
    }
  } else if (g_wake_accepted_for_session && std::strcmp(ux_state, "thinking") == 0) {
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
  } else if (std::strcmp(type, "response.text") == 0) {
    hexe::voice::prewarm_tts_output();
  } else if (wake_election_result) {
    ESP_LOGI(kTag, "Wake election result received without stand-down request");
  } else if (std::strcmp(type, "tts.ready") == 0) {
    cJSON *session_id = cJSON_GetObjectItem(root, "session_id");
    if (cJSON_IsString(session_id) && session_id->valuestring[0] != '\0') {
      g_tts_playback_session_id = session_id->valuestring;
    } else if (!g_session_id.empty()) {
      g_tts_playback_session_id = g_session_id;
    }
    cJSON *stream_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stream_id") : nullptr;
    cJSON *content_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "content_type") : nullptr;
    cJSON *audio_url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "audio_url") : nullptr;
    hexe::voice::handle_tts_ready(
        cJSON_IsString(stream_id) ? stream_id->valuestring : nullptr,
        cJSON_IsString(content_type) ? content_type->valuestring : nullptr,
        cJSON_IsString(audio_url) ? audio_url->valuestring : nullptr);
  } else if (std::strcmp(type, "ota.update") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "url") : nullptr;
    cJSON *version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "version") : nullptr;
    cJSON *profile = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "profile") : nullptr;
    cJSON *sha256 = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "sha256") : nullptr;
    cJSON *size_bytes = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "size_bytes") : nullptr;
    cJSON *application_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "application_type") : nullptr;
    cJSON *board_profile = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "board_profile") : nullptr;
    cJSON *soc = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "soc") : nullptr;
    cJSON *idf_target = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "idf_target") : nullptr;
    cJSON *flash_size = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "flash_size") : nullptr;
    cJSON *psram_size = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "psram_size") : nullptr;
    cJSON *partition_schema = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "partition_schema") : nullptr;
    cJSON *app_slot_size = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "app_slot_size") : nullptr;
    cJSON *firmware_api_version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "firmware_api_version") : nullptr;
    cJSON *model_api_version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "model_api_version") : nullptr;
    cJSON *asset_api_version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "asset_api_version") : nullptr;
    cJSON *calibration_schema_version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "calibration_schema_version") : nullptr;
    cJSON *release_channel = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "release_channel") : nullptr;
    cJSON *security_policy = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "security_policy") : nullptr;
    cJSON *signature_algorithm = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "signature_algorithm") : nullptr;
    cJSON *signature_key_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "signature_key_id") : nullptr;
    cJSON *manifest_signature = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "manifest_signature") : nullptr;
    char ota_error_code[48] = {};
    hexe::system::OtaUpdateManifest manifest = {};
    manifest.request_id = request_id;
    manifest.url = cJSON_IsString(url) ? url->valuestring : nullptr;
    manifest.version = cJSON_IsString(version) ? version->valuestring : nullptr;
    manifest.profile = cJSON_IsString(profile) ? profile->valuestring : nullptr;
    manifest.sha256 = cJSON_IsString(sha256) ? sha256->valuestring : nullptr;
    manifest.size_bytes = cJSON_IsNumber(size_bytes) ? size_bytes->valueint : 0;
    manifest.application_type = cJSON_IsString(application_type) ? application_type->valuestring : nullptr;
    manifest.board_profile = cJSON_IsString(board_profile) ? board_profile->valuestring : nullptr;
    manifest.soc = cJSON_IsString(soc) ? soc->valuestring : nullptr;
    manifest.idf_target = cJSON_IsString(idf_target) ? idf_target->valuestring : nullptr;
    manifest.flash_size = cJSON_IsString(flash_size) ? flash_size->valuestring : nullptr;
    manifest.psram_size = cJSON_IsString(psram_size) ? psram_size->valuestring : nullptr;
    manifest.partition_schema = cJSON_IsString(partition_schema) ? partition_schema->valuestring : nullptr;
    manifest.app_slot_size = cJSON_IsString(app_slot_size) ? app_slot_size->valuestring : nullptr;
    manifest.firmware_api_version = cJSON_IsString(firmware_api_version) ? firmware_api_version->valuestring : nullptr;
    manifest.model_api_version = cJSON_IsString(model_api_version) ? model_api_version->valuestring : nullptr;
    manifest.asset_api_version = cJSON_IsString(asset_api_version) ? asset_api_version->valuestring : nullptr;
    manifest.calibration_schema_version = cJSON_IsString(calibration_schema_version) ? calibration_schema_version->valuestring : nullptr;
    manifest.release_channel = cJSON_IsString(release_channel) ? release_channel->valuestring : nullptr;
    manifest.security_policy = cJSON_IsString(security_policy) ? security_policy->valuestring : nullptr;
    manifest.signature_algorithm = cJSON_IsString(signature_algorithm) ? signature_algorithm->valuestring : nullptr;
    manifest.signature_key_id = cJSON_IsString(signature_key_id) ? signature_key_id->valuestring : nullptr;
    manifest.manifest_signature = cJSON_IsString(manifest_signature) ? manifest_signature->valuestring : nullptr;
    if (hexe::system::start_ota_update(manifest, ota_error_code, sizeof(ota_error_code))) {
      app_state.phase = hexe::AppPhase::kUpdating;
    } else {
      send_command_error(request_id, "ota.update", ota_error_code, "OTA update rejected by endpoint integrity policy");
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
        hexe::voice::stop_playback("backend_mute_command");
        hexe::voice::cancel_active_session("backend_mute_command");
      }
      app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
      send_command_ack(request_id, "endpoint.mute", "succeeded", app_state.muted ? "Muted" : "Unmuted");
    } else {
      send_command_error(request_id, "endpoint.mute", "invalid_payload", "muted must be boolean");
    }
  } else if (std::strcmp(type, "endpoint.micro_vad") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *pause_ms = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "pause_ms") : nullptr;
    cJSON *energy_threshold = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "energy_threshold") : nullptr;
    if ((pause_ms != nullptr && !cJSON_IsNumber(pause_ms)) ||
        (energy_threshold != nullptr && !cJSON_IsNumber(energy_threshold))) {
      send_command_error(
          request_id,
          "endpoint.micro_vad.set",
          "invalid_payload",
          "pause_ms and energy_threshold must be numeric when provided");
    } else {
      bool updated = false;
      if (pause_ms != nullptr) {
        hexe::system::set_micro_vad_pause_ms(pause_ms->valueint);
        updated = true;
      }
      if (energy_threshold != nullptr) {
        hexe::system::set_micro_vad_energy_threshold(energy_threshold->valueint);
        updated = true;
      }
      if (updated) {
        send_command_ack(request_id, "endpoint.micro_vad.set", "succeeded", "Micro VAD settings updated");
      } else {
        send_command_error(
            request_id,
            "endpoint.micro_vad.set",
            "invalid_payload",
            "pause_ms or energy_threshold must be numeric");
      }
    }
  } else if (std::strcmp(type, "endpoint.cancel") == 0) {
    const char *request_id = payload_request_id(payload);
    hexe::voice::cancel_active_session("backend_cancel_command");
    app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::idle_or_connecting_phase();
    send_command_ack(request_id, "endpoint.cancel", "succeeded", "Active session cancelled");
  } else if (std::strcmp(type, "endpoint.listen") == 0) {
    const char *request_id = payload_request_id(payload);
    if (hexe::voice::start_voice_session("manual")) {
      send_command_ack(request_id, "endpoint.listen", "succeeded", "Voice session started");
    } else {
      send_command_error(request_id, "endpoint.listen", "listen_unavailable", "Voice session could not be started");
    }
  } else if (std::strcmp(type, "playback.stop") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *reason = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "reason") : nullptr;
    hexe::voice::stop_playback(cJSON_IsString(reason) ? reason->valuestring : "backend_stop_command");
    send_command_ack(request_id, "playback.stop", "succeeded", "Playback stop requested");
  } else if (std::strcmp(type, "endpoint.replay") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *session_id = cJSON_GetObjectItem(root, "session_id");
    if (cJSON_IsString(session_id) && session_id->valuestring[0] != '\0') {
      g_tts_playback_session_id = session_id->valuestring;
    }
    cJSON *stream_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stream_id") : nullptr;
    cJSON *content_type = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "content_type") : nullptr;
    cJSON *audio_url = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "audio_url") : nullptr;
    cJSON *loop = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "loop") : nullptr;
    cJSON *mic_mode = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "mic_mode") : nullptr;
    const bool keep_microphone_open =
        cJSON_IsString(mic_mode) && std::strcmp(mic_mode->valuestring, "interrupt_only") == 0;
    if (cJSON_IsString(stream_id) || cJSON_IsString(audio_url)) {
      hexe::voice::handle_tts_ready(
          cJSON_IsString(stream_id) ? stream_id->valuestring : nullptr,
          cJSON_IsString(content_type) ? content_type->valuestring : nullptr,
          cJSON_IsString(audio_url) ? audio_url->valuestring : nullptr,
          cJSON_IsBool(loop) && cJSON_IsTrue(loop),
          keep_microphone_open);
      send_command_ack(request_id, "endpoint.replay", "succeeded", "Replay queued");
    } else {
      send_command_error(request_id, "endpoint.replay", "invalid_payload", "Replay requires stream_id or audio_url");
    }
  } else if (std::strcmp(type, "endpoint.media.transfer") == 0) {
    queue_media_transfer(payload);
  } else if (std::strcmp(type, "endpoint.model_bundle.activate") == 0) {
    const char *request_id = payload_request_id(payload);
    cJSON *bundle_id = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "bundle_id") : nullptr;
    cJSON *version = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "version") : nullptr;
    cJSON *bank = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "bank") : nullptr;
    cJSON *storage = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "storage") : nullptr;
    const char *storage_value = cJSON_IsString(storage) ? storage->valuestring : "internal_ab";
    const hexe::voice::ModelBundleStorageKind storage_kind =
        std::strcmp(storage_value, "sd_versioned") == 0 ? hexe::voice::ModelBundleStorageKind::kSdVersionedDirectory
                                                        : hexe::voice::ModelBundleStorageKind::kInternalBank;
    hexe::voice::ModelBundleCandidate candidate = {};
    candidate.bundle_id = cJSON_IsString(bundle_id) ? bundle_id->valuestring : nullptr;
    candidate.version = cJSON_IsString(version) ? version->valuestring : nullptr;
    candidate.bank = cJSON_IsString(bank) ? bank->valuestring : nullptr;
    candidate.storage_kind = storage_kind;
    candidate.model_api_version = kModelApiVersion;
    candidate.partition_schema = hexe::board::pins::kPartitionSchema;
    char activation_error[64] = {};
    if (hexe::voice::activate_model_bundle_candidate(candidate, activation_error, sizeof(activation_error))) {
      send_command_ack(request_id, "endpoint.model_bundle.activate", "succeeded", "Model bundle activated");
    } else {
      send_command_error(request_id, "endpoint.model_bundle.activate", activation_error, "Model bundle activation rejected");
    }
  } else if (std::strcmp(type, "endpoint.model_bundle.rollback") == 0) {
    const char *request_id = payload_request_id(payload);
    char rollback_error[64] = {};
    if (hexe::voice::rollback_model_bundle(rollback_error, sizeof(rollback_error))) {
      send_command_ack(request_id, "endpoint.model_bundle.rollback", "succeeded", "Model bundle pointer rolled back");
    } else {
      send_command_error(request_id, "endpoint.model_bundle.rollback", rollback_error, "Model bundle rollback unavailable");
    }
  } else if (std::strcmp(type, "endpoint.timer") == 0) {
    handle_endpoint_timer(payload);
  } else if (std::strcmp(type, "endpoint.provisioning.apply") == 0) {
    handle_endpoint_provisioning_apply(payload);
  } else if (std::strcmp(type, "endpoint.provisioning.reset") == 0) {
    handle_endpoint_provisioning_reset(payload);
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
    if (std::strcmp(type, "session.completed") == 0) {
      hexe::board::led_ring_show_completed();
    } else {
      g_tts_playback_session_id.clear();
    }
    reset_voice_session_state(false);
    start_session_reset_input_cooldown();
    if (!app_state.muted && !hexe::voice::tts_playback_active()) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
  } else if (std::strcmp(type, "session.error") == 0) {
    cJSON *recoverable = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "recoverable") : nullptr;
    reset_voice_session_state(true);
    start_session_reset_input_cooldown();
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
    reset_voice_session_state(false);
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

void add_module_status(
    cJSON *modules,
    const char *name,
    const char *owner,
    const char *mode,
    bool local_available,
    const char *state = "intentional_noop") {
  cJSON *module = cJSON_AddObjectToObject(modules, name);
  if (module == nullptr) {
    return;
  }
  cJSON_AddStringToObject(module, "state", state);
  cJSON_AddStringToObject(module, "owner", owner);
  cJSON_AddStringToObject(module, "mode", mode);
  cJSON_AddBoolToObject(module, "local_available", local_available);
}

std::string endpoint_capabilities_json() {
  const auto &state = hexe::state();
  const esp_app_desc_t *app = esp_app_get_description();
  const bool sd_available = hexe::board::sd_card_mounted();
  cJSON *root = cJSON_CreateObject();
  if (root == nullptr) {
    return "{}";
  }

  cJSON *identity = cJSON_AddObjectToObject(root, "identity");
  cJSON_AddStringToObject(identity, "hardware_id", hardware_id());
  cJSON_AddStringToObject(identity, "id_source", "esp_efuse_mac");

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
  cJSON_AddBoolToObject(input, "paused_for_playback", state.mic_paused_for_playback);
  cJSON *passive_calibration = cJSON_AddObjectToObject(input, "passive_placement_calibration");
  cJSON_AddBoolToObject(passive_calibration, "available", true);
  cJSON_AddStringToObject(passive_calibration, "mode", "metrics_only_periodic_ambient");
  cJSON_AddStringToObject(
      passive_calibration,
      "sample_endpoint",
      "/api/voice/placement-calibrations/{calibration_id}/samples");
  cJSON_AddBoolToObject(passive_calibration, "raw_audio_persisted", false);
  cJSON_AddBoolToObject(passive_calibration, "stt_called", false);
  cJSON_AddBoolToObject(passive_calibration, "speaker_id_called", false);
  cJSON *micro_vad = cJSON_AddObjectToObject(input, "micro_vad");
  cJSON_AddBoolToObject(micro_vad, "configurable", true);
  cJSON_AddNumberToObject(micro_vad, "pause_ms", hexe::system::micro_vad_pause_ms());
  cJSON_AddNumberToObject(micro_vad, "min_pause_ms", 80);
  cJSON_AddNumberToObject(micro_vad, "max_pause_ms", 3000);
  cJSON_AddNumberToObject(micro_vad, "energy_threshold", hexe::system::micro_vad_energy_threshold());
  cJSON_AddNumberToObject(micro_vad, "min_energy_threshold", 50);
  cJSON_AddNumberToObject(micro_vad, "max_energy_threshold", 20000);
  cJSON *playback_interrupt = cJSON_AddObjectToObject(input, "playback_interrupt");
  cJSON_AddBoolToObject(playback_interrupt, "available", true);
  cJSON_AddBoolToObject(playback_interrupt, "active", state.tts_playback_active && !state.mic_paused_for_playback);
  cJSON_AddStringToObject(playback_interrupt, "mode", hexe::voice::playback_stop_word_runtime_mode());
  cJSON_AddStringToObject(playback_interrupt, "stop_word", "stop");
  cJSON_AddStringToObject(playback_interrupt, "stop_event_type", "playback.stop");
  cJSON_AddStringToObject(playback_interrupt, "stop_reason", "voice_stop");
  cJSON_AddBoolToObject(playback_interrupt, "backend_fallback", true);
  cJSON_AddStringToObject(playback_interrupt, "backend_fallback_mode", "backend_stt_interrupt");
  cJSON_AddBoolToObject(playback_interrupt, "local_keyword_configured", hexe::voice::playback_stop_word_experimental_provider_configured());
  cJSON_AddBoolToObject(playback_interrupt, "local_keyword_available", hexe::voice::playback_stop_word_on_device_available());
  if (!hexe::voice::playback_stop_word_on_device_available()) {
    cJSON_AddStringToObject(playback_interrupt, "local_keyword_reason", hexe::voice::playback_stop_word_unavailable_reason());
  }
  cJSON *output = cJSON_AddObjectToObject(audio, "output");
  cJSON_AddBoolToObject(output, "available", hexe::board::audio_output_ready());
  cJSON_AddNumberToObject(output, "volume_percent", state.output_volume_percent);
  cJSON_AddBoolToObject(output, "muted", state.muted);
  cJSON_AddBoolToObject(output, "playback_active", state.tts_playback_active);
  cJSON_AddStringToObject(output, "playback_state", playback_lifecycle_state_name(state.tts_playback_state));

  cJSON *controls = cJSON_AddObjectToObject(root, "controls");
  cJSON_AddBoolToObject(controls, "volume", true);
  cJSON_AddBoolToObject(controls, "mute", true);
  cJSON_AddBoolToObject(controls, "cancel", true);
  cJSON_AddBoolToObject(controls, "replay", true);
  cJSON_AddBoolToObject(controls, "storage_reformat", sd_available);
  cJSON_AddBoolToObject(controls, "restart", false);
  cJSON_AddBoolToObject(controls, "reconnect", false);

  cJSON *provisioning = cJSON_AddObjectToObject(root, "provisioning");
  cJSON_AddBoolToObject(provisioning, "configured", hexe::system::provisioning_configured());
  cJSON_AddStringToObject(provisioning, "endpoint_id", hexe::system::endpoint_id());
  cJSON_AddStringToObject(provisioning, "display_name", hexe::system::endpoint_display_name());
  cJSON_AddStringToObject(provisioning, "backend_host", hexe::system::endpoint_backend_host());
  cJSON_AddNumberToObject(provisioning, "http_port", hexe::system::endpoint_http_port());
  cJSON_AddNumberToObject(provisioning, "ws_port", hexe::system::endpoint_ws_port());
  cJSON_AddBoolToObject(provisioning, "use_tls", hexe::system::endpoint_use_tls());
  cJSON_AddBoolToObject(provisioning, "wifi_configured", hexe::system::wifi_ssid()[0] != '\0');
  cJSON_AddBoolToObject(provisioning, "runtime_configurable", true);
  cJSON *discovery = cJSON_AddObjectToObject(provisioning, "discovery");
  cJSON_AddBoolToObject(discovery, "enabled", hexe::config::kEndpointDiscoveryEnabled);
  cJSON_AddNumberToObject(discovery, "udp_port", hexe::config::kEndpointDiscoveryUdpPort);
  cJSON_AddBoolToObject(discovery, "attempted", g_discovery_attempted);
  cJSON_AddStringToObject(discovery, "status", g_discovery_status);
  const hexe::system::BleProvisioningStatus ble_status = hexe::system::ble_provisioning_status();
  cJSON *ble = cJSON_AddObjectToObject(provisioning, "ble");
  cJSON_AddStringToObject(ble, "operation", hexe::system::kBleProvisioningOperation);
  cJSON_AddStringToObject(ble, "lease_scope", hexe::system::kBleProvisioningLeaseScope);
  cJSON_AddStringToObject(ble, "contract_version", hexe::system::kBleProvisioningContractVersion);
  cJSON_AddStringToObject(ble, "payload_schema_id", hexe::system::kBleProvisioningPayloadSchemaId);
  cJSON_AddStringToObject(ble, "service_uuid", hexe::system::kBleProvisioningServiceUuid);
  cJSON_AddStringToObject(ble, "device_identity_uuid", hexe::system::kBleProvisioningDeviceIdentityUuid);
  cJSON_AddStringToObject(ble, "pairing_nonce_uuid", hexe::system::kBleProvisioningPairingNonceUuid);
  cJSON_AddStringToObject(ble, "provisioning_status_uuid", hexe::system::kBleProvisioningStatusUuid);
  cJSON_AddStringToObject(ble, "encrypted_credentials_uuid", hexe::system::kBleProvisioningEncryptedCredentialsUuid);
  cJSON_AddStringToObject(ble, "ack_error_uuid", hexe::system::kBleProvisioningAckErrorUuid);
  cJSON_AddBoolToObject(ble, "supported", ble_status.supported);
  cJSON_AddBoolToObject(ble, "enabled", ble_status.enabled);
  cJSON_AddBoolToObject(ble, "eligible", ble_status.eligible);
  cJSON_AddBoolToObject(ble, "advertising", ble_status.advertising);
  cJSON_AddBoolToObject(ble, "central_scanning", ble_status.central_scanning);
  cJSON_AddStringToObject(ble, "transport", ble_status.transport);
  cJSON_AddStringToObject(ble, "state", ble_status.state);
  cJSON_AddStringToObject(ble, "reason", ble_status.reason);
  cJSON *host_pairing = cJSON_AddObjectToObject(ble, "host_pairing");
  cJSON_AddBoolToObject(host_pairing, "found", ble_status.host_pairing_found);
  cJSON_AddBoolToObject(host_pairing, "role_match", ble_status.host_pairing_role_match);
  if (ble_status.host_pairing_address[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "address", ble_status.host_pairing_address);
  }
  if (ble_status.host_pairing_name[0] != '\0') {
    cJSON_AddStringToObject(host_pairing, "name", ble_status.host_pairing_name);
  }
  cJSON_AddNumberToObject(host_pairing, "rssi", ble_status.host_pairing_rssi);
  cJSON_AddNumberToObject(host_pairing, "seen_at_unix_ms", ble_status.host_pairing_seen_at_unix_ms);
  cJSON_AddBoolToObject(ble, "pairing_nonce_available", ble_status.eligible);
  cJSON_AddBoolToObject(ble, "claim_code_ref_available", false);
  if (ble_status.last_ack[0] != '\0') {
    cJSON_AddStringToObject(ble, "last_ack", ble_status.last_ack);
  }
  if (ble_status.last_error[0] != '\0') {
    cJSON_AddStringToObject(ble, "last_error", ble_status.last_error);
  }

  cJSON *firmware = cJSON_AddObjectToObject(root, "firmware");
  cJSON_AddStringToObject(firmware, "project_name", app == nullptr ? "unknown" : app->project_name);
  cJSON_AddStringToObject(firmware, "version", app == nullptr ? firmware_version() : app->version);
  cJSON_AddStringToObject(firmware, "board_profile", hexe::config::kEndpointBoardProfile);
  cJSON_AddStringToObject(firmware, "application_type", kFirmwareApplicationType);
  cJSON_AddStringToObject(firmware, "soc", hexe::board::pins::kSoc);
  cJSON_AddStringToObject(firmware, "idf_target", hexe::board::pins::kIdfTarget);
  cJSON_AddStringToObject(firmware, "flash_size", hexe::board::pins::kFlashSize);
  cJSON_AddStringToObject(firmware, "psram_size", hexe::board::pins::kPsramSize);
  cJSON_AddStringToObject(firmware, "partition_schema", hexe::board::pins::kPartitionSchema);
  cJSON_AddStringToObject(firmware, "app_slot_size", hexe::board::pins::kAppSlotSize);
  cJSON_AddStringToObject(firmware, "firmware_api_version", kFirmwareApiVersion);
  cJSON_AddStringToObject(firmware, "model_api_version", kModelApiVersion);
  cJSON_AddStringToObject(firmware, "asset_api_version", kAssetApiVersion);
  cJSON_AddStringToObject(firmware, "calibration_schema_version", kCalibrationSchemaVersion);
  cJSON_AddStringToObject(firmware, "build_date", app == nullptr ? "unknown" : app->date);
  cJSON_AddStringToObject(firmware, "build_time", app == nullptr ? "unknown" : app->time);
  cJSON_AddStringToObject(firmware, "idf_version", app == nullptr ? "unknown" : app->idf_ver);
  cJSON *ota = cJSON_AddObjectToObject(firmware, "ota");
  cJSON_AddBoolToObject(ota, "active", state.ota_active);
  cJSON_AddStringToObject(ota, "status", state.ota_active ? "running" : "idle");
  cJSON_AddNumberToObject(ota, "progress_percent", state.ota_progress_percent);
  cJSON_AddNumberToObject(ota, "bytes_read", state.ota_bytes_read);
  cJSON_AddNumberToObject(ota, "size_bytes", state.ota_size_bytes);
  cJSON_AddStringToObject(ota, "signature_algorithm", "hmac-sha256");
  cJSON_AddStringToObject(ota, "signature_key_id", hexe::config::kEndpointOtaManifestKeyId);
  cJSON_AddBoolToObject(ota, "checksum_required", true);
  cJSON_AddBoolToObject(ota, "signature_required", true);
  cJSON_AddStringToObject(ota, "boot_validation_status", hexe::system::ota_boot_validation_status());
  cJSON_AddStringToObject(ota, "boot_validation_error", hexe::system::ota_boot_validation_error());
  cJSON_AddStringToObject(ota, "running_partition", hexe::system::ota_running_partition_label());
  cJSON_AddStringToObject(ota, "running_partition_state", hexe::system::ota_running_partition_state());
  cJSON_AddBoolToObject(ota, "pending_verification", hexe::system::ota_boot_pending_verification());
  cJSON_AddBoolToObject(ota, "startup_self_tests_passed", hexe::system::ota_boot_self_tests_passed());
  cJSON_AddBoolToObject(ota, "marked_valid_after_self_tests", hexe::system::ota_boot_marked_valid());
  cJSON_AddBoolToObject(ota, "rollback_available", hexe::system::ota_rollback_available());
  cJSON *modules = cJSON_AddObjectToObject(firmware, "modules");
  if (modules != nullptr) {
    add_module_status(
        modules,
        "wake_word",
        hexe::voice::wake_word_backend_owned() ? "backend" : "firmware",
        hexe::voice::wake_word_runtime_mode(),
        hexe::voice::wake_word_on_device_available());
    cJSON *wake_word = cJSON_GetObjectItem(modules, "wake_word");
    if (cJSON_IsObject(wake_word)) {
      const hexe::voice::MicroWakeEngineStatus wake_engine = hexe::voice::micro_wake_engine_status();
      const hexe::voice::LocalKeywordModel &wake_model = hexe::voice::wake_word_primary_model();
      cJSON_AddBoolToObject(wake_word, "experimental_provider_configured", hexe::voice::wake_word_experimental_provider_configured());
      cJSON_AddBoolToObject(wake_word, "election_capable", hexe::voice::wake_word_election_capable());
      cJSON_AddNumberToObject(wake_word, "election_timeout_ms", hexe::voice::wake_word_election_timeout_ms());
      cJSON_AddStringToObject(wake_word, "candidate_event_type", "wake.candidate");
      cJSON_AddStringToObject(wake_word, "stand_down_event_type", "wake.election.result");
      cJSON_AddStringToObject(wake_word, "candidate_source", hexe::voice::wake_word_candidate_source());
      cJSON_AddBoolToObject(wake_word, "backend_fallback", true);
      cJSON_AddStringToObject(wake_word, "fallback_source", "backend_openwakeword");
      cJSON_AddStringToObject(wake_word, "timeout_policy", kWakeElectionFallbackPolicy);
      cJSON_AddStringToObject(wake_word, "unavailable_reason", hexe::voice::wake_word_unavailable_reason());
      const hexe::voice::ModelBundleState &bundle_state = hexe::voice::model_bundle_state();
      cJSON *model_bundle = cJSON_AddObjectToObject(wake_word, "model_bundle");
      if (model_bundle != nullptr) {
        cJSON_AddStringToObject(model_bundle, "status", bundle_state.status);
        cJSON_AddStringToObject(model_bundle, "error", bundle_state.error);
        cJSON_AddStringToObject(model_bundle, "active_source", bundle_state.active_source);
        cJSON_AddStringToObject(model_bundle, "active_bank", bundle_state.active_bank);
        cJSON_AddStringToObject(model_bundle, "previous_bank", bundle_state.previous_bank);
        cJSON_AddStringToObject(model_bundle, "active_bundle_id", bundle_state.active_bundle_id);
        cJSON_AddStringToObject(model_bundle, "active_version", bundle_state.active_version);
        cJSON_AddBoolToObject(model_bundle, "embedded_fallback", bundle_state.embedded_fallback);
        cJSON_AddBoolToObject(model_bundle, "rollback_available", bundle_state.rollback_available);
        cJSON_AddBoolToObject(model_bundle, "staged_tested", bundle_state.staged_tested);
        cJSON_AddBoolToObject(model_bundle, "internal_ab_available", bundle_state.internal_ab_available);
        cJSON_AddBoolToObject(model_bundle, "sd_versioned_available", bundle_state.sd_versioned_available);
        cJSON_AddNumberToObject(model_bundle, "model_a_bytes", bundle_state.model_a_bytes);
        cJSON_AddNumberToObject(model_bundle, "model_b_bytes", bundle_state.model_b_bytes);
      }
      cJSON *engine = cJSON_AddObjectToObject(wake_word, "micro_wake_engine");
      if (engine != nullptr) {
        cJSON_AddBoolToObject(engine, "tflm_linked", wake_engine.tflm_linked);
        cJSON_AddBoolToObject(engine, "feature_frontend_linked", wake_engine.feature_frontend_linked);
        cJSON_AddBoolToObject(engine, "feature_frontend_ready", wake_engine.feature_frontend_ready);
        cJSON_AddBoolToObject(engine, "initialized", wake_engine.initialized);
        cJSON_AddBoolToObject(engine, "model_asset_available", wake_engine.wake_model_asset_available);
        cJSON_AddNumberToObject(engine, "model_asset_bytes", wake_engine.wake_model_asset_bytes);
        cJSON_AddBoolToObject(engine, "model_runtime_ready", wake_engine.wake_runtime_ready);
        cJSON_AddNumberToObject(engine, "runtime_arena_bytes", wake_engine.wake_runtime_arena_bytes);
        cJSON_AddBoolToObject(engine, "ready", wake_engine.wake_ready);
        cJSON_AddStringToObject(engine, "reason", wake_engine.wake_reason);
        cJSON_AddNumberToObject(engine, "feature_frame_count", wake_engine.feature_frame_count);
        add_micro_wake_runtime_diagnostics(engine, wake_engine.wake_runtime);
      }
      cJSON *primary_model = cJSON_AddObjectToObject(wake_word, "primary_model");
      if (primary_model != nullptr) {
        cJSON_AddStringToObject(primary_model, "id", wake_model.id);
        cJSON_AddStringToObject(primary_model, "wake_word", wake_model.wake_word);
        cJSON_AddStringToObject(primary_model, "alias", wake_model.alias);
        cJSON_AddStringToObject(primary_model, "source", wake_model.source);
        cJSON_AddStringToObject(primary_model, "manifest_url", wake_model.manifest_url);
        cJSON_AddStringToObject(primary_model, "tflite_url", wake_model.tflite_url);
        cJSON_AddStringToObject(primary_model, "trained_languages", wake_model.trained_languages);
        cJSON_AddStringToObject(primary_model, "author", wake_model.author);
        cJSON_AddStringToObject(primary_model, "minimum_esphome_version", wake_model.minimum_esphome_version);
        cJSON_AddNumberToObject(primary_model, "model_version", wake_model.model_version);
        cJSON_AddStringToObject(primary_model, "manifest_sha256", wake_model.manifest_sha256);
        cJSON_AddStringToObject(primary_model, "tflite_sha256", wake_model.tflite_sha256);
        cJSON_AddNumberToObject(primary_model, "tflite_size_bytes", wake_engine.wake_model_asset_bytes);
        cJSON_AddNumberToObject(primary_model, "probability_cutoff", wake_model.probability_cutoff);
        cJSON_AddNumberToObject(primary_model, "sliding_window_size", wake_model.sliding_window_size);
        cJSON_AddNumberToObject(primary_model, "feature_step_size_ms", wake_model.feature_step_size_ms);
        cJSON_AddNumberToObject(primary_model, "tensor_arena_size", wake_model.tensor_arena_size);
      }
    }
    add_module_status(
        modules,
        "playback_stop_word",
        hexe::voice::playback_stop_word_on_device_available() ? "firmware" : "backend",
        hexe::voice::playback_stop_word_runtime_mode(),
        hexe::voice::playback_stop_word_on_device_available(),
        state.tts_playback_active && !state.mic_paused_for_playback ? "active" : "ready");
    cJSON *playback_stop_word = cJSON_GetObjectItem(modules, "playback_stop_word");
    if (cJSON_IsObject(playback_stop_word)) {
      const hexe::voice::MicroWakeEngineStatus stop_engine = hexe::voice::micro_wake_engine_status();
      const hexe::voice::LocalKeywordModel &stop_model = hexe::voice::playback_stop_word_model();
      cJSON_AddBoolToObject(playback_stop_word, "backend_available", true);
      cJSON_AddBoolToObject(playback_stop_word, "experimental_provider_configured", hexe::voice::playback_stop_word_experimental_provider_configured());
      cJSON_AddStringToObject(playback_stop_word, "stop_word", "stop");
      cJSON_AddStringToObject(playback_stop_word, "stop_event_type", "playback.stop");
      cJSON_AddStringToObject(playback_stop_word, "stop_reason", "voice_stop");
      cJSON_AddBoolToObject(playback_stop_word, "backend_fallback", true);
      cJSON_AddStringToObject(playback_stop_word, "backend_fallback_mode", "backend_stt_interrupt");
      cJSON_AddBoolToObject(playback_stop_word, "local_keyword_available", hexe::voice::playback_stop_word_on_device_available());
      if (!hexe::voice::playback_stop_word_on_device_available()) {
        cJSON_AddStringToObject(playback_stop_word, "local_keyword_reason", hexe::voice::playback_stop_word_unavailable_reason());
      }
      cJSON *engine = cJSON_AddObjectToObject(playback_stop_word, "micro_wake_engine");
      if (engine != nullptr) {
        cJSON_AddBoolToObject(engine, "tflm_linked", stop_engine.tflm_linked);
        cJSON_AddBoolToObject(engine, "feature_frontend_linked", stop_engine.feature_frontend_linked);
        cJSON_AddBoolToObject(engine, "feature_frontend_ready", stop_engine.feature_frontend_ready);
        cJSON_AddBoolToObject(engine, "initialized", stop_engine.initialized);
        cJSON_AddBoolToObject(engine, "model_asset_available", stop_engine.stop_model_asset_available);
        cJSON_AddNumberToObject(engine, "model_asset_bytes", stop_engine.stop_model_asset_bytes);
        cJSON_AddBoolToObject(engine, "model_runtime_ready", stop_engine.stop_runtime_ready);
        cJSON_AddNumberToObject(engine, "runtime_arena_bytes", stop_engine.stop_runtime_arena_bytes);
        cJSON_AddBoolToObject(engine, "ready", stop_engine.stop_ready);
        cJSON_AddStringToObject(engine, "reason", stop_engine.stop_reason);
        cJSON_AddNumberToObject(engine, "feature_frame_count", stop_engine.feature_frame_count);
        add_micro_wake_runtime_diagnostics(engine, stop_engine.stop_runtime);
      }
      cJSON *stop_keyword_model = cJSON_AddObjectToObject(playback_stop_word, "keyword_model");
      if (stop_keyword_model != nullptr) {
        cJSON_AddStringToObject(stop_keyword_model, "id", stop_model.id);
        cJSON_AddStringToObject(stop_keyword_model, "wake_word", stop_model.wake_word);
        cJSON_AddStringToObject(stop_keyword_model, "alias", stop_model.alias);
        cJSON_AddStringToObject(stop_keyword_model, "source", stop_model.source);
        cJSON_AddStringToObject(stop_keyword_model, "manifest_url", stop_model.manifest_url);
        cJSON_AddStringToObject(stop_keyword_model, "tflite_url", stop_model.tflite_url);
        cJSON_AddStringToObject(stop_keyword_model, "trained_languages", stop_model.trained_languages);
        cJSON_AddStringToObject(stop_keyword_model, "author", stop_model.author);
        cJSON_AddStringToObject(stop_keyword_model, "minimum_esphome_version", stop_model.minimum_esphome_version);
        cJSON_AddNumberToObject(stop_keyword_model, "model_version", stop_model.model_version);
        cJSON_AddStringToObject(stop_keyword_model, "manifest_sha256", stop_model.manifest_sha256);
        cJSON_AddStringToObject(stop_keyword_model, "tflite_sha256", stop_model.tflite_sha256);
        cJSON_AddNumberToObject(stop_keyword_model, "tflite_size_bytes", stop_engine.stop_model_asset_bytes);
        cJSON_AddNumberToObject(stop_keyword_model, "probability_cutoff", stop_model.probability_cutoff);
        cJSON_AddNumberToObject(stop_keyword_model, "sliding_window_size", stop_model.sliding_window_size);
        cJSON_AddNumberToObject(stop_keyword_model, "feature_step_size_ms", stop_model.feature_step_size_ms);
        cJSON_AddNumberToObject(stop_keyword_model, "tensor_arena_size", stop_model.tensor_arena_size);
      }
    }
    add_module_status(
        modules,
        "stt_stream",
        hexe::voice::stt_stream_backend_owned() ? "backend" : "firmware",
        hexe::voice::stt_stream_runtime_mode(),
        hexe::voice::stt_stream_local_decoder_available());
    add_module_status(
        modules,
        "assistant_client",
        hexe::voice::assistant_client_backend_owned() ? "backend" : "firmware",
        hexe::voice::assistant_client_runtime_mode(),
        hexe::voice::assistant_client_local_llm_available());
    add_module_status(
        modules,
        "telemetry",
        hexe::system::telemetry_heartbeat_owned() ? "heartbeat" : "firmware",
        hexe::system::telemetry_runtime_mode(),
        hexe::system::telemetry_dedicated_channel_enabled());
    add_module_status(
        modules,
        "ble_onboarding",
        "firmware",
        "nimble_peripheral",
        ble_status.supported,
        ble_status.state);
    cJSON *ble_onboarding = cJSON_GetObjectItem(modules, "ble_onboarding");
    if (cJSON_IsObject(ble_onboarding)) {
      cJSON_AddBoolToObject(ble_onboarding, "enabled", ble_status.enabled);
      cJSON_AddBoolToObject(ble_onboarding, "advertising", ble_status.advertising);
      cJSON_AddStringToObject(ble_onboarding, "transport", ble_status.transport);
      cJSON_AddStringToObject(ble_onboarding, "reason", ble_status.reason);
    }
    add_module_status(
        modules,
        "power",
        "board",
        hexe::system::power_runtime_mode(),
        hexe::system::power_low_power_mode_available());
    cJSON *power = cJSON_GetObjectItem(modules, "power");
    if (cJSON_IsObject(power)) {
      cJSON_AddBoolToObject(power, "shutdown_command_available", hexe::system::power_shutdown_command_available());
    }
  }

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
  if (std::strcmp(event_type, "endpoint.micro_vad") == 0) {
    return "endpoint.micro_vad.set";
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

bool copy_optional_string_field(cJSON *payload, const char *key, char *target, size_t target_size) {
  cJSON *field = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, key) : nullptr;
  if (field == nullptr) {
    return true;
  }
  if (!cJSON_IsString(field)) {
    return false;
  }
  std::strncpy(target, field->valuestring == nullptr ? "" : field->valuestring, target_size - 1);
  target[target_size - 1] = '\0';
  return true;
}

bool assign_optional_int_field(cJSON *payload, const char *key, int *target) {
  cJSON *field = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, key) : nullptr;
  if (field == nullptr) {
    return true;
  }
  if (!cJSON_IsNumber(field)) {
    return false;
  }
  *target = field->valueint;
  return true;
}

bool assign_optional_bool_field(cJSON *payload, const char *key, bool *target) {
  cJSON *field = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, key) : nullptr;
  if (field == nullptr) {
    return true;
  }
  if (!cJSON_IsBool(field)) {
    return false;
  }
  *target = cJSON_IsTrue(field);
  return true;
}

void handle_endpoint_provisioning_apply(cJSON *payload) {
  const char *request_id = payload_request_id(payload);
  hexe::system::EndpointProvisioningSettings settings = hexe::system::endpoint_provisioning_settings();
  if (!copy_optional_string_field(payload, "endpoint_id", settings.endpoint_id, sizeof(settings.endpoint_id)) ||
      !copy_optional_string_field(payload, "display_name", settings.display_name, sizeof(settings.display_name)) ||
      !copy_optional_string_field(payload, "backend_host", settings.backend_host, sizeof(settings.backend_host)) ||
      !copy_optional_string_field(payload, "wifi_ssid", settings.wifi_ssid, sizeof(settings.wifi_ssid)) ||
      !copy_optional_string_field(payload, "wifi_password", settings.wifi_password, sizeof(settings.wifi_password)) ||
      !assign_optional_int_field(payload, "http_port", &settings.http_port) ||
      !assign_optional_int_field(payload, "ws_port", &settings.ws_port) ||
      !assign_optional_bool_field(payload, "use_tls", &settings.use_tls)) {
    send_command_error(
        request_id,
        "endpoint.provisioning.apply",
        "invalid_payload",
        "Provisioning fields must match the declared types");
    return;
  }

  if (settings.display_name[0] == '\0') {
    std::strncpy(settings.display_name, settings.endpoint_id, sizeof(settings.display_name) - 1);
    settings.display_name[sizeof(settings.display_name) - 1] = '\0';
  }

  if (hexe::system::save_endpoint_provisioning(settings)) {
    send_command_ack(
        request_id,
        "endpoint.provisioning.apply",
        "succeeded",
        "Provisioning saved; reboot or reconnect to apply network changes");
  } else {
    send_command_error(
        request_id,
        "endpoint.provisioning.apply",
        "invalid_payload",
        "endpoint_id, backend_host, http_port, and ws_port must be valid");
  }
}

void handle_endpoint_provisioning_reset(cJSON *payload) {
  const char *request_id = payload_request_id(payload);
  hexe::system::reset_endpoint_provisioning();
  send_command_ack(
      request_id,
      "endpoint.provisioning.reset",
      "succeeded",
      "Provisioning reset to build-time defaults; reboot or reconnect to apply network changes");
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
  if (!voice_transport_ready()) {
    return false;
  }
  if (hexe::voice::post_tts_input_cooldown_active() && !wake_source_is_local_acceptance(wake_source)) {
    return false;
  }
  if (wake_source_is_local_acceptance(wake_source)) {
    clear_post_tts_input_cooldown();
  }
  ++g_session_counter;
  g_chunk_index = 0;
  g_audio_stream_finished = false;
  g_wake_accepted_for_session = wake_source_is_local_acceptance(wake_source);
  g_vad_speech_started_reported = false;
  g_preroll_drained = false;
  g_transport_sample_count = 0;
  g_session_started_at_us = esp_timer_get_time();
  reset_transport_micro_vad();
  reset_wake_election_state();
  g_tts_playback_session_id.clear();
  char session_buffer[96];
  std::snprintf(
      session_buffer,
      sizeof(session_buffer),
      "%s-%" PRIu32,
      hexe::system::endpoint_id(),
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
  } else {
    g_wake_accepted_for_session = false;
  }
  return g_session_started;
}

bool wake_source_is_local_acceptance(const char *wake_source) {
  return wake_source != nullptr &&
         (std::strcmp(wake_source, "button") == 0 || std::strcmp(wake_source, "manual") == 0);
}

bool event_requests_followup_listen(cJSON *payload, const char *ux_state) {
  if (std::strcmp(ux_state, "listening") != 0 || !cJSON_IsObject(payload)) {
    return false;
  }
  cJSON *followup = cJSON_GetObjectItem(payload, "followup");
  if (!cJSON_IsObject(followup)) {
    return false;
  }
  cJSON *needed = cJSON_GetObjectItem(followup, "needed");
  cJSON *timeout_ms = cJSON_GetObjectItem(followup, "listen_timeout_ms");
  return cJSON_IsTrue(needed) || (cJSON_IsNumber(timeout_ms) && timeout_ms->valueint > 0);
}

bool wake_election_wait_timed_out() {
  if (!g_wake_election_waiting || g_wake_election_started_at_us <= 0) {
    return false;
  }
  const int64_t timeout_us = static_cast<int64_t>(hexe::voice::wake_word_election_timeout_ms()) * 1000;
  return timeout_us <= 0 || (esp_timer_get_time() - g_wake_election_started_at_us) > timeout_us;
}

bool wake_election_result_requests_stand_down(cJSON *payload) {
  cJSON *stand_down = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "stand_down") : nullptr;
  return cJSON_IsBool(stand_down) && cJSON_IsTrue(stand_down);
}

const char *wake_election_stand_down_reason(cJSON *payload) {
  cJSON *reason = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "reason") : nullptr;
  return cJSON_IsString(reason) && reason->valuestring[0] != '\0' ? reason->valuestring : "wake_election_lost";
}

void stand_down_wake_candidate(const char *reason) {
  ESP_LOGI(kTag, "Wake election stand-down received: %s", reason == nullptr ? "wake_election_lost" : reason);
  reset_voice_session_state(false);
  auto &app_state = hexe::state();
  if (!app_state.muted && !app_state.ota_active && !hexe::voice::tts_playback_active()) {
    app_state.phase = hexe::idle_or_connecting_phase();
  }
}

bool timer_state_matches(const char *value, const char *expected) {
  return value != nullptr && expected != nullptr && std::strcmp(value, expected) == 0;
}

const char *timer_state_from_payload(cJSON *payload) {
  cJSON *state = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "state") : nullptr;
  if (!cJSON_IsString(state)) {
    state = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "status") : nullptr;
  }
  if (!cJSON_IsString(state)) {
    state = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "timer_state") : nullptr;
  }
  return cJSON_IsString(state) ? state->valuestring : "";
}

int64_t unix_ms_from_number(double value) {
  if (value <= 0) {
    return 0;
  }
  int64_t normalized = static_cast<int64_t>(value);
  if (normalized > 0 && normalized < 1000000000000LL) {
    normalized *= 1000;
  }
  return normalized;
}

int64_t read_unix_ms_field(cJSON *payload, const char *field_name) {
  cJSON *item = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, field_name) : nullptr;
  return cJSON_IsNumber(item) ? unix_ms_from_number(item->valuedouble) : 0;
}

int64_t read_duration_ms(cJSON *payload, const char *ms_field, const char *seconds_field) {
  cJSON *ms = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, ms_field) : nullptr;
  if (cJSON_IsNumber(ms) && ms->valuedouble > 0) {
    return static_cast<int64_t>(ms->valuedouble);
  }
  cJSON *seconds = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, seconds_field) : nullptr;
  if (cJSON_IsNumber(seconds) && seconds->valuedouble > 0) {
    return static_cast<int64_t>(seconds->valuedouble * 1000.0);
  }
  return 0;
}

bool is_timer_active_state(const char *state) {
  return timer_state_matches(state, "active") || timer_state_matches(state, "running") || timer_state_matches(state, "started");
}

bool is_timer_paused_state(const char *state) {
  return timer_state_matches(state, "paused");
}

bool is_timer_finished_state(const char *state) {
  return timer_state_matches(state, "finished") || timer_state_matches(state, "expired") || timer_state_matches(state, "done");
}

bool is_timer_clear_state(const char *state) {
  return timer_state_matches(state, "inactive") || timer_state_matches(state, "cancelled") ||
      timer_state_matches(state, "canceled") || timer_state_matches(state, "cleared");
}

void clear_timer_state() {
  auto &app_state = hexe::state();
  app_state.timer_active = false;
  app_state.timer_state = hexe::TimerLifecycleState::kInactive;
  app_state.timer_due_unix_ms = 0;
  app_state.timer_remaining_ms = 0;
  app_state.timer_duration_seconds = 0;
  app_state.timer_label[0] = '\0';
  if (app_state.phase == hexe::AppPhase::kTimerFinished) {
    app_state.phase = hexe::idle_or_connecting_phase();
  }
}

void handle_endpoint_timer(cJSON *payload) {
  const char *request_id = payload_request_id(payload);
  const char *state_value = timer_state_from_payload(payload);
  int64_t due_unix_ms = read_unix_ms_field(payload, "due_unix_ms");
  if (due_unix_ms <= 0) {
    due_unix_ms = read_unix_ms_field(payload, "due_at_unix_ms");
  }
  if (due_unix_ms <= 0) {
    due_unix_ms = read_unix_ms_field(payload, "due_epoch_ms");
  }
  const int64_t requested_unix_ms = read_unix_ms_field(payload, "requested_at_unix_ms");
  int64_t remaining_ms = read_duration_ms(payload, "remaining_ms", "remaining_seconds");
  const int64_t duration_ms = read_duration_ms(payload, "duration_ms", "duration_seconds");
  if (due_unix_ms <= 0 && requested_unix_ms > 0 && duration_ms > 0) {
    due_unix_ms = requested_unix_ms + duration_ms;
  }

  int64_t now_unix_ms = 0;
  const bool has_clock = hexe::system::current_utc_unix_ms(&now_unix_ms);
  if (due_unix_ms <= 0 && has_clock && remaining_ms > 0 && is_timer_active_state(state_value)) {
    due_unix_ms = now_unix_ms + remaining_ms;
  }
  if (remaining_ms <= 0 && due_unix_ms > 0 && has_clock) {
    remaining_ms = due_unix_ms > now_unix_ms ? (due_unix_ms - now_unix_ms) : 0;
  }

  if (state_value[0] == '\0' && (due_unix_ms > 0 || remaining_ms > 0 || duration_ms > 0)) {
    state_value = "active";
  }

  if (is_timer_clear_state(state_value)) {
    clear_timer_state();
    send_command_ack(request_id, "endpoint.timer", "succeeded", "Timer cleared");
    return;
  }

  auto &app_state = hexe::state();
  if (is_timer_active_state(state_value) && has_clock && due_unix_ms > 0 && due_unix_ms <= now_unix_ms) {
    state_value = "finished";
  }
  if (is_timer_active_state(state_value)) {
    app_state.timer_active = true;
    app_state.timer_state = hexe::TimerLifecycleState::kActive;
    app_state.timer_due_unix_ms = due_unix_ms;
    app_state.timer_remaining_ms = remaining_ms;
    app_state.timer_duration_seconds = duration_ms > 0 ? static_cast<int>(duration_ms / 1000) : 0;
    if (app_state.phase == hexe::AppPhase::kTimerFinished) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
  } else if (is_timer_paused_state(state_value)) {
    app_state.timer_active = true;
    app_state.timer_state = hexe::TimerLifecycleState::kPaused;
    app_state.timer_due_unix_ms = 0;
    app_state.timer_remaining_ms = remaining_ms;
    app_state.timer_duration_seconds = duration_ms > 0 ? static_cast<int>(duration_ms / 1000) : app_state.timer_duration_seconds;
    if (app_state.phase == hexe::AppPhase::kTimerFinished) {
      app_state.phase = hexe::idle_or_connecting_phase();
    }
  } else if (is_timer_finished_state(state_value)) {
    app_state.timer_active = true;
    app_state.timer_state = hexe::TimerLifecycleState::kFinished;
    app_state.timer_due_unix_ms = 0;
    app_state.timer_remaining_ms = 0;
    app_state.phase = app_state.muted ? hexe::AppPhase::kMuted : hexe::AppPhase::kTimerFinished;
  } else {
    send_command_error(request_id, "endpoint.timer", "invalid_payload", "Timer state is not supported");
    return;
  }

  cJSON *label = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "label") : nullptr;
  if (!cJSON_IsString(label)) {
    label = cJSON_IsObject(payload) ? cJSON_GetObjectItem(payload, "name") : nullptr;
  }
  if (cJSON_IsString(label) && label->valuestring != nullptr) {
    std::snprintf(app_state.timer_label, sizeof(app_state.timer_label), "%s", label->valuestring);
  } else if (app_state.timer_label[0] == '\0') {
    std::snprintf(app_state.timer_label, sizeof(app_state.timer_label), "%s", "Timer");
  }
  send_command_ack(request_id, "endpoint.timer", "succeeded", "Timer state updated");
}

void resume_audio_stream_for_followup() {
  if (!g_session_started) {
    return;
  }
  g_audio_stream_finished = false;
  g_vad_speech_started_reported = false;
  g_preroll_drained = false;
  g_transport_sample_count = 0;
  g_session_started_at_us = esp_timer_get_time();
  reset_transport_micro_vad();
  reset_wake_election_state();
  set_audio_streaming(true);
  ESP_LOGI(kTag, "Resuming voice audio stream for follow-up window");
}

bool active_audio_stream_timed_out() {
  if (!g_session_started || g_audio_stream_finished || g_session_started_at_us <= 0) {
    return false;
  }
  const int64_t elapsed_us = esp_timer_get_time() - g_session_started_at_us;
  const int64_t timeout_us = g_wake_accepted_for_session ? kAcceptedCaptureTimeoutUs : kPreWakeStreamTimeoutUs;
  return elapsed_us > timeout_us;
}

bool send_vad_speech_started_event(uint32_t level) {
  if (g_vad_speech_started_reported) {
    return true;
  }
  if (hexe::voice::post_tts_input_cooldown_active()) {
    return false;
  }
  if (!g_session_started || g_audio_stream_finished) {
    return false;
  }

  std::string payload;
  payload.reserve(384);
  append_event_header(payload, "vad.speech_started", g_session_id.c_str(), g_sequence++);
  char body[128];
  std::snprintf(
      body,
      sizeof(body),
      "{\"level\":%" PRIu32 ",\"source\":\"firmware_vad\"}}",
      level);
  payload.append(body);
  const bool sent = send_ws_text(payload);
  if (sent) {
    g_vad_speech_started_reported = true;
  }
  return sent;
}

void send_audio_frame(const AudioFrame &frame) {
  if (!g_session_started) {
    remember_preroll_frame(frame);
    return;
  }
  if (g_audio_stream_finished) {
    return;
  }
  if (!g_session_started) {
    return;
  }
  if (active_audio_stream_timed_out()) {
    ESP_LOGW(kTag, "Ending voice audio stream after local capture timeout");
    hexe::voice::finish_audio_stream("capture_timeout");
    return;
  }
  if (g_wake_election_waiting && !g_wake_accepted_for_session) {
    if (!wake_election_wait_timed_out()) {
      remember_preroll_frame(frame);
      return;
    }
    ESP_LOGW(kTag, "Wake election timed out; streaming buffered audio to backend fallback");
    reset_wake_election_state();
    set_audio_streaming(true);
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

bool http_get_text(const std::string &url, size_t max_bytes, std::string *response_text) {
  if (response_text == nullptr || url.empty()) {
    return false;
  }
  response_text->clear();
  HttpTextBuffer response;
  response.max_bytes = max_bytes;
  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_GET;
  config.timeout_ms = kPlacementCalibrationHttpTimeoutMs;
  config.event_handler = text_http_event_handler;
  config.user_data = &response;

  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    return false;
  }
  esp_err_t err = esp_http_client_perform(client);
  const int status_code = esp_http_client_get_status_code(client);
  esp_http_client_cleanup(client);
  if (err != ESP_OK || status_code < 200 || status_code >= 300 || response.overflow) {
    ESP_LOGW(
        kTag,
        "HTTP GET failed: url=%s err=%s status=%d overflow=%d",
        url.c_str(),
        esp_err_to_name(err),
        status_code,
        response.overflow);
    return false;
  }
  *response_text = std::move(response.text);
  return true;
}

bool http_post_json_text(const std::string &url, const std::string &body, size_t max_bytes, std::string *response_text) {
  if (response_text != nullptr) {
    response_text->clear();
  }
  if (url.empty()) {
    return false;
  }
  HttpTextBuffer response;
  response.max_bytes = max_bytes;
  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_POST;
  config.timeout_ms = kPlacementCalibrationHttpTimeoutMs;
  config.event_handler = text_http_event_handler;
  config.user_data = &response;

  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    return false;
  }
  esp_http_client_set_header(client, "Content-Type", "application/json");
  esp_http_client_set_post_field(client, body.c_str(), static_cast<int>(body.size()));
  esp_err_t err = esp_http_client_perform(client);
  const int status_code = esp_http_client_get_status_code(client);
  esp_http_client_cleanup(client);
  if (err != ESP_OK || status_code < 200 || status_code >= 300 || response.overflow) {
    ESP_LOGW(
        kTag,
        "HTTP JSON POST failed: url=%s err=%s status=%d overflow=%d",
        url.c_str(),
        esp_err_to_name(err),
        status_code,
        response.overflow);
    return false;
  }
  if (response_text != nullptr) {
    *response_text = std::move(response.text);
  }
  return true;
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

void clear_passive_placement_calibration() {
  g_placement_calibration.calibration_id.clear();
  g_placement_calibration.sample_interval_seconds = 600;
  g_placement_calibration.next_sample_due_us = 0;
}

void reset_passive_placement_metrics() {
  portENTER_CRITICAL(&g_placement_ambient_lock);
  g_placement_ambient = {};
  portEXIT_CRITICAL(&g_placement_ambient_lock);
}

bool apply_passive_placement_calibration_status(const std::string &payload) {
  if (payload.empty()) {
    return false;
  }
  cJSON *root = cJSON_Parse(payload.c_str());
  if (root == nullptr) {
    ESP_LOGW(kTag, "Passive placement calibration status was not valid JSON");
    return false;
  }

  cJSON *calibrations = cJSON_GetObjectItem(root, "calibrations");
  cJSON *active_windows = cJSON_IsObject(calibrations) ? cJSON_GetObjectItem(calibrations, "active_windows") : nullptr;
  cJSON *selected_window = nullptr;
  if (cJSON_IsArray(active_windows)) {
    cJSON *window = nullptr;
    cJSON_ArrayForEach(window, active_windows) {
      cJSON *endpoint_id = cJSON_IsObject(window) ? cJSON_GetObjectItem(window, "endpoint_id") : nullptr;
      cJSON *status = cJSON_IsObject(window) ? cJSON_GetObjectItem(window, "status") : nullptr;
      if (cJSON_IsString(endpoint_id) && std::strcmp(endpoint_id->valuestring, hexe::system::endpoint_id()) == 0 &&
          (!cJSON_IsString(status) || std::strcmp(status->valuestring, "active") == 0)) {
        selected_window = window;
        break;
      }
    }
  }

  if (selected_window == nullptr) {
    clear_passive_placement_calibration();
    reset_passive_placement_metrics();
    cJSON_Delete(root);
    return true;
  }

  cJSON *calibration_id = cJSON_GetObjectItem(selected_window, "calibration_id");
  cJSON *interval = cJSON_GetObjectItem(selected_window, "sample_interval_seconds");
  if (!cJSON_IsString(calibration_id) || calibration_id->valuestring[0] == '\0') {
    clear_passive_placement_calibration();
    reset_passive_placement_metrics();
    cJSON_Delete(root);
    return false;
  }

  const std::string previous_id = g_placement_calibration.calibration_id;
  g_placement_calibration.calibration_id = calibration_id->valuestring;
  g_placement_calibration.sample_interval_seconds = cJSON_IsNumber(interval) ? std::max(60, interval->valueint) : 600;
  if (previous_id != g_placement_calibration.calibration_id || g_placement_calibration.next_sample_due_us <= 0) {
    if (previous_id != g_placement_calibration.calibration_id) {
      reset_passive_placement_metrics();
    }
    g_placement_calibration.next_sample_due_us = esp_timer_get_time();
    ESP_LOGI(
        kTag,
        "Passive placement calibration active: id=%s interval_seconds=%d",
        g_placement_calibration.calibration_id.c_str(),
        g_placement_calibration.sample_interval_seconds);
  }
  cJSON_Delete(root);
  return true;
}

bool refresh_passive_placement_calibration() {
  if (!hexe::state().backend_connected || hexe::state().ota_active) {
    return false;
  }
  std::string payload;
  if (!http_get_text(placement_calibrations_status_url(), kMaxPlacementCalibrationStatusBytes, &payload)) {
    return false;
  }
  return apply_passive_placement_calibration_status(payload);
}

bool snapshot_passive_placement_metrics(PlacementAmbientAccumulator *snapshot) {
  if (snapshot == nullptr) {
    return false;
  }
  portENTER_CRITICAL(&g_placement_ambient_lock);
  *snapshot = g_placement_ambient;
  g_placement_ambient = {};
  portEXIT_CRITICAL(&g_placement_ambient_lock);
  return snapshot->sample_count > 0 && snapshot->frame_count > 0;
}

void restore_passive_placement_metrics(const PlacementAmbientAccumulator &snapshot) {
  if (snapshot.sample_count == 0 || snapshot.frame_count == 0) {
    return;
  }
  portENTER_CRITICAL(&g_placement_ambient_lock);
  g_placement_ambient.sample_count += snapshot.sample_count;
  g_placement_ambient.square_sum += snapshot.square_sum;
  g_placement_ambient.clipping_count += snapshot.clipping_count;
  g_placement_ambient.frame_count += snapshot.frame_count;
  g_placement_ambient.speech_like_frame_count += snapshot.speech_like_frame_count;
  g_placement_ambient.peak_abs = std::max(g_placement_ambient.peak_abs, snapshot.peak_abs);
  portEXIT_CRITICAL(&g_placement_ambient_lock);
}

std::string passive_placement_sample_body(const PlacementAmbientAccumulator &snapshot) {
  const double full_scale = 32768.0;
  const double rms = snapshot.sample_count == 0
      ? 0.0
      : std::sqrt(static_cast<double>(snapshot.square_sum) / static_cast<double>(snapshot.sample_count)) / full_scale;
  const double peak = static_cast<double>(snapshot.peak_abs) / full_scale;
  const double clipping_ratio = snapshot.sample_count == 0
      ? 0.0
      : static_cast<double>(snapshot.clipping_count) / static_cast<double>(snapshot.sample_count);
  const double speech_ratio = snapshot.frame_count == 0
      ? 0.0
      : static_cast<double>(snapshot.speech_like_frame_count) / static_cast<double>(snapshot.frame_count);
  const uint64_t sample_duration_ms = (snapshot.sample_count * 1000ULL) / hexe::config::kEndpointAudioSampleRateHz;

  char body[512];
  std::snprintf(
      body,
      sizeof(body),
      "{\"observed_at\":\"%s\",\"metrics\":{\"ambient_rms\":%.6f,\"peak\":%.6f,"
      "\"clipping_count\":%llu,\"clipping_ratio\":%.6f,\"speech_like_activity\":%s,"
      "\"speech_like_activity_ratio\":%.6f,\"sample_duration_ms\":%llu}}",
      event_timestamp().c_str(),
      rms,
      peak,
      static_cast<unsigned long long>(snapshot.clipping_count),
      clipping_ratio,
      speech_ratio >= 0.05 ? "true" : "false",
      speech_ratio,
      static_cast<unsigned long long>(sample_duration_ms));
  return std::string(body);
}

void maybe_post_passive_placement_sample() {
  if (g_placement_calibration.calibration_id.empty() || !hexe::state().backend_connected || hexe::state().ota_active) {
    return;
  }
  const int64_t now_us = esp_timer_get_time();
  if (g_placement_calibration.next_sample_due_us > 0 && now_us < g_placement_calibration.next_sample_due_us) {
    return;
  }

  PlacementAmbientAccumulator snapshot;
  if (!snapshot_passive_placement_metrics(&snapshot)) {
    return;
  }

  const std::string url = placement_calibration_sample_url(g_placement_calibration.calibration_id.c_str());
  const std::string body = passive_placement_sample_body(snapshot);
  std::string response;
  if (!http_post_json_text(url, body, 2048, &response)) {
    restore_passive_placement_metrics(snapshot);
    g_placement_calibration.next_sample_due_us = now_us + 30000000LL;
    return;
  }

  g_placement_calibration.next_sample_due_us =
      now_us + (static_cast<int64_t>(std::max(60, g_placement_calibration.sample_interval_seconds)) * 1000000LL);
  ESP_LOGI(
      kTag,
      "Passive placement calibration sample posted: id=%s duration_ms=%llu",
      g_placement_calibration.calibration_id.c_str(),
      static_cast<unsigned long long>((snapshot.sample_count * 1000ULL) / hexe::config::kEndpointAudioSampleRateHz));
}

void heartbeat_task(void *arg) {
  (void)arg;

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
    try_endpoint_discovery();
    if (hexe::state().media_transfer_active) {
      vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
      continue;
    }

    const std::string url = heartbeat_url();
    const std::string clock_url = time_url();
    std::string session_json = "null";
    if (g_session_started) {
      session_json = "\"" + g_session_id + "\"";
    }
    const std::string capabilities = endpoint_capabilities_json();
    std::string body;
    body.reserve(capabilities.size() + 256);
    body.append("{\"endpoint_id\":\"");
    body.append(hexe::system::endpoint_id());
    body.append("\",\"hardware_id\":\"");
    body.append(hardware_id());
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
    if (state.backend_connected) {
      refresh_passive_placement_calibration();
      maybe_post_passive_placement_sample();
    }
    vTaskDelay(pdMS_TO_TICKS(hexe::config::kEndpointHeartbeatIntervalMs));
  }
}

void websocket_task(void *arg) {
  (void)arg;

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
      if (g_ws_client == nullptr) {
        const std::string uri = websocket_url();
        esp_websocket_client_config_t config = {};
        config.uri = uri.c_str();
        config.reconnect_timeout_ms = hexe::config::kEndpointReconnectBackoffMs;
        g_ws_client = esp_websocket_client_init(&config);
        if (g_ws_client == nullptr) {
          ESP_LOGE(kTag, "Failed to initialize voice WebSocket client");
          vTaskDelay(pdMS_TO_TICKS(kBackendReadinessPollMs));
          continue;
        }
        esp_websocket_register_events(g_ws_client, WEBSOCKET_EVENT_ANY, websocket_event_handler, nullptr);
      }
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

bool post_tts_input_cooldown_active() {
  const int64_t ignore_until_us = g_post_tts_input_ignore_until_us;
  if (ignore_until_us <= 0) {
    return false;
  }
  if (esp_timer_get_time() < ignore_until_us) {
    return true;
  }
  g_post_tts_input_ignore_until_us = 0;
  return false;
}

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
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_ws_port(),
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
  const char *session_id = nullptr;
  if (!g_tts_playback_session_id.empty()) {
    session_id = g_tts_playback_session_id.c_str();
  } else if (g_session_started) {
    session_id = g_session_id.c_str();
  }
  append_event_header(envelope, event_type, session_id, g_sequence++);
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
  const bool sent = send_ws_text(envelope);
  if (sent && (std::strcmp(event_type, "tts.playback.completed") == 0 ||
               std::strcmp(event_type, "tts.playback.failed") == 0)) {
    if (std::strcmp(event_type, "tts.playback.completed") == 0) {
      start_post_tts_input_cooldown();
    }
    g_tts_playback_session_id.clear();
  }
  return sent;
}

bool submit_audio_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    uint32_t noise_floor_level,
    uint32_t speech_peak_level,
    bool vad_speaking,
    const MicroVadFrameState *micro_vad) {
  if (g_audio_queue == nullptr || samples == nullptr || sample_count == 0 || !voice_transport_ready()) {
    return false;
  }
  if (post_tts_input_cooldown_active()) {
    return false;
  }

  AudioFrame frame = {};
  frame.sample_count = std::min(sample_count, frame.samples.size());
  std::copy(samples, samples + frame.sample_count, frame.samples.begin());
  frame.level = level;
  frame.noise_floor_level = noise_floor_level;
  frame.speech_peak_level = speech_peak_level;
  frame.vad_speaking = vad_speaking;
  if (micro_vad != nullptr) {
    frame.micro_vad_active = micro_vad->active;
    frame.micro_vad_started = micro_vad->started;
    frame.micro_vad_ended = micro_vad->ended;
    frame.micro_vad_chunk_index = micro_vad->chunk_index;
    frame.micro_vad_pause_ms = micro_vad->pause_ms;
  }

  if (xQueueSend(g_audio_queue, &frame, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping audio frame because transport queue is full");
    return false;
  }
  return true;
}

void observe_passive_placement_frame(
    const int16_t *samples,
    size_t sample_count,
    uint32_t level,
    bool speech_like_activity) {
  if (samples == nullptr || sample_count == 0 || hexe::state().ota_active || hexe::state().mic_paused_for_playback ||
      post_tts_input_cooldown_active()) {
    return;
  }

  uint64_t square_sum = 0;
  uint64_t clipping_count = 0;
  uint32_t peak_abs = 0;
  for (size_t index = 0; index < sample_count; ++index) {
    const int32_t sample = samples[index];
    const uint32_t magnitude = sample < 0 ? static_cast<uint32_t>(-sample) : static_cast<uint32_t>(sample);
    square_sum += static_cast<uint64_t>(magnitude) * static_cast<uint64_t>(magnitude);
    peak_abs = std::max(peak_abs, magnitude);
    if (magnitude >= 32760) {
      ++clipping_count;
    }
  }

  portENTER_CRITICAL(&g_placement_ambient_lock);
  g_placement_ambient.sample_count += sample_count;
  g_placement_ambient.square_sum += square_sum;
  g_placement_ambient.clipping_count += clipping_count;
  ++g_placement_ambient.frame_count;
  if (speech_like_activity) {
    ++g_placement_ambient.speech_like_frame_count;
  }
  g_placement_ambient.peak_abs = std::max(g_placement_ambient.peak_abs, peak_abs);
  portEXIT_CRITICAL(&g_placement_ambient_lock);
}

bool submit_wake_candidate(const WakeCandidateMetrics &candidate) {
  auto &app_state = hexe::state();
  if (app_state.muted || app_state.ota_active || !hexe::voice::wake_word_election_capable()) {
    return false;
  }
  if (!voice_transport_ready() || hexe::voice::post_tts_input_cooldown_active()) {
    return false;
  }
  if (!ensure_session_started("unknown")) {
    return false;
  }

  char candidate_id[128];
  std::snprintf(
      candidate_id,
      sizeof(candidate_id),
      "wake_%s_%" PRIu32 "_%llu",
      hexe::system::endpoint_id(),
      g_session_counter,
      static_cast<unsigned long long>(esp_timer_get_time()));

  cJSON *payload_root = cJSON_CreateObject();
  if (payload_root == nullptr) {
    return false;
  }
  const char *source = candidate.source != nullptr && candidate.source[0] != '\0'
                           ? candidate.source
                           : hexe::voice::wake_word_candidate_source();
  cJSON_AddStringToObject(payload_root, "source", source);
  if (candidate.model != nullptr && candidate.model[0] != '\0') {
    cJSON_AddStringToObject(payload_root, "model", candidate.model);
  }
  cJSON_AddNumberToObject(payload_root, "confidence", std::max(0.0f, std::min(1.0f, candidate.confidence)));
  if (candidate.chunk_index > 0) {
    cJSON_AddNumberToObject(payload_root, "chunk_index", candidate.chunk_index);
  }
  if (candidate.chunk_count > 0) {
    cJSON_AddNumberToObject(payload_root, "chunk_count", candidate.chunk_count);
  }
  const std::string detected_at = event_timestamp();
  cJSON_AddStringToObject(payload_root, "detected_at", detected_at.c_str());
  if (candidate.detection_window_ms > 0) {
    cJSON_AddNumberToObject(payload_root, "detection_window_ms", candidate.detection_window_ms);
  }
  if (candidate.frame_level > 0) {
    cJSON_AddNumberToObject(payload_root, "frame_level", candidate.frame_level);
    cJSON_AddNumberToObject(payload_root, "ambient_level", candidate.frame_level);
  }
  if (candidate.noise_floor_level > 0) {
    cJSON_AddNumberToObject(payload_root, "noise_floor_level", candidate.noise_floor_level);
  }
  if (candidate.speech_peak_level > 0) {
    cJSON_AddNumberToObject(payload_root, "speech_peak_level", candidate.speech_peak_level);
  }
  if (candidate.speech_peak_level > 0 && candidate.noise_floor_level > 0 &&
      candidate.speech_peak_level > candidate.noise_floor_level) {
    cJSON_AddNumberToObject(
        payload_root,
        "snr_db",
        static_cast<double>(candidate.speech_peak_level - candidate.noise_floor_level));
  }
  if (candidate.endpoint_audio_profile_version != nullptr && candidate.endpoint_audio_profile_version[0] != '\0') {
    cJSON_AddStringToObject(payload_root, "endpoint_audio_profile_version", candidate.endpoint_audio_profile_version);
  }
  cJSON *metadata = cJSON_AddObjectToObject(payload_root, "metadata");
  if (metadata != nullptr) {
    cJSON_AddStringToObject(metadata, "candidate_id", candidate_id);
    cJSON_AddStringToObject(metadata, "firmware_timeout_policy", kWakeElectionFallbackPolicy);
    cJSON_AddBoolToObject(metadata, "backend_wake_fallback", true);
    cJSON_AddStringToObject(metadata, "backend_wake_fallback_source", "backend_openwakeword");
  }

  char *rendered = cJSON_PrintUnformatted(payload_root);
  cJSON_Delete(payload_root);
  if (rendered == nullptr) {
    return false;
  }
  std::string envelope;
  envelope.reserve(std::strlen(rendered) + 384);
  append_event_header(envelope, "wake.candidate", g_session_id.c_str(), g_sequence++);
  envelope.append(rendered);
  envelope.append("}");
  cJSON_free(rendered);

  const bool sent = send_ws_text(envelope);
  if (sent) {
    g_wake_candidate_id = candidate_id;
    g_wake_election_waiting = true;
    g_wake_election_started_at_us = esp_timer_get_time();
    g_wake_accepted_for_session = true;
    set_audio_streaming(true);
    if (!app_state.muted) {
      app_state.phase = hexe::AppPhase::kListening;
    }
    hexe::voice::prewarm_tts_output();
    hexe::voice::play_wake_accepted_sound();
    ESP_LOGI(
        kTag,
        "Submitted wake.candidate %s source=%s and entered local listening mode before backend election",
        g_wake_candidate_id.c_str(),
        source);
  } else {
    reset_wake_election_state();
  }
  return sent;
}

bool start_voice_session(const char *wake_source) {
  auto &app_state = hexe::state();
  if (app_state.muted || app_state.ota_active) {
    return false;
  }
  if (!voice_transport_ready()) {
    app_state.phase = hexe::idle_or_connecting_phase();
    return false;
  }

  const bool started = ensure_session_started(wake_source);
  if (started) {
    app_state.phase = hexe::AppPhase::kListening;
  }
  return started;
}

bool notify_vad_speech_started(uint32_t level) {
  return send_vad_speech_started_event(level);
}

bool finish_audio_stream(const char *reason) {
  if (hexe::state().ota_active || !g_session_started || g_audio_stream_finished) {
    return false;
  }
  if (g_wake_election_waiting && !g_wake_accepted_for_session) {
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
    if (g_wake_accepted_for_session) {
      hexe::state().phase = hexe::AppPhase::kThinking;
    }
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
  reset_voice_session_state(true);
  start_session_reset_input_cooldown();
  hexe::voice::stop_tts_playback();
  return sent;
}

}  // namespace hexe::voice
