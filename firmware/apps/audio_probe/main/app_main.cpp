#include "audio_probe.h"

#include "esp_app_desc.h"
#include "esp_log.h"

extern "C" void app_main(void) {
  const esp_app_desc_t *app = esp_app_get_description();
  ESP_LOGI("hexe_audio_probe", "Starting Hexe audio probe firmware version=%s project=%s", app->version, app->project_name);
  hexe::audio_probe::run();
}
