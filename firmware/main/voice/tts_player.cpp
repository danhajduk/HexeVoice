#include "voice/tts_player.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "app_state.h"
#include "bsp/esp-box-3.h"
#include "endpoint_config.h"
#include "esp_codec_dev.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_tts";
constexpr int kPlaybackQueueDepth = 2;
constexpr int kTaskStackBytes = 6144;
constexpr int kTaskPriority = 4;
constexpr size_t kMaxTtsBytes = 512 * 1024;
constexpr size_t kPlaybackWriteBytes = 4096;

struct PlaybackRequest {
  char stream_id[64];
  char content_type[32];
  char audio_url[192];
};

struct HttpBuffer {
  std::vector<uint8_t> bytes;
  bool overflow{false};
};

struct WavView {
  const uint8_t *pcm{nullptr};
  size_t pcm_size{0};
  int sample_rate{16000};
  int channels{1};
  int bits_per_sample{16};
};

QueueHandle_t g_playback_queue = nullptr;
TaskHandle_t g_playback_task = nullptr;
esp_codec_dev_handle_t g_speaker_codec = nullptr;
volatile bool g_stop_requested = false;

const char *scheme_http() {
  return hexe::config::kEndpointUseTls ? "https" : "http";
}

uint16_t read_le16(const uint8_t *bytes) {
  return static_cast<uint16_t>(bytes[0] | (bytes[1] << 8));
}

uint32_t read_le32(const uint8_t *bytes) {
  return static_cast<uint32_t>(bytes[0] | (bytes[1] << 8) | (bytes[2] << 16) | (bytes[3] << 24));
}

std::string resolve_audio_url(const char *audio_url) {
  if (audio_url == nullptr || audio_url[0] == '\0') {
    return std::string();
  }
  std::string url(audio_url);
  if (url.rfind("http://", 0) == 0 || url.rfind("https://", 0) == 0) {
    return url;
  }

  char buffer[256];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "%s://%s:%d%s",
      scheme_http(),
      hexe::config::kEndpointBackendHost,
      hexe::config::kEndpointHttpPort,
      url.c_str());
  return std::string(buffer);
}

esp_err_t http_event_handler(esp_http_client_event_t *event) {
  if (event == nullptr || event->user_data == nullptr || event->event_id != HTTP_EVENT_ON_DATA) {
    return ESP_OK;
  }
  auto *buffer = static_cast<HttpBuffer *>(event->user_data);
  if (event->data == nullptr || event->data_len <= 0 || buffer->overflow) {
    return ESP_OK;
  }
  if (buffer->bytes.size() + static_cast<size_t>(event->data_len) > kMaxTtsBytes) {
    buffer->overflow = true;
    return ESP_OK;
  }

  const auto *data = static_cast<const uint8_t *>(event->data);
  buffer->bytes.insert(buffer->bytes.end(), data, data + event->data_len);
  return ESP_OK;
}

bool fetch_audio(const std::string &url, std::vector<uint8_t> *audio) {
  if (audio == nullptr || url.empty()) {
    return false;
  }

  HttpBuffer buffer;
  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_GET;
  config.event_handler = http_event_handler;
  config.user_data = &buffer;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    ESP_LOGW(kTag, "Failed to initialize TTS HTTP client");
    return false;
  }

  esp_err_t err = esp_http_client_perform(client);
  const int status_code = esp_http_client_get_status_code(client);
  esp_http_client_cleanup(client);
  if (err != ESP_OK || status_code < 200 || status_code >= 300 || buffer.overflow || buffer.bytes.empty()) {
    ESP_LOGW(kTag, "Failed to fetch TTS audio: err=%s status=%d overflow=%d", esp_err_to_name(err), status_code, buffer.overflow);
    return false;
  }

  *audio = std::move(buffer.bytes);
  return true;
}

bool parse_wav(const std::vector<uint8_t> &audio, WavView *wav) {
  if (wav == nullptr || audio.size() < 44 || std::memcmp(audio.data(), "RIFF", 4) != 0 ||
      std::memcmp(audio.data() + 8, "WAVE", 4) != 0) {
    return false;
  }

  size_t offset = 12;
  bool saw_format = false;
  while (offset + 8 <= audio.size()) {
    const uint8_t *chunk = audio.data() + offset;
    const uint32_t chunk_size = read_le32(chunk + 4);
    const size_t chunk_data = offset + 8;
    if (chunk_data + chunk_size > audio.size()) {
      return false;
    }

    if (std::memcmp(chunk, "fmt ", 4) == 0 && chunk_size >= 16) {
      const uint16_t audio_format = read_le16(audio.data() + chunk_data);
      wav->channels = read_le16(audio.data() + chunk_data + 2);
      wav->sample_rate = static_cast<int>(read_le32(audio.data() + chunk_data + 4));
      wav->bits_per_sample = read_le16(audio.data() + chunk_data + 14);
      saw_format = audio_format == 1 && wav->channels > 0 && wav->bits_per_sample == 16;
    } else if (std::memcmp(chunk, "data", 4) == 0 && saw_format) {
      wav->pcm = audio.data() + chunk_data;
      wav->pcm_size = chunk_size;
      return wav->pcm_size > 0;
    }

    offset = chunk_data + chunk_size + (chunk_size % 2);
  }
  return false;
}

bool play_wav(const std::vector<uint8_t> &audio) {
  WavView wav;
  if (!parse_wav(audio, &wav)) {
    ESP_LOGW(kTag, "TTS audio is not supported WAV PCM");
    return false;
  }
  if (g_speaker_codec == nullptr) {
    g_speaker_codec = bsp_audio_codec_speaker_init();
  }
  if (g_speaker_codec == nullptr) {
    ESP_LOGW(kTag, "Speaker codec is not available");
    return false;
  }

  esp_codec_dev_sample_info_t sample_info = {};
  sample_info.bits_per_sample = wav.bits_per_sample;
  sample_info.channel = wav.channels;
  sample_info.sample_rate = wav.sample_rate;
  esp_codec_dev_set_out_vol(g_speaker_codec, 70);
  int result = esp_codec_dev_open(g_speaker_codec, &sample_info);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to open speaker stream: %d", result);
    return false;
  }

  size_t offset = 0;
  while (offset < wav.pcm_size && !g_stop_requested) {
    const size_t remaining = wav.pcm_size - offset;
    const size_t write_size = std::min(remaining, kPlaybackWriteBytes);
    result = esp_codec_dev_write(g_speaker_codec, const_cast<uint8_t *>(wav.pcm + offset), static_cast<int>(write_size));
    if (result != 0) {
      ESP_LOGW(kTag, "Speaker write failed: %d", result);
      break;
    }
    offset += write_size;
  }
  esp_codec_dev_close(g_speaker_codec);
  return result == 0 && !g_stop_requested;
}

void playback_task(void *arg) {
  (void)arg;
  PlaybackRequest request = {};
  while (true) {
    if (xQueueReceive(g_playback_queue, &request, portMAX_DELAY) != pdTRUE) {
      continue;
    }

    g_stop_requested = false;
    auto &state = hexe::state();
    if (state.muted) {
      continue;
    }
    state.phase = hexe::AppPhase::kReplying;

    const std::string url = resolve_audio_url(request.audio_url);
    std::vector<uint8_t> audio;
    if (fetch_audio(url, &audio) && play_wav(audio) && !state.muted) {
      state.phase = hexe::AppPhase::kIdle;
    } else if (!state.muted && state.phase == hexe::AppPhase::kReplying) {
      state.phase = hexe::AppPhase::kError;
    }
  }
}

void copy_field(char *target, size_t target_size, const char *source) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", source == nullptr ? "" : source);
}
}

namespace hexe::voice {

void init_tts_player() {
  if (g_playback_queue != nullptr) {
    return;
  }
  g_playback_queue = xQueueCreate(kPlaybackQueueDepth, sizeof(PlaybackRequest));
  if (g_playback_queue == nullptr) {
    ESP_LOGE(kTag, "Failed to create TTS playback queue");
    return;
  }
  xTaskCreate(playback_task, "hexe_tts_play", kTaskStackBytes, nullptr, kTaskPriority, &g_playback_task);
  ESP_LOGI(kTag, "TTS player initialized");
}

void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring TTS while muted");
    return;
  }

  ESP_LOGI(
      kTag,
      "TTS ready stream=%s content_type=%s url=%s",
      stream_id == nullptr ? "none" : stream_id,
      content_type == nullptr ? "unknown" : content_type,
      audio_url == nullptr ? "none" : audio_url);
  if (audio_url == nullptr || audio_url[0] == '\0') {
    state.phase = hexe::AppPhase::kReplying;
    return;
  }

  PlaybackRequest request = {};
  copy_field(request.stream_id, sizeof(request.stream_id), stream_id);
  copy_field(request.content_type, sizeof(request.content_type), content_type);
  copy_field(request.audio_url, sizeof(request.audio_url), audio_url);
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping TTS playback request because queue is unavailable");
    state.phase = hexe::AppPhase::kError;
  }
}

void stop_tts_playback() {
  ESP_LOGI(kTag, "Stopping TTS playback");
  g_stop_requested = true;
  auto &state = hexe::state();
  if (!state.muted) {
    state.phase = hexe::AppPhase::kIdle;
  }
}

}  // namespace hexe::voice
