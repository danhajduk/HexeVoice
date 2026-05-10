#include "voice/tts_player.h"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <memory>
#include <new>
#include <string>
#include <vector>

#include "app_state.h"
#include "board/audio.h"
#include "board/storage.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "endpoint_config.h"
#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "system/settings.h"
#include "voice/backend_client.h"

namespace {
constexpr char kTag[] = "hexe_tts_vpe";
constexpr int kPlaybackQueueDepth = 2;
constexpr int kPlaybackTaskStackBytes = 12288;
constexpr int kPrewarmTaskStackBytes = 4096;
constexpr int kTaskPriority = 4;
constexpr int kSpeakerSampleRate = 48000;
constexpr size_t kMaxTtsBytes = 512 * 1024;
constexpr size_t kHttpReadBufferBytes = 4096;
constexpr size_t kPlaybackFrameCapacity = 384;
constexpr size_t kPlaybackDrainFrames = kSpeakerSampleRate / 4;
constexpr int kHttpReadIdleRetryDelayMs = 20;
constexpr int kHttpReadMaxIdleRetries = 50;
constexpr size_t kMaxWavHeaderBytes = 4096;
constexpr uint32_t kI2cClockHz = 400000;
constexpr uint32_t kI2cTimeoutMs = 1000;
constexpr uint32_t kI2sWriteTimeoutMs = 1000;

constexpr gpio_num_t kI2cSda = GPIO_NUM_5;
constexpr gpio_num_t kI2cScl = GPIO_NUM_6;
constexpr gpio_num_t kSpeakerLrclk = GPIO_NUM_7;
constexpr gpio_num_t kSpeakerBclk = GPIO_NUM_8;
constexpr gpio_num_t kSpeakerDout = GPIO_NUM_10;
constexpr gpio_num_t kSpeakerAmp = GPIO_NUM_47;
constexpr uint8_t kAic3204I2cAddress = 0x18;

constexpr uint8_t kAicPageCtrl = 0x00;
constexpr uint8_t kAicSwReset = 0x01;
constexpr uint8_t kAicNdac = 0x0B;
constexpr uint8_t kAicMdac = 0x0C;
constexpr uint8_t kAicDosr = 0x0E;
constexpr uint8_t kAicCodecIf = 0x1B;
constexpr uint8_t kAicAudioIf4 = 0x1F;
constexpr uint8_t kAicAudioIf5 = 0x20;
constexpr uint8_t kAicSclkMfp3 = 0x38;
constexpr uint8_t kAicDacSigProc = 0x3C;
constexpr uint8_t kAicDacChSet1 = 0x3F;
constexpr uint8_t kAicDacChSet2 = 0x40;
constexpr uint8_t kAicDaclVolD = 0x41;
constexpr uint8_t kAicDacrVolD = 0x42;
constexpr uint8_t kAicLdoCtrl = 0x02;
constexpr uint8_t kAicPwrCfg = 0x01;
constexpr uint8_t kAicPlayCfg1 = 0x03;
constexpr uint8_t kAicPlayCfg2 = 0x04;
constexpr uint8_t kAicOpPwrCtrl = 0x09;
constexpr uint8_t kAicCmCtrl = 0x0A;
constexpr uint8_t kAicHplRoute = 0x0C;
constexpr uint8_t kAicHprRoute = 0x0D;
constexpr uint8_t kAicLolRoute = 0x0E;
constexpr uint8_t kAicLorRoute = 0x0F;
constexpr uint8_t kAicHplGain = 0x10;
constexpr uint8_t kAicHprGain = 0x11;
constexpr uint8_t kAicLolDrvGain = 0x12;
constexpr uint8_t kAicLorDrvGain = 0x13;
constexpr uint8_t kAicHpStart = 0x14;
constexpr uint8_t kAicRefStartup = 0x7B;

struct PlaybackRequest {
  char stream_id[64];
  char content_type[32];
  char audio_url[192];
  char file_path[256];
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

enum class StreamPlaybackResult {
  kPlayed,
  kFallback,
  kFailed,
};

struct PcmStreamWriter {
  std::array<int32_t, kPlaybackFrameCapacity * 2> output_frames{};
  size_t queued_frames{0};
  bool first_frame_reported{false};
  size_t source_bytes_written{0};
};

QueueHandle_t g_playback_queue = nullptr;
TaskHandle_t g_playback_task = nullptr;
TaskHandle_t g_prewarm_task = nullptr;
i2c_master_bus_handle_t g_i2c_bus = nullptr;
i2c_master_dev_handle_t g_aic_device = nullptr;
i2s_chan_handle_t g_tx_channel = nullptr;
SemaphoreHandle_t g_codec_lock = nullptr;
volatile bool g_stop_requested = false;
volatile bool g_playback_active = false;
bool g_aic_ready = false;
bool g_tx_enabled = false;

int current_output_volume() {
  return std::clamp(hexe::state().output_volume_percent, 0, 100);
}

void send_playback_event(const char *event_type, const PlaybackRequest &request, const char *reason = nullptr, size_t byte_count = 0) {
  hexe::voice::send_tts_playback_event(event_type, request.stream_id, request.audio_url, reason, byte_count);
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

bool read_audio_file(const char *path, std::vector<uint8_t> *audio) {
  if (path == nullptr || path[0] == '\0' || audio == nullptr) {
    return false;
  }

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

  audio->assign(static_cast<size_t>(file_size), 0);
  const size_t read_bytes = std::fread(audio->data(), 1, audio->size(), file);
  std::fclose(file);
  if (read_bytes != audio->size()) {
    audio->clear();
    ESP_LOGW(kTag, "Could not read SD sound %s", path);
    return false;
  }
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
      saw_format = audio_format == 1 && wav->channels > 0 && wav->channels <= 2 && wav->bits_per_sample == 16;
    } else if (std::memcmp(chunk, "data", 4) == 0 && saw_format) {
      wav->pcm = audio.data() + chunk_data;
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
    if (chunk_data > audio.size()) {
      return WavHeaderParseResult::kNeedMore;
    }

    if (std::memcmp(chunk, "fmt ", 4) == 0) {
      if (chunk_size < 16) {
        return WavHeaderParseResult::kUnsupported;
      }
      if (chunk_data + chunk_size > audio.size()) {
        return WavHeaderParseResult::kNeedMore;
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
  return audio.size() > kMaxWavHeaderBytes ? WavHeaderParseResult::kUnsupported : WavHeaderParseResult::kNeedMore;
}

bool ensure_i2c_bus() {
  if (g_i2c_bus != nullptr) {
    return true;
  }

  esp_err_t result = i2c_master_get_bus_handle(I2C_NUM_0, &g_i2c_bus);
  if (result == ESP_OK) {
    return true;
  }

  i2c_master_bus_config_t bus_config = {};
  bus_config.i2c_port = I2C_NUM_0;
  bus_config.sda_io_num = kI2cSda;
  bus_config.scl_io_num = kI2cScl;
  bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
  bus_config.glitch_ignore_cnt = 7;
  bus_config.flags.enable_internal_pullup = true;

  result = i2c_new_master_bus(&bus_config, &g_i2c_bus);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create Voice PE codec I2C bus: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool ensure_aic_device() {
  if (g_aic_device != nullptr) {
    return true;
  }
  if (!ensure_i2c_bus()) {
    return false;
  }

  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = kAic3204I2cAddress;
  device_config.scl_speed_hz = kI2cClockHz;

  const esp_err_t result = i2c_master_bus_add_device(g_i2c_bus, &device_config, &g_aic_device);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to add Voice PE AIC3204 device: %s", esp_err_to_name(result));
    return false;
  }
  return true;
}

bool aic_write_byte(uint8_t register_address, uint8_t value) {
  if (!ensure_aic_device()) {
    return false;
  }
  const uint8_t data[] = {register_address, value};
  const esp_err_t result = i2c_master_transmit(g_aic_device, data, sizeof(data), pdMS_TO_TICKS(kI2cTimeoutMs));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "AIC3204 write failed reg=0x%02x value=0x%02x: %s", register_address, value, esp_err_to_name(result));
    return false;
  }
  return true;
}

bool aic_select_page(uint8_t page) {
  return aic_write_byte(kAicPageCtrl, page);
}

bool aic_write_reg(uint8_t page, uint8_t register_address, uint8_t value) {
  return aic_select_page(page) && aic_write_byte(register_address, value);
}

uint8_t aic_volume_from_percent(int volume_percent) {
  constexpr int kMinVolume = -127;
  constexpr int kMaxVolume = 48;
  const int clamped = std::clamp(volume_percent, 0, 100);
  const int value = kMinVolume + ((kMaxVolume - kMinVolume) * clamped) / 100;
  return static_cast<uint8_t>(static_cast<int8_t>(value));
}

bool set_codec_volume(int volume_percent) {
  const uint8_t register_value = aic_volume_from_percent(volume_percent);
  return aic_write_reg(0, kAicDaclVolD, register_value) && aic_write_reg(0, kAicDacrVolD, register_value);
}

bool set_codec_muted(bool muted) {
  return aic_write_reg(0, kAicDacChSet2, muted ? 0x0C : 0x00);
}

bool ensure_codec_ready() {
  if (g_aic_ready) {
    set_codec_volume(current_output_volume());
    set_codec_muted(hexe::state().muted);
    return true;
  }
  if (!ensure_aic_device()) {
    return false;
  }

  gpio_config_t amp_config = {};
  amp_config.pin_bit_mask = 1ULL << kSpeakerAmp;
  amp_config.mode = GPIO_MODE_OUTPUT;
  gpio_config(&amp_config);
  gpio_set_level(kSpeakerAmp, 1);

  if (!aic_select_page(0) ||
      !aic_write_byte(kAicSwReset, 0x01)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(10));

  const bool configured =
      aic_write_reg(0, kAicNdac, 0x82) &&
      aic_write_reg(0, kAicMdac, 0x82) &&
      aic_write_reg(0, kAicDosr, 0x80) &&
      aic_write_reg(0, kAicCodecIf, 0x30) &&
      aic_write_reg(0, kAicSclkMfp3, 0x02) &&
      aic_write_reg(0, kAicAudioIf4, 0x01) &&
      aic_write_reg(0, kAicAudioIf5, 0x01) &&
      aic_write_reg(0, kAicDacSigProc, 0x01) &&
      aic_write_reg(1, kAicLdoCtrl, 0x09) &&
      aic_write_reg(1, kAicPwrCfg, 0x08) &&
      aic_write_reg(1, kAicLdoCtrl, 0x01) &&
      aic_write_reg(1, kAicCmCtrl, 0x40) &&
      aic_write_reg(1, kAicPlayCfg1, 0x00) &&
      aic_write_reg(1, kAicPlayCfg2, 0x00) &&
      aic_write_reg(1, kAicRefStartup, 0x01) &&
      aic_write_reg(1, kAicHpStart, 0x25) &&
      aic_write_reg(1, kAicHplRoute, 0x08) &&
      aic_write_reg(1, kAicHprRoute, 0x08) &&
      aic_write_reg(1, kAicLolRoute, 0x08) &&
      aic_write_reg(1, kAicLorRoute, 0x08) &&
      aic_write_reg(1, kAicHplGain, 0x3E) &&
      aic_write_reg(1, kAicHprGain, 0x3E) &&
      aic_write_reg(1, kAicLolDrvGain, 0x00) &&
      aic_write_reg(1, kAicLorDrvGain, 0x00) &&
      aic_write_reg(1, kAicOpPwrCtrl, 0x3C);
  if (!configured) {
    return false;
  }

  vTaskDelay(pdMS_TO_TICKS(2500));
  if (!aic_write_reg(0, kAicDacChSet1, 0xD4) ||
      !set_codec_volume(current_output_volume()) ||
      !set_codec_muted(hexe::state().muted)) {
    return false;
  }

  g_aic_ready = true;
  ESP_LOGI(kTag, "Home Assistant Voice PE speaker codec initialized on AIC3204 I2C 0x18");
  return true;
}

bool ensure_codec_ready_locked() {
  if (g_codec_lock == nullptr) {
    return ensure_codec_ready();
  }
  if (xSemaphoreTake(g_codec_lock, pdMS_TO_TICKS(4000)) != pdTRUE) {
    ESP_LOGW(kTag, "Timed out waiting for Voice PE codec lock");
    return false;
  }
  const bool ready = ensure_codec_ready();
  xSemaphoreGive(g_codec_lock);
  return ready;
}

void prewarm_task(void *arg) {
  (void)arg;
  while (true) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    if (hexe::state().muted) {
      continue;
    }
    ESP_LOGI(kTag, "Prewarming Voice PE speaker codec");
    ensure_codec_ready_locked();
  }
}

bool ensure_i2s_output() {
  if (g_tx_channel == nullptr) {
    i2s_chan_config_t channel_config = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_SLAVE);
    channel_config.dma_desc_num = 6;
    channel_config.dma_frame_num = kPlaybackFrameCapacity;
    esp_err_t result = i2s_new_channel(&channel_config, &g_tx_channel, nullptr);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to create Voice PE I2S TX channel: %s", esp_err_to_name(result));
      return false;
    }

    i2s_std_config_t std_config = {};
    std_config.clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(kSpeakerSampleRate);
    std_config.slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO);
    std_config.gpio_cfg = {
        .mclk = I2S_GPIO_UNUSED,
        .bclk = kSpeakerBclk,
        .ws = kSpeakerLrclk,
        .dout = kSpeakerDout,
        .din = I2S_GPIO_UNUSED,
        .invert_flags = {},
    };

    result = i2s_channel_init_std_mode(g_tx_channel, &std_config);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to initialize Voice PE I2S TX mode: %s", esp_err_to_name(result));
      return false;
    }
  }

  if (!g_tx_enabled) {
    const esp_err_t result = i2s_channel_enable(g_tx_channel);
    if (result != ESP_OK) {
      ESP_LOGE(kTag, "Failed to enable Voice PE speaker stream: %s", esp_err_to_name(result));
      return false;
    }
    g_tx_enabled = true;
  }
  return true;
}

void disable_i2s_output() {
  if (g_tx_channel != nullptr && g_tx_enabled) {
    i2s_channel_disable(g_tx_channel);
    g_tx_enabled = false;
  }
}

bool write_i2s_frames(const int32_t *frames, size_t stereo_frame_count) {
  const size_t bytes_to_write = stereo_frame_count * 2 * sizeof(int32_t);
  size_t bytes_written = 0;
  const esp_err_t result = i2s_channel_write(g_tx_channel, frames, bytes_to_write, &bytes_written, kI2sWriteTimeoutMs);
  if (result != ESP_OK || bytes_written != bytes_to_write) {
    ESP_LOGW(
        kTag,
        "Voice PE speaker write failed: %s bytes=%u/%u",
        esp_err_to_name(result),
        static_cast<unsigned>(bytes_written),
        static_cast<unsigned>(bytes_to_write));
    return false;
  }
  return true;
}

bool flush_output_frames(std::array<int32_t, kPlaybackFrameCapacity * 2> *output_frames, size_t *queued_frames) {
  if (output_frames == nullptr || queued_frames == nullptr || *queued_frames == 0) {
    return true;
  }
  const bool written = write_i2s_frames(output_frames->data(), *queued_frames);
  *queued_frames = 0;
  return written;
}

bool write_silence_drain() {
  std::array<int32_t, kPlaybackFrameCapacity * 2> silence = {};
  size_t remaining_frames = kPlaybackDrainFrames;
  while (remaining_frames > 0 && !g_stop_requested) {
    const size_t frame_count = std::min(remaining_frames, kPlaybackFrameCapacity);
    if (!write_i2s_frames(silence.data(), frame_count)) {
      return false;
    }
    remaining_frames -= frame_count;
  }
  return !g_stop_requested;
}

int16_t pcm16_sample(const uint8_t *bytes) {
  return static_cast<int16_t>(read_le16(bytes));
}

int16_t pcm16_frame_sample(const WavView &wav, size_t frame, int channel, size_t bytes_per_source_frame) {
  const uint8_t *source = wav.pcm + (frame * bytes_per_source_frame);
  if (wav.channels == 1 || channel == 0) {
    return pcm16_sample(source);
  }
  return pcm16_sample(source + sizeof(int16_t));
}

int16_t interpolate_pcm16(int16_t first, int16_t second, uint64_t numerator, uint64_t denominator) {
  if (denominator == 0 || numerator == 0 || first == second) {
    return first;
  }
  const int64_t delta = static_cast<int64_t>(second) - static_cast<int64_t>(first);
  const int64_t scaled = static_cast<int64_t>(first) + ((delta * static_cast<int64_t>(numerator)) / static_cast<int64_t>(denominator));
  return static_cast<int16_t>(std::clamp<int64_t>(scaled, -32768, 32767));
}

bool play_wav(const std::vector<uint8_t> &audio, const PlaybackRequest &request) {
  WavView wav;
  if (!parse_wav(audio, &wav)) {
    ESP_LOGW(kTag, "TTS audio is not supported WAV PCM");
    return false;
  }
  if (wav.sample_rate <= 0) {
    ESP_LOGW(kTag, "Unsupported TTS sample rate %d for Voice PE speaker", wav.sample_rate);
    return false;
  }
  if (wav.channels <= 0 || wav.channels > 2) {
    ESP_LOGW(kTag, "Unsupported TTS channel count %d for Voice PE speaker", wav.channels);
    return false;
  }
  const size_t bytes_per_source_frame = static_cast<size_t>(wav.channels) * sizeof(int16_t);
  if (bytes_per_source_frame == 0 || wav.pcm_size < bytes_per_source_frame) {
    return false;
  }

  if (!ensure_codec_ready_locked() || !ensure_i2s_output()) {
    return false;
  }

  std::array<int32_t, kPlaybackFrameCapacity * 2> output_frames = {};
  size_t queued_frames = 0;
  bool first_frame_reported = false;
  const size_t source_frames = wav.pcm_size / bytes_per_source_frame;
  const uint64_t output_frame_count =
      (static_cast<uint64_t>(source_frames) * static_cast<uint64_t>(kSpeakerSampleRate) +
       static_cast<uint64_t>(wav.sample_rate) - 1) /
      static_cast<uint64_t>(wav.sample_rate);
  if (wav.sample_rate != kSpeakerSampleRate) {
    ESP_LOGI(kTag, "Resampling TTS WAV from %d Hz to %d Hz for Voice PE speaker", wav.sample_rate, kSpeakerSampleRate);
  }

  for (uint64_t frame = 0; frame < output_frame_count && !g_stop_requested; ++frame) {
    const uint64_t source_position = frame * static_cast<uint64_t>(wav.sample_rate);
    const size_t source_index = std::min<size_t>(
        static_cast<size_t>(source_position / static_cast<uint64_t>(kSpeakerSampleRate)),
        source_frames - 1);
    const size_t next_source_index = std::min(source_index + 1, source_frames - 1);
    const uint64_t fractional = source_position % static_cast<uint64_t>(kSpeakerSampleRate);

    const int16_t left16 = interpolate_pcm16(
        pcm16_frame_sample(wav, source_index, 0, bytes_per_source_frame),
        pcm16_frame_sample(wav, next_source_index, 0, bytes_per_source_frame),
        fractional,
        kSpeakerSampleRate);
    const int16_t right16 = interpolate_pcm16(
        pcm16_frame_sample(wav, source_index, 1, bytes_per_source_frame),
        pcm16_frame_sample(wav, next_source_index, 1, bytes_per_source_frame),
        fractional,
        kSpeakerSampleRate);
    const int32_t left32 = static_cast<int32_t>(left16) << 16;
    const int32_t right32 = static_cast<int32_t>(right16) << 16;

    output_frames[queued_frames * 2] = left32;
    output_frames[(queued_frames * 2) + 1] = right32;
    ++queued_frames;
    if (queued_frames == kPlaybackFrameCapacity) {
      if (!flush_output_frames(&output_frames, &queued_frames)) {
        disable_i2s_output();
        return false;
      }
      if (!first_frame_reported) {
        send_playback_event("tts.playback.first_audio_frame", request, nullptr, wav.pcm_size);
        first_frame_reported = true;
      }
    }
  }

  bool flushed = flush_output_frames(&output_frames, &queued_frames);
  if (flushed && output_frame_count > 0) {
    flushed = write_silence_drain();
  }
  if (flushed && !first_frame_reported && output_frame_count > 0 && !g_stop_requested) {
    send_playback_event("tts.playback.first_audio_frame", request, nullptr, wav.pcm_size);
  }
  disable_i2s_output();
  return flushed && !g_stop_requested;
}

bool write_stream_pcm(
    const WavStreamInfo &wav,
    const uint8_t *pcm,
    size_t pcm_size,
    const PlaybackRequest &request,
    PcmStreamWriter *writer) {
  if (pcm == nullptr || writer == nullptr || pcm_size == 0) {
    return true;
  }
  const size_t bytes_per_source_frame = static_cast<size_t>(wav.channels) * sizeof(int16_t);
  if (bytes_per_source_frame == 0 || pcm_size % bytes_per_source_frame != 0) {
    return false;
  }

  for (size_t offset = 0; offset < pcm_size && !g_stop_requested; offset += bytes_per_source_frame) {
    const uint8_t *source = pcm + offset;
    const int16_t left16 = pcm16_sample(source);
    const int16_t right16 = wav.channels == 1 ? left16 : pcm16_sample(source + sizeof(int16_t));
    writer->output_frames[writer->queued_frames * 2] = static_cast<int32_t>(left16) << 16;
    writer->output_frames[(writer->queued_frames * 2) + 1] = static_cast<int32_t>(right16) << 16;
    ++writer->queued_frames;
    writer->source_bytes_written += bytes_per_source_frame;

    if (writer->queued_frames == kPlaybackFrameCapacity) {
      if (!flush_output_frames(&writer->output_frames, &writer->queued_frames)) {
        return false;
      }
      if (!writer->first_frame_reported) {
        send_playback_event("tts.playback.first_audio_frame", request, nullptr, writer->source_bytes_written);
        writer->first_frame_reported = true;
      }
    }
  }
  return !g_stop_requested;
}

bool process_pending_stream_pcm(
    const WavStreamInfo &wav,
    std::vector<uint8_t> *pending,
    const PlaybackRequest &request,
    PcmStreamWriter *writer) {
  if (pending == nullptr || writer == nullptr) {
    return false;
  }
  const size_t bytes_per_source_frame = static_cast<size_t>(wav.channels) * sizeof(int16_t);
  if (bytes_per_source_frame == 0) {
    return false;
  }
  const size_t complete_bytes = (pending->size() / bytes_per_source_frame) * bytes_per_source_frame;
  if (complete_bytes == 0) {
    return true;
  }
  const bool written = write_stream_pcm(wav, pending->data(), complete_bytes, request, writer);
  pending->erase(pending->begin(), pending->begin() + static_cast<std::vector<uint8_t>::difference_type>(complete_bytes));
  return written;
}

StreamPlaybackResult stream_http_wav(const std::string &url, const PlaybackRequest &request, size_t *downloaded_bytes) {
  if (downloaded_bytes != nullptr) {
    *downloaded_bytes = 0;
  }
  if (url.empty()) {
    return StreamPlaybackResult::kFailed;
  }

  esp_http_client_config_t config = {};
  config.url = url.c_str();
  config.method = HTTP_METHOD_GET;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    ESP_LOGW(kTag, "Failed to initialize streaming TTS HTTP client");
    return StreamPlaybackResult::kFailed;
  }

  esp_err_t err = esp_http_client_open(client, 0);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Failed to open streaming TTS HTTP client: %s", esp_err_to_name(err));
    esp_http_client_cleanup(client);
    return StreamPlaybackResult::kFailed;
  }
  esp_http_client_fetch_headers(client);
  const int status_code = esp_http_client_get_status_code(client);
  if (status_code < 200 || status_code >= 300) {
    ESP_LOGW(kTag, "Streaming TTS HTTP request failed: status=%d", status_code);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return StreamPlaybackResult::kFailed;
  }

  auto read_buffer = std::make_unique<std::array<char, kHttpReadBufferBytes>>();
  auto writer = std::unique_ptr<PcmStreamWriter>(new (std::nothrow) PcmStreamWriter());
  if (read_buffer == nullptr || writer == nullptr) {
    ESP_LOGW(kTag, "Failed to allocate streaming TTS buffers");
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return StreamPlaybackResult::kFailed;
  }

  std::vector<uint8_t> header;
  std::vector<uint8_t> pending_pcm;
  header.reserve(256);
  pending_pcm.reserve(kHttpReadBufferBytes);
  WavStreamInfo wav;
  bool header_ready = false;
  bool output_ready = false;
  uint32_t data_bytes_received = 0;
  size_t total_bytes = 0;
  int idle_retries = 0;
  StreamPlaybackResult result = StreamPlaybackResult::kFailed;

  while (!g_stop_requested) {
    const int read_bytes = esp_http_client_read(client, read_buffer->data(), read_buffer->size());
    if (read_bytes < 0) {
      ESP_LOGW(kTag, "Streaming TTS HTTP read failed");
      result = StreamPlaybackResult::kFailed;
      break;
    }
    if (read_bytes == 0) {
      if (esp_http_client_is_complete_data_received(client) || (header_ready && data_bytes_received >= wav.data_size)) {
        result = header_ready ? StreamPlaybackResult::kPlayed : StreamPlaybackResult::kFailed;
        break;
      }
      if (++idle_retries > kHttpReadMaxIdleRetries) {
        ESP_LOGW(kTag, "Streaming TTS HTTP read timed out");
        result = StreamPlaybackResult::kFailed;
        break;
      }
      vTaskDelay(pdMS_TO_TICKS(kHttpReadIdleRetryDelayMs));
      continue;
    }
    idle_retries = 0;
    total_bytes += static_cast<size_t>(read_bytes);
    if (downloaded_bytes != nullptr) {
      *downloaded_bytes = total_bytes;
    }
    if (total_bytes > kMaxTtsBytes) {
      ESP_LOGW(kTag, "Streaming TTS audio exceeded size limit");
      result = StreamPlaybackResult::kFailed;
      break;
    }

    const auto *chunk = reinterpret_cast<const uint8_t *>(read_buffer->data());
    size_t chunk_size = static_cast<size_t>(read_bytes);
    if (!header_ready) {
      header.insert(header.end(), chunk, chunk + chunk_size);
      WavHeaderParseResult parsed = parse_wav_header_prefix(header, &wav);
      if (parsed == WavHeaderParseResult::kNeedMore) {
        if (header.size() > kMaxWavHeaderBytes) {
          result = StreamPlaybackResult::kFallback;
          break;
        }
        continue;
      }
      if (parsed == WavHeaderParseResult::kUnsupported || wav.sample_rate != kSpeakerSampleRate) {
        result = StreamPlaybackResult::kFallback;
        break;
      }
      header_ready = true;
      if (!ensure_codec_ready_locked() || !ensure_i2s_output()) {
        result = StreamPlaybackResult::kFailed;
        break;
      }
      output_ready = true;
      ESP_LOGI(kTag, "Streaming TTS WAV at %d Hz while downloading", wav.sample_rate);
      if (header.size() > wav.data_offset) {
        const size_t available_pcm = std::min<size_t>(header.size() - wav.data_offset, wav.data_size);
        pending_pcm.insert(pending_pcm.end(), header.data() + wav.data_offset, header.data() + wav.data_offset + available_pcm);
        data_bytes_received += static_cast<uint32_t>(available_pcm);
        if (!process_pending_stream_pcm(wav, &pending_pcm, request, writer.get())) {
          result = StreamPlaybackResult::kFailed;
          break;
        }
      }
      header.clear();
    } else if (data_bytes_received < wav.data_size) {
      const size_t remaining = static_cast<size_t>(wav.data_size - data_bytes_received);
      const size_t pcm_bytes = std::min(chunk_size, remaining);
      pending_pcm.insert(pending_pcm.end(), chunk, chunk + pcm_bytes);
      data_bytes_received += static_cast<uint32_t>(pcm_bytes);
      if (!process_pending_stream_pcm(wav, &pending_pcm, request, writer.get())) {
        result = StreamPlaybackResult::kFailed;
        break;
      }
    }

    if (header_ready && data_bytes_received >= wav.data_size) {
      result = StreamPlaybackResult::kPlayed;
      break;
    }
  }

  if (result == StreamPlaybackResult::kPlayed && !pending_pcm.empty()) {
    result = process_pending_stream_pcm(wav, &pending_pcm, request, writer.get()) && pending_pcm.empty()
                 ? StreamPlaybackResult::kPlayed
                 : StreamPlaybackResult::kFailed;
  }
  if (result == StreamPlaybackResult::kPlayed) {
    if (!flush_output_frames(&writer->output_frames, &writer->queued_frames)) {
      result = StreamPlaybackResult::kFailed;
    } else if (!write_silence_drain()) {
      result = StreamPlaybackResult::kFailed;
    } else if (!writer->first_frame_reported && writer->source_bytes_written > 0 && !g_stop_requested) {
      send_playback_event("tts.playback.first_audio_frame", request, nullptr, writer->source_bytes_written);
      writer->first_frame_reported = true;
    }
  }

  if (output_ready) {
    disable_i2s_output();
  }
  esp_http_client_close(client);
  esp_http_client_cleanup(client);
  if (g_stop_requested && result == StreamPlaybackResult::kPlayed) {
    return StreamPlaybackResult::kFailed;
  }
  return result;
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
      send_playback_event("tts.playback.failed", request, "muted");
      g_playback_active = false;
      continue;
    }

    state.phase = hexe::AppPhase::kReplying;

    std::vector<uint8_t> audio;
    const bool mic_paused = hexe::board::pause_microphone_for_playback();
    const std::string url = resolve_audio_url(request.audio_url);
    bool loaded = false;
    bool played = false;
    size_t byte_count = 0;
    if (request.file_path[0] == '\0') {
      send_playback_event("tts.playback.download_started", request);
      const StreamPlaybackResult streamed = stream_http_wav(url, request, &byte_count);
      if (streamed == StreamPlaybackResult::kPlayed) {
        loaded = true;
        played = true;
      } else if (streamed == StreamPlaybackResult::kFallback) {
        ESP_LOGI(kTag, "Falling back to full-buffer TTS playback");
        loaded = fetch_audio(url, &audio);
        played = loaded && play_wav(audio, request);
        byte_count = audio.size();
      } else {
        loaded = false;
        played = false;
      }
    } else {
      loaded = read_audio_file(request.file_path, &audio);
      played = loaded && play_wav(audio, request);
      byte_count = audio.size();
    }
    if (mic_paused) {
      hexe::board::resume_microphone_after_playback();
    }
    if (!loaded) {
      send_playback_event(
          "tts.playback.failed",
          request,
          request.file_path[0] == '\0' ? "download_failed" : "file_read_failed");
    } else if (played) {
      send_playback_event("tts.playback.completed", request, nullptr, byte_count);
    } else {
      send_playback_event("tts.playback.failed", request, g_stop_requested ? "stopped" : "playback_failed", byte_count);
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
  g_codec_lock = xSemaphoreCreateMutex();
  if (g_codec_lock == nullptr) {
    ESP_LOGE(kTag, "Failed to create Voice PE codec lock");
    return;
  }
  xTaskCreate(playback_task, "hexe_tts_vpe", kPlaybackTaskStackBytes, nullptr, kTaskPriority, &g_playback_task);
  xTaskCreate(prewarm_task, "hexe_tts_warm", kPrewarmTaskStackBytes, nullptr, kTaskPriority - 1, &g_prewarm_task);
  ESP_LOGI(kTag, "Home Assistant Voice PE TTS player initialized");
}

void prewarm_tts_output() {
  if (g_prewarm_task == nullptr || hexe::state().muted) {
    return;
  }
  xTaskNotifyGive(g_prewarm_task);
}

void handle_tts_ready(const char *stream_id, const char *content_type, const char *audio_url) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring TTS while muted");
    hexe::voice::send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "muted", 0);
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
    hexe::voice::send_tts_playback_event("tts.playback.failed", stream_id, audio_url, "missing_audio_url", 0);
    return;
  }

  g_playback_active = true;
  PlaybackRequest request = {};
  copy_field(request.stream_id, sizeof(request.stream_id), stream_id);
  copy_field(request.content_type, sizeof(request.content_type), content_type);
  copy_field(request.audio_url, sizeof(request.audio_url), audio_url);
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping TTS playback request because queue is unavailable");
    send_playback_event("tts.playback.failed", request, "queue_unavailable");
    g_playback_active = false;
    state.phase = hexe::AppPhase::kError;
  }
}

void play_sd_sound(const char *filename) {
  auto &state = hexe::state();
  if (state.muted) {
    ESP_LOGI(kTag, "Ignoring SD sound while muted");
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

  g_playback_active = true;
  if (g_playback_queue == nullptr || xQueueSend(g_playback_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "Dropping SD sound playback request because queue is unavailable");
    g_playback_active = false;
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
  if (g_aic_ready) {
    set_codec_volume(clamped);
  }
  ESP_LOGI(kTag, "Output volume set to %d%%", clamped);
}

bool tts_playback_active() {
  return g_playback_active;
}

}  // namespace hexe::voice
