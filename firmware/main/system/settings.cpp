#include "system/settings.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_settings";
}

namespace hexe::system {

void init_settings() {
  ESP_LOGI(kTag, "Settings scaffold initialized");
}

}  // namespace hexe::system
