#include "voice/tts_player.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_tts";
}

namespace hexe::voice {

void init_tts_player() {
  ESP_LOGI(kTag, "TTS player scaffold initialized");
}

}  // namespace hexe::voice
