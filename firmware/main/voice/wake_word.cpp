#include "voice/wake_word.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_wake";
}

namespace hexe::voice {

void init_wake_word() {
  ESP_LOGI(kTag, "Wake detection is backend-owned; on-device wake engine is intentionally disabled");
}

const char *wake_word_runtime_mode() {
  return "backend_streaming";
}

bool wake_word_on_device_available() {
  return false;
}

bool wake_word_backend_owned() {
  return true;
}

const char *playback_stop_word_runtime_mode() {
  return "unavailable";
}

bool playback_stop_word_on_device_available() {
  return false;
}

bool playback_stop_word_active() {
  return false;
}

const char *playback_stop_word_unavailable_reason() {
  return "missing_on_device_keyword_engine";
}

}  // namespace hexe::voice
