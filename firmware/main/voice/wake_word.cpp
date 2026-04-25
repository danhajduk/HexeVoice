#include "voice/wake_word.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_wake";
}

namespace hexe::voice {

void init_wake_word() {
  ESP_LOGI(kTag, "Wake-word scaffold ready for OpenWakeWord and on-device modes");
}

}  // namespace hexe::voice
