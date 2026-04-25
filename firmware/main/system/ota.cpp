#include "system/ota.h"

#include <cstdio>
#include <cstring>

#include "app_state.h"
#include "esp_err.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_ota";
constexpr int kOtaQueueDepth = 1;
constexpr int kOtaTaskStackBytes = 8192;
constexpr int kOtaTaskPriority = 5;
constexpr int kOtaTimeoutMs = 30000;

struct OtaRequest {
  char url[256];
  char version[32];
  char sha256[65];
};

QueueHandle_t g_ota_queue = nullptr;
TaskHandle_t g_ota_task = nullptr;

void ota_task(void *arg) {
  (void)arg;
  OtaRequest request = {};
  while (true) {
    if (xQueueReceive(g_ota_queue, &request, portMAX_DELAY) != pdTRUE) {
      continue;
    }

    ESP_LOGI(kTag, "Starting OTA update version=%s url=%s", request.version, request.url);
    hexe::state().phase = hexe::AppPhase::kUpdating;

    esp_http_client_config_t http_config = {};
    http_config.url = request.url;
    http_config.timeout_ms = kOtaTimeoutMs;
    http_config.keep_alive_enable = true;

    esp_https_ota_config_t ota_config = {};
    ota_config.http_config = &http_config;

    const esp_err_t result = esp_https_ota(&ota_config);
    if (result == ESP_OK) {
      ESP_LOGI(kTag, "OTA update installed; restarting into version=%s", request.version);
      esp_restart();
    }

    ESP_LOGE(kTag, "OTA update failed: %s", esp_err_to_name(result));
    hexe::state().phase = hexe::AppPhase::kError;
  }
}
}

namespace hexe::system {

void init_ota() {
  const esp_err_t valid_result = esp_ota_mark_app_valid_cancel_rollback();
  if (valid_result != ESP_OK && valid_result != ESP_ERR_OTA_ROLLBACK_INVALID_STATE) {
    ESP_LOGW(kTag, "Failed to mark app valid: %s", esp_err_to_name(valid_result));
  }

  if (g_ota_queue != nullptr) {
    return;
  }
  g_ota_queue = xQueueCreate(kOtaQueueDepth, sizeof(OtaRequest));
  if (g_ota_queue == nullptr) {
    ESP_LOGE(kTag, "Failed to create OTA request queue");
    return;
  }
  xTaskCreate(ota_task, "hexe_ota", kOtaTaskStackBytes, nullptr, kOtaTaskPriority, &g_ota_task);
  ESP_LOGI(kTag, "OTA client initialized");
}

bool start_ota_update(const char *url, const char *version, const char *sha256) {
  if (g_ota_queue == nullptr || url == nullptr || url[0] == '\0') {
    return false;
  }

  OtaRequest request = {};
  std::snprintf(request.url, sizeof(request.url), "%s", url);
  std::snprintf(request.version, sizeof(request.version), "%s", version == nullptr ? "unknown" : version);
  std::snprintf(request.sha256, sizeof(request.sha256), "%s", sha256 == nullptr ? "" : sha256);

  if (request.sha256[0] != '\0') {
    ESP_LOGI(kTag, "OTA manifest sha256=%s", request.sha256);
  }

  if (xQueueSend(g_ota_queue, &request, 0) != pdTRUE) {
    ESP_LOGW(kTag, "OTA update already queued or running");
    return false;
  }
  return true;
}

}  // namespace hexe::system
