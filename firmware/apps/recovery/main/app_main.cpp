#include "recovery_status.h"
#include "recovery_control.h"

#include "esp_app_desc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_recovery_main";
constexpr uint32_t kStatusLogIntervalMs = 30000;
}

extern "C" void app_main(void) {
  const esp_app_desc_t *app = esp_app_get_description();
  ESP_LOGI(kTag, "Starting Hexe recovery firmware");
  ESP_LOGI(kTag, "Recovery project=%s version=%s", app->project_name, app->version);

  hexe::recovery::init_recovery_runtime();
  hexe::recovery::init_recovery_controls();
  hexe::recovery::log_recovery_status();

  while (true) {
    vTaskDelay(pdMS_TO_TICKS(kStatusLogIntervalMs));
    hexe::recovery::log_recovery_status();
  }
}
