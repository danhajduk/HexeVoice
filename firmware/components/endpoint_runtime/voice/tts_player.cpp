#include "voice/tts_player.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "app_state.h"
#include "board/audio.h"
#include "board/storage.h"
#if defined(HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B)
#include "bsp/esp32_p4_wifi6_touch_lcd_7b.h"
#else
#include "bsp/esp-box-3.h"
#endif
#include "endpoint_config.h"
#include "esp_codec_dev.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "system/settings.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_tts";
constexpr int kPlaybackQueueDepth = 2;
// ESP-IDF's streaming HTTP path uses newlib formatting internally. Keep enough
// task-local headroom for that call chain while PCM data remains heap-buffered.
constexpr int kTaskStackBytes = 10240;
constexpr int kTaskPriority = 4;
constexpr size_t kMaxTtsBytes = 1024 * 1024;
constexpr size_t kPlaybackWriteBytes = 4096;
constexpr size_t kHttpReadBufferBytes = 4096;
constexpr size_t kMaxWavHeaderBytes = 4096;
constexpr int kHttpReadIdleRetryDelayMs = 20;
constexpr int kHttpReadMaxIdleRetries = 50;

struct PlaybackRequest {
  char stream_id[64];
  char content_type[32];
  char audio_url[192];
  char file_path[256];
  bool loop{false};
  bool keep_microphone_open{false};
};

struct WavView {
  const uint8_t *pcm{nullptr};
  size_t pcm_size{0};
  int sample_rate{16000};
  int channels{1};
  int bits_per_sample{16};
};

struct WavStreamInfo {
  size_t data_offset{0};
  uint32_t data_size{0};
  int sample_rate{16000};
  int channels{1};
  int bits_per_sample{16};
};

enum class WavHeaderParseResult {
  kNeedMore,
  kReady,
  kUnsupported,
};

struct PcmStreamWriter {
  std::array<uint8_t, 4> pending{};
  size_t pending_size{0};
  size_t source_bytes_written{0};
  bool first_frame_reported{false};
};

QueueHandle_t g_playback_queue = nullptr;
TaskHandle_t g_playback_task = nullptr;
esp_codec_dev_handle_t g_speaker_codec = nullptr;
volatile bool g_stop_requested = false;
volatile bool g_playback_active = false;
PlaybackRequest g_current_playback_request = {};
bool g_current_playback_request_valid = false;

void set_playback_lifecycle(hexe::PlaybackLifecycleState playback_state, bool active) {
  g_playback_active = active;
  auto &state = hexe::state();
  state.tts_playback_state = playback_state;
  state.tts_playback_active = active;
}

int current_output_volume() {
  return std::clamp(hexe::state().output_volume_percent, 0, 100);
}

void send_playback_event(const char *event_type, const PlaybackRequest &request, const char *reason = nullptr, size_t byte_count = 0) {
  hexe::voice::send_tts_playback_event(event_type, request.stream_id, request.audio_url, reason, byte_count);
}

const char *scheme_http() {
  return hexe::system::endpoint_use_tls() ? "https" : "http";
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
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port(),
      url.c_str());
  return std::string(buffer);
}

uint8_t *allocate_audio_buffer(size_t bytes) {
  auto *buffer = static_cast<uint8_t *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (buffer == nullptr) {
    buffer = static_cast<uint8_t *>(heap_caps_malloc(bytes, MALLOC_CAP_8BIT));
  }
  return buffer;
}

bool read_audio_file(const char *path, uint8_t **audio, size_t *audio_size) {
  if (path == nullptr || path[0] == '\0' || audio == nullptr || audio_size == nullptr) {
    return false;
  }
  *audio = nullptr;
  *audio_size = 0;

  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Could not open SD sound: %s", path);
    return false;
  }
  if (std::fseek(file, 0, SEEK_END) != 0) {
    std::fclose(file);
    return false;
  }
  const long file_size = std::ftell(file);
  if (file_size <= 0 || static_cast<size_t>(file_size) > kMaxTtsBytes) {
    ESP_LOGW(kTag, "Ignoring SD sound %s: size %ld is outside limit", path, file_size);
    std::fclose(file);
    return false;
  }
  std::rewind(file);

  auto *buffer = allocate_audio_buffer(static_cast<size_t>(file_size));
  if (buffer == nullptr) {
    std::fclose(file);
    ESP_LOGW(kTag, "Could not allocate SD sound buffer bytes=%ld", file_size);
    return false;
  }
  const size_t read_bytes = std::fread(buffer, 1, static_cast<size_t>(file_size), file);
  std::fclose(file);
  if (read_bytes != static_cast<size_t>(file_size)) {
    heap_caps_free(buffer);
    ESP_LOGW(kTag, "Could not read SD sound %s", path);
    return false;
  }
  *audio = buffer;
  *audio_size = read_bytes;
  return true;
}

bool parse_wav(const uint8_t *audio, size_t audio_size, WavView *wav) {
  if (wav == nullptr || audio == nullptr || audio_size < 44 || std::memcmp(audio, "RIFF", 4) != 0 ||
      std::memcmp(audio + 8, "WAVE", 4) != 0) {
    return false;
  }

  size_t offset = 12;
  bool saw_format = false;
  while (offset + 8 <= audio_size) {
    const uint8_t *chunk = audio + offset;
    const uint32_t chunk_size = read_le32(chunk + 4);
    const size_t chunk_data = offset + 8;
    if (chunk_data + chunk_size > audio_size) {
      return false;
    }

    if (std::memcmp(chunk, "fmt ", 4) == 0 && chunk_size >= 16) {
      const uint16_t audio_format = read_le16(audio + chunk_data);
      wav->channels = read_le16(audio + chunk_data + 2);
      wav->sample_rate = static_cast<int>(read_le32(audio + chunk_data + 4));
      wav->bits_per_sample = read_le16(audio + chunk_data + 14);
      saw_format = audio_format == 1 && wav->channels > 0 && wav->bits_per_sample == 16;
    } else if (std::memcmp(chunk, "data", 4) == 0 && saw_format) {
      wav->pcm = audio + chunk_data;
      wav->pcm_size = chunk_size;
      return wav->pcm_size > 0;
    }

    offset = chunk_data + chunk_size + (chunk_size % 2);
  }
  return false;
}

WavHeaderParseResult parse_wav_header_prefix(const std::vector<uint8_t> &audio, WavStreamInfo *info) {
  if (info == nullptr) {
    return WavHeaderParseResult::kUnsupported;
  }
  if (audio.size() < 12) {
    return WavHeaderParseResult::kNeedMore;
  }
  if (std::memcmp(audio.data(), "RIFF", 4) != 0 || std::memcmp(audio.data() + 8, "WAVE", 4) != 0) {
    return WavHeaderParseResult::kUnsupported;
  }

  size_t offset = 12;
  bool saw_format = false;
  while (offset + 8 <= audio.size()) {
    const uint8_t *chunk = audio.data() + offset;
    const uint32_t chunk_size = read_le32(chunk + 4);
    const size_t chunk_data = offset + 8;
    if (std::memcmp(chunk, "fmt ", 4) == 0) {
      if (chunk_size < 16 || chunk_data + chunk_size > audio.size()) {
        return chunk_size < 16 ? WavHeaderParseResult::kUnsupported : WavHeaderParseResult::kNeedMore;
      }
      const uint16_t audio_format = read_le16(audio.data() + chunk_data);
      info->channels = read_le16(audio.data() + chunk_data + 2);
      info->sample_rate = static_cast<int>(read_le32(audio.data() + chunk_data + 4));
      info->bits_per_sample = read_le16(audio.data() + chunk_data + 14);
      saw_format = audio_format == 1 && info->channels > 0 && info->channels <= 2 && info->bits_per_sample == 16;
      if (!saw_format) {
        return WavHeaderParseResult::kUnsupported;
      }
    } else if (std::memcmp(chunk, "data", 4) == 0) {
      if (!saw_format) {
        return WavHeaderParseResult::kUnsupported;
      }
      info->data_offset = chunk_data;
      info->data_size = chunk_size;
      return WavHeaderParseResult::kReady;
    }

    const size_t next_offset = chunk_data + chunk_size + (chunk_size % 2);
    if (next_offset < offset) {
      return WavHeaderParseResult::kUnsupported;
    }
    if (next_offset > audio.size()) {
      return WavHeaderParseResult::kNeedMore;
    }
    offset = next_offset;
  }
  return audio.size() >= kMaxWavHeaderBytes ? WavHeaderParseResult::kUnsupported : WavHeaderParseResult::kNeedMore;
}

bool open_speaker_stream(const WavStreamInfo &info) {
  if (g_speaker_codec == nullptr) {
    g_speaker_codec = bsp_audio_codec_speaker_init();
  }
  if (g_speaker_codec == nullptr) {
    ESP_LOGW(kTag, "Speaker codec is not available");
    return false;
  }

  esp_codec_dev_sample_info_t sample_info = {};
  sample_info.bits_per_sample = info.bits_per_sample;
  sample_info.channel = info.channels;
  sample_info.sample_rate = info.sample_rate;
  esp_codec_dev_set_out_vol(g_speaker_codec, current_output_volume());
  const int result = esp_codec_dev_open(g_speaker_codec, &sample_info);
  if (result != 0) {
    ESP_LOGW(kTag, "Failed to open speaker stream: %d", result);
    return false;
  }
  return true;
}

bool write_pcm_frames(
    const uint8_t *data,
    size_t size,
    size_t frame_bytes,
    const PlaybackRequest &request,
    PcmStreamWriter *writer) {
  if (data == nullptr || writer == nullptr || frame_bytes == 0 || frame_bytes > writer->pending.size()) {
    return false;
  }

  size_t offset = 0;
  if (writer->pending_size > 0) {
    const size_t needed = frame_bytes - writer->pending_size;
    const size_t copied = std::min(needed, size);
    std::memcpy(writer->pending.data() + writer->pending_size, data, copied);
    writer->pending_size += copied;
    offset += copied;
    if (writer->pending_size == frame_bytes) {
      if (esp_codec_dev_write(g_speaker_codec, writer->pending.data(), static_cast<int>(frame_bytes)) != 0) {
        return false;
      }
      writer->source_bytes_written += frame_bytes;
      writer->pending_size = 0;
    }
  }

  const size_t aligned_size = ((size - offset) / frame_bytes) * frame_bytes;
  if (aligned_size > 0) {
    uint64_t level_total = 0;
    const size_t sample_count = aligned_size / sizeof(int16_t);
    for (size_t sample = 0; sample < sample_count; ++sample) {
      const size_t byte = offset + (sample * sizeof(int16_t));
      const int16_t value = static_cast<int16_t>(
          static_cast<uint16_t>(data[byte]) |
          (static_cast<uint16_t>(data[byte + 1]) << 8));
      level_total += static_cast<uint32_t>(value < 0 ? -static_cast<int32_t>(value) : value);
    }
    const uint32_t raw_level = sample_count == 0 ? 0 : static_cast<uint32_t>(level_total / sample_count);
    hexe::state().speaker_output_level = raw_level * current_output_volume() / 100;
    if (esp_codec_dev_write(g_speaker_codec, const_cast<uint8_t *>(data + offset), static_cast<int>(aligned_size)) != 0) {
      return false;
    }
    writer->source_bytes_written += aligned_size;
    offset += aligned_size;
  }

  if (offset < size) {
    writer->pending_size = size - offset;
    std::memcpy(writer->pending.data(), data + offset, writer->pending_size);
  }
  if (!writer->first_frame_reported && writer->source_bytes_written > 0) {
    send_playback_event("tts.playback.first_audio_frame", request, nullptr, writer->source_bytes_written);
    writer->first_frame_reported = true;
  }
  return true;
}

bool stream_http_wav(
    const std::string &url,
    const PlaybackRequest &request,
    size_t *played_bytes,
    bool report_first_frame = true) {
  if (url.empty() || played_bytes == nullptr) {
    return false;
  }
  *played_bytes = 0;

  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_GET;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    return false;
  }
  if (esp_http_client_open(client, 0) != ESP_OK) {
    esp_http_client_cleanup(client);
    return false;
  }
  esp_http_client_fetch_headers(client);
  const int status_code = esp_http_client_get_status_code(client);
  if (status_code < 200 || status_code >= 300) {
    ESP_LOGW(kTag, "TTS HTTP stream returned status=%d", status_code);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return false;
  }

  std::vector<uint8_t> header;
  header.reserve(256);
  std::array<uint8_t, kHttpReadBufferBytes> read_buffer{};
  WavStreamInfo info;
  WavHeaderParseResult header_result = WavHeaderParseResult::kNeedMore;
  int idle_retries = 0;
  while (header_result == WavHeaderParseResult::kNeedMore && header.size() < kMaxWavHeaderBytes && !g_stop_requested) {
    const int read = esp_http_client_read(client, reinterpret_cast<char *>(read_buffer.data()), read_buffer.size());
    if (read < 0) {
      break;
    }
    if (read == 0) {
      if (++idle_retries > kHttpReadMaxIdleRetries) {
        break;
      }
      vTaskDelay(pdMS_TO_TICKS(kHttpReadIdleRetryDelayMs));
      continue;
    }
    idle_retries = 0;
    header.insert(header.end(), read_buffer.begin(), read_buffer.begin() + read);
    header_result = parse_wav_header_prefix(header, &info);
  }

  const bool speaker_opened = header_result == WavHeaderParseResult::kReady && open_speaker_stream(info);
  bool played = speaker_opened;
  PcmStreamWriter writer;
  writer.first_frame_reported = !report_first_frame;
  const size_t frame_bytes = static_cast<size_t>(info.channels) * sizeof(int16_t);
  size_t remaining = info.data_size;
  if (speaker_opened) {
    ESP_LOGI(
        kTag,
        "Streaming WAV while downloading sample_rate=%d channels=%d pcm_bytes=%u buffer_bytes=%u",
        info.sample_rate,
        info.channels,
        static_cast<unsigned>(info.data_size),
        static_cast<unsigned>(read_buffer.size()));
  }
  if (played && header.size() > info.data_offset) {
    const size_t available = std::min(remaining, header.size() - info.data_offset);
    played = write_pcm_frames(header.data() + info.data_offset, available, frame_bytes, request, &writer);
    remaining -= available;
  }
  while (played && remaining > 0 && !g_stop_requested) {
    const size_t requested = std::min(remaining, read_buffer.size());
    const int read = esp_http_client_read(client, reinterpret_cast<char *>(read_buffer.data()), requested);
    if (read < 0) {
      played = false;
      break;
    }
    if (read == 0) {
      if (++idle_retries > kHttpReadMaxIdleRetries) {
        played = false;
        break;
      }
      vTaskDelay(pdMS_TO_TICKS(kHttpReadIdleRetryDelayMs));
      continue;
    }
    idle_retries = 0;
    played = write_pcm_frames(read_buffer.data(), static_cast<size_t>(read), frame_bytes, request, &writer);
    remaining -= static_cast<size_t>(read);
  }

  *played_bytes = writer.source_bytes_written;
  hexe::state().speaker_output_level = 0;
  if (speaker_opened) {
    esp_codec_dev_close(g_speaker_codec);
  }
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
  if (!played || remaining != 0 || writer.pending_size != 0 || g_stop_requested) {
    ESP_LOGW(
        kTag,
        "TTS HTTP stream failed played=%d remaining=%u pending=%u stopped=%d",
        played ? 1 : 0,
        static_cast<unsigned>(remaining),
        static_cast<unsigned>(writer.pending_size),
        g_stop_requested ? 1 : 0);
    return false;
  }
  return writer.source_bytes_written > 0;
}

bool play_wav(const uint8_t *audio, size_t audio_size, const PlaybackRequest &request, bool report_first_frame = true) {
  WavView wav;
  if (!parse_wav(audio, audio_size, &wav)) {
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
  bool first_frame_reported = false;
  while (offset < wav.pcm_size && !g_stop_requested) {
    const size_t remaining = wav.pcm_size - offset;
    const size_t write_size = std::min(remaining, kPlaybackWriteBytes);
    uint64_t level_total = 0;
    const size_t sample_count = write_size / sizeof(int16_t);
    for (size_t sample = 0; sample < sample_count; ++sample) {
      const size_t byte = offset + (sample * sizeof(int16_t));
      const int16_t value = static_cast<int16_t>(
          static_cast<uint16_t>(wav.pcm[byte]) |
          (static_cast<uint16_t>(wav.pcm[byte + 1]) << 8));
      level_total += static_cast<uint32_t>(value < 0 ? -static_cast<int32_t>(value) : value);
    }
    const uint32_t raw_level = sample_count == 0 ? 0 : static_cast<uint32_t>(level_total / sample_count);
    hexe::state().speaker_output_level = raw_level * current_output_volume() / 100;
    result = esp_codec_dev_write(g_speaker_codec, const_cast<uint8_t *>(wav.pcm + offset), static_cast<int>(write_size));
    if (result != 0) {
      ESP_LOGW(kTag, "Speaker write failed: %d", result);
      break;
    }
    offset += write_size;
    if (report_first_frame && !first_frame_reported) {
      send_playback_event("tts.playback.first_audio_frame", request, nullptr, offset);
      first_frame_reported = true;
    }
  }
  hexe::state().speaker_output_level = 0;
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
    g_current_playback_request = request;
    g_current_playback_request_valid = true;
    auto &state = hexe::state();
    if (state.muted) {
      ESP_LOGI(kTag, "Skipping playback request while muted");
      send_playback_event("tts.playback.failed", request, "muted");
      set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
      g_current_playback_request_valid = false;
      continue;
    }

    state.phase = hexe::AppPhase::kReplying;
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kStarted, true);

    uint8_t *audio = nullptr;
    size_t audio_size = 0;
    const bool mic_paused = request.keep_microphone_open ? false : hexe::board::pause_microphone_for_playback();
    const std::string url = resolve_audio_url(request.audio_url);
    const bool remote_audio = request.file_path[0] == '\0';
    if (remote_audio) {
      send_playback_event("tts.playback.download_started", request);
    }
    bool played = false;
    bool loaded = false;
    if (remote_audio) {
      bool report_first_frame = true;
      do {
        audio_size = 0;
        played = stream_http_wav(url, request, &audio_size, report_first_frame);
        report_first_frame = false;
        loaded = loaded || played || audio_size > 0;
      } while (request.loop && played && !g_stop_requested && !state.muted);
    } else {
      loaded = read_audio_file(request.file_path, &audio, &audio_size);
    }
    if (!remote_audio && loaded) {
      bool report_first_frame = true;
      do {
        played = play_wav(audio, audio_size, request, report_first_frame);
        report_first_frame = false;
      } while (request.loop && played && !g_stop_requested && !state.muted);
    }
    if (mic_paused) {
      hexe::board::resume_microphone_after_playback();
    }
    if (!loaded) {
      send_playback_event(
          "tts.playback.failed",
          request,
          remote_audio ? "download_failed" : "file_read_failed");
    } else if (played && !request.loop) {
      send_playback_event("tts.playback.completed", request, nullptr, audio_size);
    } else if (!request.loop || !g_stop_requested) {
      send_playback_event("tts.playback.failed", request, g_stop_requested ? "stopped" : "playback_failed", audio_size);
    }
    heap_caps_free(audio);
    if ((played || g_stop_requested) && !state.muted) {
      state.phase = hexe::idle_or_connecting_phase();
    } else if (!state.muted && state.phase == hexe::AppPhase::kReplying) {
      state.phase = hexe::AppPhase::kError;
    }
    set_playback_lifecycle(
        played && !request.loop ? hexe::PlaybackLifecycleState::kFinished
               : (g_stop_requested ? hexe::PlaybackLifecycleState::kStopped : hexe::PlaybackLifecycleState::kFailed),
        false);
    g_current_playback_request_valid = false;
  }
}

void copy_field(char *target, size_t target_size, const char *source) {
  if (target == nullptr || target_size == 0) {
    return;
  }
  std::snprintf(target, target_size, "%s", source == nullptr ? "" : source);
}

bool is_safe_sound_filename(const char *filename) {
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

void prewarm_tts_output() {
}

void handle_tts_ready(
    const char *stream_id,
    const char *content_type,
    const char *audio_url,
    bool loop,
    bool keep_microphone_open) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring TTS while muted");
    hexe::voice::send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "muted", 0);
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
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
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
    hexe::voice::send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "missing_audio_url", 0);
    return;
  }

  set_playback_lifecycle(hexe::PlaybackLifecycleState::kQueued, true);
  PlaybackRequest request = {};
  copy_field(request.stream_id, sizeof(request.stream_id), stream_id);
  copy_field(request.content_type, sizeof(request.content_type), content_type);
  copy_field(request.audio_url, sizeof(request.audio_url), audio_url);
  request.loop = loop;
  request.keep_microphone_open = keep_microphone_open;
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping TTS playback request because queue is unavailable");
    send_playback_event("tts.playback.failed", request, "queue_unavailable");
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
    state.phase = hexe::AppPhase::kError;
  }
}

void play_wake_accepted_sound() {}

void play_sd_sound(const char *filename) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring SD sound while muted");
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
    return;
  }
  if (!hexe::board::sd_card_mounted() || !is_safe_sound_filename(filename)) {
    ESP_LOGW(kTag, "Ignoring SD sound request for invalid or unavailable file");
    return;
  }

  PlaybackRequest request = {};
  copy_field(request.stream_id, sizeof(request.stream_id), filename);
  copy_field(request.content_type, sizeof(request.content_type), "audio/wav");
  const int written = std::snprintf(
      request.file_path,
      sizeof(request.file_path),
      "%s/%s",
      hexe::board::sd_card_sounds_path(),
      filename);
  if (written < 0 || written >= static_cast<int>(sizeof(request.file_path))) {
    ESP_LOGW(kTag, "SD sound path is too long");
    return;
  }

  set_playback_lifecycle(hexe::PlaybackLifecycleState::kQueued, true);
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping SD sound playback request because queue is unavailable");
    set_playback_lifecycle(hexe::PlaybackLifecycleState::kFailed, false);
  }
}

void stop_playback(const char *reason) {
  ESP_LOGI(kTag, "Stopping playback");
  g_stop_requested = true;
  if (g_playback_active && g_current_playback_request_valid) {
    send_playback_event(
        "playback.stop",
        g_current_playback_request,
        reason == nullptr ? "operator_stop" : reason);
  }
  set_playback_lifecycle(hexe::PlaybackLifecycleState::kStopped, false);
  auto &state = hexe::state();
  if (!state.muted) {
    state.phase = hexe::idle_or_connecting_phase();
  }
}

void stop_tts_playback() {
  stop_playback("tts_stop");
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
