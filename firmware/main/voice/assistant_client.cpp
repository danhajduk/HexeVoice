#include "voice/assistant_client.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_assistant";
}

namespace hexe::voice {

void init_assistant_client() {
  ESP_LOGI(kTag, "Assistant client scaffold ready for Hexe backend connection");
}

}  // namespace hexe::voice
