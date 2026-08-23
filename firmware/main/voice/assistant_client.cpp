#include "voice/assistant_client.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_assistant";
}

namespace hexe::voice {

void init_assistant_client() {
  ESP_LOGI(kTag, "Assistant turns are backend-owned; firmware consumes backend events");
}

const char *assistant_client_runtime_mode() {
  return "backend_voice_pipeline";
}

bool assistant_client_local_llm_available() {
  return false;
}

bool assistant_client_backend_owned() {
  return true;
}

}  // namespace hexe::voice
