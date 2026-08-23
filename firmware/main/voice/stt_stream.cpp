#include "voice/stt_stream.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_stt";
}

namespace hexe::voice {

void init_stt_stream() {
  ESP_LOGI(kTag, "STT decoding is backend-owned; firmware streams captured PCM");
}

const char *stt_stream_runtime_mode() {
  return "backend_pcm_stream";
}

bool stt_stream_local_decoder_available() {
  return false;
}

bool stt_stream_backend_owned() {
  return true;
}

}  // namespace hexe::voice
