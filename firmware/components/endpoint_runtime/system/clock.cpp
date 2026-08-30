#include "system/clock.h"

#include <sys/time.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

namespace {
constexpr char kTag[] = "hexe_clock";
constexpr int64_t kValidUnixSeconds = 1600000000;

portMUX_TYPE g_clock_lock = portMUX_INITIALIZER_UNLOCKED;
bool g_clock_synced = false;
int64_t g_utc_epoch_ms_at_sync = 0;
int64_t g_monotonic_us_at_sync = 0;
int32_t g_utc_offset_seconds = 0;

bool current_utc_ms(int64_t *utc_ms) {
  if (utc_ms == nullptr) {
    return false;
  }

  bool synced = false;
  int64_t epoch_ms = 0;
  int64_t monotonic_us = 0;
  portENTER_CRITICAL(&g_clock_lock);
  synced = g_clock_synced;
  epoch_ms = g_utc_epoch_ms_at_sync;
  monotonic_us = g_monotonic_us_at_sync;
  portEXIT_CRITICAL(&g_clock_lock);

  if (synced) {
    *utc_ms = epoch_ms + ((esp_timer_get_time() - monotonic_us) / 1000);
    return true;
  }

  const std::time_t now = std::time(nullptr);
  if (now >= kValidUnixSeconds) {
    *utc_ms = static_cast<int64_t>(now) * 1000;
    return true;
  }
  return false;
}

int32_t utc_offset_seconds() {
  int32_t offset = 0;
  portENTER_CRITICAL(&g_clock_lock);
  offset = g_utc_offset_seconds;
  portEXIT_CRITICAL(&g_clock_lock);
  return offset;
}
}  // namespace

namespace hexe::system {

void sync_clock_from_server(int64_t server_unix_ms, int32_t utc_offset_seconds, int64_t round_trip_us) {
  const int64_t corrected_utc_ms = server_unix_ms + (round_trip_us / 2000);
  const int64_t sync_monotonic_us = esp_timer_get_time();

  portENTER_CRITICAL(&g_clock_lock);
  g_clock_synced = true;
  g_utc_epoch_ms_at_sync = corrected_utc_ms;
  g_monotonic_us_at_sync = sync_monotonic_us;
  g_utc_offset_seconds = utc_offset_seconds;
  portEXIT_CRITICAL(&g_clock_lock);

  timeval tv = {};
  tv.tv_sec = static_cast<time_t>(corrected_utc_ms / 1000);
  tv.tv_usec = static_cast<suseconds_t>((corrected_utc_ms % 1000) * 1000);
  settimeofday(&tv, nullptr);

  ESP_LOGI(
      kTag,
      "Clock synchronized: utc_ms=%lld rtt_ms=%lld utc_offset_seconds=%ld",
      static_cast<long long>(corrected_utc_ms),
      static_cast<long long>(round_trip_us / 1000),
      static_cast<long>(utc_offset_seconds));
}

bool clock_synced() {
  bool synced = false;
  portENTER_CRITICAL(&g_clock_lock);
  synced = g_clock_synced;
  portEXIT_CRITICAL(&g_clock_lock);
  return synced;
}

bool current_local_time(std::tm *local_time) {
  if (local_time == nullptr) {
    return false;
  }

  int64_t utc_ms = 0;
  if (!current_utc_ms(&utc_ms)) {
    return false;
  }

  const std::time_t local_seconds = static_cast<std::time_t>((utc_ms / 1000) + utc_offset_seconds());
  gmtime_r(&local_seconds, local_time);
  return true;
}

bool current_utc_unix_ms(int64_t *utc_ms) {
  return current_utc_ms(utc_ms);
}

int current_local_minute_signature() {
  std::tm local = {};
  if (!current_local_time(&local)) {
    return -1;
  }
  return (local.tm_yday * 24 * 60) + (local.tm_hour * 60) + local.tm_min;
}

std::string current_utc_timestamp() {
  int64_t utc_ms = 0;
  std::time_t seconds = 0;
  if (current_utc_ms(&utc_ms)) {
    seconds = static_cast<std::time_t>(utc_ms / 1000);
  } else {
    seconds = static_cast<std::time_t>(esp_timer_get_time() / 1000000);
  }

  std::tm utc = {};
  gmtime_r(&seconds, &utc);
  char buffer[32];
  std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &utc);
  return std::string(buffer);
}

}  // namespace hexe::system
