#include "voice/tts_player.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "app_state.h"
#include "board/audio.h"
#include "bsp/esp-box-3.h"
#include "endpoint_config.h"
#include "esp_codec_dev.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "system/settings.h"

namespace {
constexpr char kTag[] = "hexe_tts";
constexpr int kPlaybackQueueDepth = 2;
constexpr int kTaskStackBytes = 6144;
constexpr int kTaskPriority = 4;
constexpr size_t kMaxTtsBytes = 512 * 1024;
constexpr size_t kPlaybackWriteBytes = 4096;
constexpr int kCueSampleRateHz = 16000;
constexpr float kPi = 3.14159265358979323846f;

enum class PlaybackKind {
  kTtsAudio,
  kListeningCue,
};

struct PlaybackRequest {
  PlaybackKind kind{PlaybackKind::kTtsAudio};
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
volatile bool g_playback_active = false;

int current_output_volume() {
  return std::clamp(hexe::state().output_volume_percent, 0, 100);
}

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
  esp_codec_dev_set_out_vol(g_speaker_codec, current_output_volume());
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

bool open_speaker(int sample_rate, int channels, int bits_per_sample, int volume) {
  if (g_speaker_codec == nullptr) {
    g_speaker_codec = bsp_audio_codec_speaker_init();
  }
  if (g_speaker_codec == nullptr) {
    ESP_LOGW(kTag, "Speaker codec is not available");
    return false;
  }

  esp_codec_dev_sample_info_t sample_info = {};
  sample_info.bits_per_sample = bits_per_sample;
  sample_info.channel = channels;
  sample_info.sample_rate = sample_rate;
  esp_codec_dev_set_out_vol(g_speaker_codec, volume);
  const int result = esp_codec_dev_open(g_speaker_codec, &sample_info);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to open speaker stream: %d", result);
    return false;
  }
  return true;
}

bool write_silence(int duration_ms) {
  std::array<int16_t, 160> chunk = {};
  int samples_remaining = (kCueSampleRateHz * duration_ms) / 1000;
  while (samples_remaining > 0 && !g_stop_requested) {
    const int samples_to_write = std::min(samples_remaining, static_cast<int>(chunk.size()));
    const int result = esp_codec_dev_write(
        g_speaker_codec,
        reinterpret_cast<uint8_t *>(chunk.data()),
        samples_to_write * static_cast<int>(sizeof(int16_t)));
    if (result != 0) {
      ESP_LOGW(kTag, "Speaker silence write failed: %d", result);
      return false;
    }
    samples_remaining -= samples_to_write;
  }
  return !g_stop_requested;
}

bool write_tone(int frequency_hz, int duration_ms, int attack_ms, int release_ms, float amplitude) {
  std::array<int16_t, 160> chunk = {};
  int sample_index = 0;
  int samples_remaining = (kCueSampleRateHz * duration_ms) / 1000;
  const int total_samples = samples_remaining;
  const int attack_samples = (kCueSampleRateHz * attack_ms) / 1000;
  const int release_samples = (kCueSampleRateHz * release_ms) / 1000;
  const float angular_step = (2.0f * kPi * static_cast<float>(frequency_hz)) / static_cast<float>(kCueSampleRateHz);
  const float max_sample = 32767.0f * amplitude;

  while (samples_remaining > 0 && !g_stop_requested) {
    const int samples_to_write = std::min(samples_remaining, static_cast<int>(chunk.size()));
    for (int i = 0; i < samples_to_write; ++i) {
      float envelope = 1.0f;
      if (attack_samples > 0 && sample_index < attack_samples) {
        envelope = static_cast<float>(sample_index) / static_cast<float>(attack_samples);
      } else if (release_samples > 0 && sample_index >= total_samples - release_samples) {
        const int release_index = total_samples - sample_index;
        envelope = static_cast<float>(release_index) / static_cast<float>(release_samples);
      }
      const float sample = std::sinf(static_cast<float>(sample_index) * angular_step) * max_sample * envelope;
      chunk[i] = static_cast<int16_t>(sample);
      ++sample_index;
    }
    const int result = esp_codec_dev_write(
        g_speaker_codec,
        reinterpret_cast<uint8_t *>(chunk.data()),
        samples_to_write * static_cast<int>(sizeof(int16_t)));
    if (result != 0) {
      ESP_LOGW(kTag, "Speaker cue write failed: %d", result);
      return false;
    }
    samples_remaining -= samples_to_write;
  }
  return !g_stop_requested;
}

bool play_listening_cue_now() {
  if (!open_speaker(kCueSampleRateHz, 1, 16, current_output_volume())) {
    return false;
  }

  const bool ok = write_tone(880, 70, 6, 14, 0.28f) && write_silence(35) && write_tone(1320, 95, 6, 18, 0.24f);
  esp_codec_dev_close(g_speaker_codec);
  return ok;
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
      ESP_LOGI(kTag, "Skipping playback request while muted");
      if (request.kind == PlaybackKind::kTtsAudio) {
        g_playback_active = false;
      }
      continue;
    }

    if (request.kind == PlaybackKind::kListeningCue) {
      ESP_LOGI(kTag, "Playing listening cue");
      const bool mic_paused = hexe::board::pause_microphone_for_playback();
      if (play_listening_cue_now()) {
        ESP_LOGI(kTag, "Listening cue played");
      } else {
        ESP_LOGW(kTag, "Listening cue failed");
      }
      if (mic_paused) {
        hexe::board::resume_microphone_after_playback();
      }
      continue;
    }

    state.phase = hexe::AppPhase::kReplying;

    const std::string url = resolve_audio_url(request.audio_url);
    std::vector<uint8_t> audio;
    const bool mic_paused = hexe::board::pause_microphone_for_playback();
    const bool played = fetch_audio(url, &audio) && play_wav(audio);
    if (mic_paused) {
      hexe::board::resume_microphone_after_playback();
    }
    if (played && !state.muted) {
      state.phase = hexe::idle_or_connecting_phase();
    } else if (!state.muted && state.phase == hexe::AppPhase::kReplying) {
      state.phase = hexe::AppPhase::kError;
    }
    g_playback_active = false;
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

void play_listening_cue() {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Skipping listening cue while muted");
    return;
  }

  PlaybackRequest request = {};
  request.kind = PlaybackKind::kListeningCue;
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping listening cue because playback queue is unavailable");
  } else {
    ESP_LOGI(kTag, "Queued listening cue");
  }
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
    g_playback_active = false;
    return;
  }

  g_playback_active = true;
  PlaybackRequest request = {};
  request.kind = PlaybackKind::kTtsAudio;
  copy_field(request.stream_id, sizeof(request.stream_id), stream_id);
  copy_field(request.content_type, sizeof(request.content_type), content_type);
  copy_field(request.audio_url, sizeof(request.audio_url), audio_url);
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping TTS playback request because queue is unavailable");
    g_playback_active = false;
    state.phase = hexe::AppPhase::kError;
  }
}

void stop_tts_playback() {
  ESP_LOGI(kTag, "Stopping TTS playback");
  g_stop_requested = true;
  g_playback_active = false;
  auto &state = hexe::state();
  if (!state.muted) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void set_output_volume(int volume_percent) {
  const int clamped = std::clamp(volume_percent, 0, 100);
  hexe::system::set_output_volume_percent(clamped);
  if (g_speaker_codec != nullptr) {
    esp_codec_dev_set_out_vol(g_speaker_codec, clamped);
  }
  ESP_LOGI(kTag, "Output volume set to %d%%", clamped);
}

bool tts_playback_active() {
  return g_playback_active;
}

}  // namespace hexe::voice
