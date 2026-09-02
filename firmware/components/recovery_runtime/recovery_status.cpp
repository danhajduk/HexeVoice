#include "recovery_status.h"

#include <cstring>
#include <cstdio>
#include <string>

#include "board_profile_pins.h"
#include "recovery_ble_provisioning.h"
#include "recovery_control.h"
#include "esp_app_desc.h"
#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "nvs_flash.h"

namespace {

constexpr char kTag[] = "hexe_recovery";
constexpr char kSchemaVersion[] = "hexe-recovery-status-v1";
constexpr char kRecoveryApiVersion[] = "hexe-recovery-api-v1";
constexpr char kApplicationType[] = "recovery";
constexpr char kEntryReason[] = "factory_or_serial";

esp_err_t g_nvs_init_result = ESP_ERR_INVALID_STATE;

const char *reset_reason_name(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_POWERON:
      return "power_on";
    case ESP_RST_EXT:
      return "external";
    case ESP_RST_SW:
      return "software";
    case ESP_RST_PANIC:
      return "panic";
    case ESP_RST_INT_WDT:
      return "interrupt_watchdog";
    case ESP_RST_TASK_WDT:
      return "task_watchdog";
    case ESP_RST_WDT:
      return "watchdog";
    case ESP_RST_DEEPSLEEP:
      return "deep_sleep";
    case ESP_RST_BROWNOUT:
      return "brownout";
    case ESP_RST_SDIO:
      return "sdio";
    default:
      return "unknown";
  }
}

const char *ota_state_name(esp_ota_img_states_t state) {
  switch (state) {
    case ESP_OTA_IMG_NEW:
      return "new";
    case ESP_OTA_IMG_PENDING_VERIFY:
      return "pending_verify";
    case ESP_OTA_IMG_VALID:
      return "valid";
    case ESP_OTA_IMG_INVALID:
      return "invalid";
    case ESP_OTA_IMG_ABORTED:
      return "aborted";
    case ESP_OTA_IMG_UNDEFINED:
      return "undefined";
    default:
      return "unknown";
  }
}

const char *partition_label(const esp_partition_t *partition) {
  return partition == nullptr ? "unknown" : partition->label;
}

uint32_t flash_size_bytes() {
  uint32_t size = 0;
  if (esp_flash_get_size(nullptr, &size) != ESP_OK) {
    return 0;
  }
  return size;
}

size_t psram_size_bytes() {
#if CONFIG_SPIRAM
  return esp_psram_is_initialized() ? esp_psram_get_size() : 0;
#else
  return 0;
#endif
}

bool psram_ready() {
#if CONFIG_SPIRAM
  return esp_psram_is_initialized();
#else
  return false;
#endif
}

std::string bool_json(bool value) {
  return value ? "true" : "false";
}

}  // namespace

namespace hexe::recovery {

void init_recovery_runtime() {
  g_nvs_init_result = nvs_flash_init();
  if (g_nvs_init_result == ESP_OK) {
    ESP_LOGI(kTag, "Recovery NVS initialized");
  } else {
    ESP_LOGW(kTag, "Recovery NVS init failed without erase: %s", esp_err_to_name(g_nvs_init_result));
  }

  ESP_LOGI(kTag, "Recovery runtime ready for board=%s schema=%s",
           hexe::board::pins::kBoardProfile,
           hexe::board::pins::kPartitionSchema);
}

std::string render_status_json() {
  const esp_app_desc_t *app = esp_app_get_description();
  const esp_partition_t *running_partition = esp_ota_get_running_partition();
  const esp_partition_t *boot_partition = esp_ota_get_boot_partition();
  esp_ota_img_states_t running_state = ESP_OTA_IMG_UNDEFINED;
  const esp_err_t running_state_result =
      running_partition == nullptr ? ESP_ERR_NOT_FOUND : esp_ota_get_state_partition(running_partition, &running_state);

  char buffer[2048];
  std::snprintf(
      buffer,
      sizeof(buffer),
      "{"
      "\"schema_version\":\"%s\","
      "\"application_type\":\"%s\","
      "\"recovery_api_version\":\"%s\","
      "\"project_name\":\"%s\","
      "\"version\":\"%s\","
      "\"board_profile\":\"%s\","
      "\"partition_schema\":\"%s\","
      "\"soc\":\"%s\","
      "\"idf_target\":\"%s\","
      "\"flash_size\":\"%s\","
      "\"psram_size\":\"%s\","
      "\"flash_size_bytes\":%lu,"
      "\"detected_psram_bytes\":%zu,"
      "\"entry_reason\":\"%s\","
      "\"reset_reason\":\"%s\","
      "\"core_required\":false,"
      "\"sd_required\":false,"
      "\"models_required\":false,"
      "\"endpoint_runtime_linked\":false,"
      "\"nvs\":{\"init_ok\":%s,\"error\":\"%s\"},"
      "\"network\":{\"mode\":\"%s\",\"ip_address\":\"%s\",\"ssid_configured\":%s,\"temporary_ap_active\":%s},"
      "\"interfaces\":{\"serial_console\":true,\"http_api\":%s,\"status_page\":%s,\"display_ui\":false,"
      "\"ble\":%s,\"ble_mode\":\"%s\",\"ble_reason\":\"%s\"},"
      "\"main_slots\":[{\"label\":\"%s\",\"selected_for_boot\":true,\"state\":\"%s\",\"state_readable\":%s}],"
      "\"actions\":{\"wifi_provisioning\":%s,\"endpoint_provisioning\":%s,\"firmware_upload\":%s,"
      "\"boot_select\":%s,\"selective_config_reset\":%s,\"ble_local_recovery_provisioning\":%s,"
      "\"ble_core_governed_provisioning\":false},"
      "\"boot\":{\"running_partition\":\"%s\",\"boot_partition\":\"%s\"}"
      "}",
      kSchemaVersion,
      kApplicationType,
      kRecoveryApiVersion,
      app->project_name,
      app->version,
      hexe::board::pins::kBoardProfile,
      hexe::board::pins::kPartitionSchema,
      hexe::board::pins::kSoc,
      hexe::board::pins::kIdfTarget,
      hexe::board::pins::kFlashSize,
      hexe::board::pins::kPsramSize,
      static_cast<unsigned long>(flash_size_bytes()),
      psram_size_bytes(),
      kEntryReason,
      reset_reason_name(esp_reset_reason()),
      bool_json(g_nvs_init_result == ESP_OK).c_str(),
      esp_err_to_name(g_nvs_init_result),
      recovery_network_mode(),
      recovery_ip_address(),
      bool_json(std::strcmp(recovery_network_mode(), "apsta") == 0 || std::strcmp(recovery_network_mode(), "sta") == 0).c_str(),
      bool_json(recovery_temporary_ap_active()).c_str(),
      bool_json(recovery_http_api_active()).c_str(),
      bool_json(recovery_http_api_active()).c_str(),
      bool_json(recovery_ble_enabled()).c_str(),
      recovery_ble_advertising() ? "local_recovery_advertising" : recovery_ble_state(),
      recovery_ble_reason(),
      partition_label(boot_partition),
      ota_state_name(running_state),
      bool_json(running_state_result == ESP_OK).c_str(),
      bool_json(recovery_wifi_recovery_enabled()).c_str(),
      bool_json(recovery_wifi_recovery_enabled()).c_str(),
      bool_json(recovery_wifi_recovery_enabled()).c_str(),
      bool_json(recovery_wifi_recovery_enabled()).c_str(),
      bool_json(recovery_wifi_recovery_enabled()).c_str(),
      bool_json(recovery_ble_enabled()).c_str(),
      partition_label(running_partition),
      partition_label(boot_partition));
  return std::string(buffer);
}

void log_recovery_status() {
  ESP_LOGI(kTag, "Recovery status: %s", render_status_json().c_str());
  ESP_LOGI(kTag, "Recovery PSRAM ready=%s", psram_ready() ? "true" : "false");
}

}  // namespace hexe::recovery
