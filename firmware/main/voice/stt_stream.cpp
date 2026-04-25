#include "voice/stt_stream.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_stt";
}

namespace hexe::voice {

void init_stt_stream() {
  ESP_LOGI(kTag, "STT stream scaffold initialized");
}

}  // namespace hexe::voice
