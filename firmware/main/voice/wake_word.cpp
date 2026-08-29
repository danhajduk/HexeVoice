#include "voice/wake_word.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_wake";
constexpr int kWakeElectionTimeoutMs = 300;
}

namespace hexe::voice {

void init_wake_word() {
  ESP_LOGI(kTag, "Wake detection is backend-owned; on-device wake engine is intentionally disabled");
  ESP_LOGI(kTag, "Wake election candidate protocol is available with backend openWakeWord fallback");
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

bool wake_word_election_capable() {
  return true;
}

int wake_word_election_timeout_ms() {
  return kWakeElectionTimeoutMs;
}

const char *wake_word_candidate_source() {
  return "endpoint_micro_wake_word";
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
