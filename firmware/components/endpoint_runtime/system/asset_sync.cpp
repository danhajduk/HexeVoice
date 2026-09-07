#include "system/asset_sync.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <string>
#include <sys/stat.h>

#include "app_state.h"
#include "board/display.h"
#include "board/storage.h"
#include "cJSON.h"
#include "endpoint_config.h"
#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "psa/crypto.h"
#include "system/settings.h"

namespace {
constexpr char kTag[] = "hexe_asset_sync";
constexpr int kTaskStackBytes = 8192;
constexpr int kTaskPriority = 3;
constexpr int kWifiWaitDelayMs = 500;
constexpr int kWifiWaitMaxAttempts = 120;
constexpr int kHttpTimeoutMs = 30000;
constexpr int kReadIdleRetryDelayMs = 100;
constexpr int kReadMaxIdleRetries = 3;
constexpr size_t kMaxManifestBytes = 32 * 1024;
constexpr char kAssetsPath[] = "/sdcard/hexe/assets";
constexpr char kManifestPath[] = "/sdcard/hexe/assets/assets.json";
constexpr char kManifestTempPath[] = "/sdcard/hexe/assets/.assets.json.tmp";

TaskHandle_t g_asset_sync_task = nullptr;
bool g_active = false;
char g_status[32] = "not_started";
char g_manifest_version[64] = "";
int g_checked_count = 0;
int g_downloaded_count = 0;
int g_failed_count = 0;

void set_status(const char *status) {
  std::snprintf(g_status, sizeof(g_status), "%s", status == nullptr ? "unknown" : status);
}

const char *scheme_http() {
  return hexe::system::endpoint_use_tls() ? "https" : "http";
}

bool is_safe_filename(const char *filename) {
  if (filename == nullptr || filename[0] == '\0' || filename[0] == '.' || std::strlen(filename) >= 120) {
    return false;
  }
  for (const char *cursor = filename; *cursor != '\0'; ++cursor) {
    if (*cursor == '/' || *cursor == '\\' || static_cast<unsigned char>(*cursor) < 32) {
      return false;
    }
    if (*cursor == '.' && cursor[1] == '.') {
      return false;
    }
  }
  return true;
}

const char *destination_dir(const char *media_type) {
  if (std::strcmp(media_type, "picture") == 0) {
    return hexe::board::sd_card_pictures_path();
  }
  if (std::strcmp(media_type, "sprite") == 0) {
    return hexe::board::sd_card_sprites_path();
  }
  if (std::strcmp(media_type, "sound") == 0) {
    return hexe::board::sd_card_sounds_path();
  }
  return nullptr;
}

void bytes_to_hex(const unsigned char *bytes, size_t byte_count, char *output, size_t output_size) {
  if (output_size < (byte_count * 2) + 1) {
    if (output_size > 0) {
      output[0] = '\0';
    }
    return;
  }
  for (size_t i = 0; i < byte_count; ++i) {
    std::snprintf(output + (i * 2), output_size - (i * 2), "%02x", bytes[i]);
  }
}

bool build_url(char *output, size_t output_size, const char *suffix) {
  const int written = std::snprintf(
      output,
      output_size,
      "%s://%s:%d/firmware/assets/%s/assets/%s",
      scheme_http(),
      hexe::system::endpoint_backend_host(),
      hexe::system::endpoint_http_port(),
      hexe::config::kEndpointBoardProfile,
      suffix);
  return written > 0 && written < static_cast<int>(output_size);
}

bool build_file_url(char *output, size_t output_size, const char *media_type, const char *filename) {
  char suffix[192] = {};
  const int written = std::snprintf(suffix, sizeof(suffix), "%s/%s", media_type, filename);
  return written > 0 && written < static_cast<int>(sizeof(suffix)) && build_url(output, output_size, suffix);
}

bool ensure_directory(const char *path) {
  if (mkdir(path, 0775) == 0) {
    return true;
  }
  if (errno == EEXIST) {
    struct stat info = {};
    return stat(path, &info) == 0 && S_ISDIR(info.st_mode);
  }
  ESP_LOGW(kTag, "Could not create %s: %s", path, std::strerror(errno));
  return false;
}

bool read_http_to_string(const char *url, std::string *body) {
  if (body == nullptr) {
    return false;
  }
  body->clear();
  esp_http_client_config_t config = {};
  config.url = url;
  config.timeout_ms = kHttpTimeoutMs;
  config.keep_alive_enable = true;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    ESP_LOGW(kTag, "Could not initialize HTTP client for %s", url);
    return false;
  }

  esp_err_t err = esp_http_client_open(client, 0);
  if (err == ESP_OK) {
    const int content_length = esp_http_client_fetch_headers(client);
    const int status_code = esp_http_client_get_status_code(client);
    if (status_code < 200 || status_code >= 300) {
      ESP_LOGW(kTag, "Manifest HTTP %d for %s", status_code, url);
      err = ESP_FAIL;
    } else if (content_length > static_cast<int>(kMaxManifestBytes)) {
      ESP_LOGW(kTag, "Manifest too large: %d bytes", content_length);
      err = ESP_ERR_INVALID_SIZE;
    }
  }

  char buffer[512] = {};
  int idle_retries = 0;
  while (err == ESP_OK) {
    const int read = esp_http_client_read(client, buffer, sizeof(buffer));
    if (read == -ESP_ERR_HTTP_EAGAIN) {
      if (idle_retries++ < kReadMaxIdleRetries) {
        vTaskDelay(pdMS_TO_TICKS(kReadIdleRetryDelayMs));
        continue;
      }
      err = ESP_ERR_HTTP_EAGAIN;
      break;
    }
    if (read < 0) {
      err = ESP_FAIL;
      break;
    }
    idle_retries = 0;
    if (read == 0) {
      break;
    }
    if (body->size() + static_cast<size_t>(read) > kMaxManifestBytes) {
      err = ESP_ERR_INVALID_SIZE;
      break;
    }
    body->append(buffer, read);
  }

  esp_http_client_close(client);
  esp_http_client_cleanup(client);
  if (err != ESP_OK) {
    ESP_LOGW(kTag, "Manifest fetch failed: %s", esp_err_to_name(err));
  }
  return err == ESP_OK && !body->empty();
}

bool calculate_file_sha256(const char *path, int *size_bytes, char *sha256, size_t sha256_size) {
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }

  psa_hash_operation_t hash_op = PSA_HASH_OPERATION_INIT;
  psa_status_t hash_status = psa_crypto_init();
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_setup(&hash_op, PSA_ALG_SHA_256);
  }

  int total_read = 0;
  unsigned char buffer[1024] = {};
  while (hash_status == PSA_SUCCESS) {
    const size_t read = std::fread(buffer, 1, sizeof(buffer), file);
    if (read > 0) {
      hash_status = psa_hash_update(&hash_op, buffer, read);
      total_read += static_cast<int>(read);
    }
    if (read < sizeof(buffer)) {
      if (std::ferror(file)) {
        hash_status = PSA_ERROR_GENERIC_ERROR;
      }
      break;
    }
  }
  std::fclose(file);

  unsigned char digest[32] = {};
  size_t digest_length = 0;
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_finish(&hash_op, digest, sizeof(digest), &digest_length);
  } else {
    psa_hash_abort(&hash_op);
  }
  if (hash_status != PSA_SUCCESS || digest_length != sizeof(digest)) {
    return false;
  }

  if (size_bytes != nullptr) {
    *size_bytes = total_read;
  }
  bytes_to_hex(digest, sizeof(digest), sha256, sha256_size);
  return true;
}

bool file_current(const char *path, int expected_size, const char *expected_sha256) {
  struct stat info = {};
  if (stat(path, &info) != 0 || !S_ISREG(info.st_mode) || info.st_size != expected_size) {
    return false;
  }
  char sha256[65] = {};
  int actual_size = 0;
  return calculate_file_sha256(path, &actual_size, sha256, sizeof(sha256)) &&
         actual_size == expected_size &&
         std::strcmp(sha256, expected_sha256) == 0;
}

bool download_asset_file(const char *url, const char *final_path, const char *temp_path, int expected_size, const char *expected_sha256) {
  esp_http_client_config_t config = {};
  config.url = url;
  config.timeout_ms = kHttpTimeoutMs;
  config.keep_alive_enable = true;
  esp_http_client_handle_t client = esp_http_client_init(&config);
  if (client == nullptr) {
    return false;
  }

  FILE *file = std::fopen(temp_path, "wb");
  if (file == nullptr) {
    esp_http_client_cleanup(client);
    return false;
  }

  psa_hash_operation_t hash_op = PSA_HASH_OPERATION_INIT;
  psa_status_t hash_status = psa_crypto_init();
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_setup(&hash_op, PSA_ALG_SHA_256);
  }

  esp_err_t err = esp_http_client_open(client, 0);
  int total_read = 0;
  if (err == ESP_OK) {
    const int content_length = esp_http_client_fetch_headers(client);
    const int status_code = esp_http_client_get_status_code(client);
    if (status_code < 200 || status_code >= 300) {
      ESP_LOGW(kTag, "Asset HTTP %d for %s", status_code, url);
      err = ESP_FAIL;
    } else if (content_length >= 0 && content_length != expected_size) {
      ESP_LOGW(kTag, "Asset content length mismatch expected=%d header=%d", expected_size, content_length);
    }
  }

  char buffer[1024] = {};
  int idle_retries = 0;
  while (err == ESP_OK) {
    const int read = esp_http_client_read(client, buffer, sizeof(buffer));
    if (read == -ESP_ERR_HTTP_EAGAIN) {
      if (idle_retries++ < kReadMaxIdleRetries) {
        vTaskDelay(pdMS_TO_TICKS(kReadIdleRetryDelayMs));
        continue;
      }
      err = ESP_ERR_HTTP_EAGAIN;
      break;
    }
    if (read < 0) {
      err = ESP_FAIL;
      break;
    }
    idle_retries = 0;
    if (read == 0) {
      break;
    }
    if (std::fwrite(buffer, 1, read, file) != static_cast<size_t>(read)) {
      err = ESP_ERR_NO_MEM;
      break;
    }
    if (hash_status == PSA_SUCCESS) {
      hash_status = psa_hash_update(&hash_op, reinterpret_cast<const uint8_t *>(buffer), read);
    }
    total_read += read;
  }

  std::fclose(file);
  esp_http_client_close(client);
  esp_http_client_cleanup(client);

  unsigned char digest[32] = {};
  size_t digest_length = 0;
  if (hash_status == PSA_SUCCESS) {
    hash_status = psa_hash_finish(&hash_op, digest, sizeof(digest), &digest_length);
  } else {
    psa_hash_abort(&hash_op);
  }

  char sha256[65] = {};
  if (digest_length == sizeof(digest)) {
    bytes_to_hex(digest, sizeof(digest), sha256, sizeof(sha256));
  }
  if (err != ESP_OK || total_read != expected_size || digest_length != sizeof(digest) ||
      std::strcmp(sha256, expected_sha256) != 0) {
    std::remove(temp_path);
    ESP_LOGW(kTag, "Asset download failed url=%s err=%s bytes=%d", url, esp_err_to_name(err), total_read);
    return false;
  }

  std::remove(final_path);
  if (std::rename(temp_path, final_path) != 0) {
    std::remove(temp_path);
    ESP_LOGW(kTag, "Could not activate %s: %s", final_path, std::strerror(errno));
    return false;
  }
  return true;
}

bool write_manifest(const std::string &manifest) {
  FILE *file = std::fopen(kManifestTempPath, "wb");
  if (file == nullptr) {
    return false;
  }
  const bool ok = std::fwrite(manifest.data(), 1, manifest.size(), file) == manifest.size();
  std::fclose(file);
  if (!ok) {
    std::remove(kManifestTempPath);
    return false;
  }
  std::remove(kManifestPath);
  if (std::rename(kManifestTempPath, kManifestPath) != 0) {
    std::remove(kManifestTempPath);
    return false;
  }
  return true;
}

bool copy_json_string(cJSON *object, const char *key, char *target, size_t target_size) {
  cJSON *item = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  if (!cJSON_IsString(item) || item->valuestring == nullptr || item->valuestring[0] == '\0') {
    return false;
  }
  std::snprintf(target, target_size, "%s", item->valuestring);
  return target[0] != '\0';
}

bool copy_asset_json_string(cJSON *asset, const char *key, char *target, size_t target_size) {
  if (copy_json_string(asset, key, target, target_size)) {
    return true;
  }
  cJSON *metadata = cJSON_IsObject(asset) ? cJSON_GetObjectItem(asset, "metadata") : nullptr;
  return copy_json_string(metadata, key, target, target_size);
}

cJSON *asset_json_number(cJSON *asset, const char *key) {
  cJSON *item = cJSON_IsObject(asset) ? cJSON_GetObjectItem(asset, key) : nullptr;
  if (cJSON_IsNumber(item)) {
    return item;
  }
  cJSON *metadata = cJSON_IsObject(asset) ? cJSON_GetObjectItem(asset, "metadata") : nullptr;
  item = cJSON_IsObject(metadata) ? cJSON_GetObjectItem(metadata, key) : nullptr;
  return cJSON_IsNumber(item) ? item : nullptr;
}

void sync_assets_once() {
  g_checked_count = 0;
  g_downloaded_count = 0;
  g_failed_count = 0;
  g_manifest_version[0] = '\0';

  if (!hexe::board::sd_card_mounted()) {
    set_status("sd_card_not_mounted");
    ESP_LOGW(kTag, "Skipping asset sync; SD card is not mounted");
    return;
  }
  if (!hexe::board::ensure_sd_media_directories()) {
    set_status("mkdir_failed");
    ESP_LOGW(kTag, "Skipping asset sync; SD media directories unavailable");
    return;
  }
  if (!ensure_directory(kAssetsPath)) {
    set_status("mkdir_failed");
    ESP_LOGW(kTag, "Skipping asset sync; SD assets directory unavailable");
    return;
  }

  char manifest_url[256] = {};
  if (!build_url(manifest_url, sizeof(manifest_url), "assets.json")) {
    set_status("url_too_long");
    return;
  }

  set_status("fetching_manifest");
  std::string manifest_body;
  ESP_LOGI(kTag, "Fetching asset manifest %s", manifest_url);
  if (!read_http_to_string(manifest_url, &manifest_body)) {
    set_status("manifest_fetch_failed");
    return;
  }

  cJSON *root = cJSON_ParseWithLength(manifest_body.data(), manifest_body.size());
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    set_status("invalid_manifest");
    return;
  }

  cJSON *version = cJSON_GetObjectItem(root, "version");
  if (!cJSON_IsString(version)) {
    version = cJSON_GetObjectItem(root, "asset_library_version");
  }
  if (cJSON_IsString(version) && version->valuestring != nullptr) {
    std::snprintf(g_manifest_version, sizeof(g_manifest_version), "%s", version->valuestring);
  }

  cJSON *assets = cJSON_GetObjectItem(root, "assets");
  if (!cJSON_IsArray(assets)) {
    cJSON_Delete(root);
    set_status("invalid_manifest");
    return;
  }

  set_status("syncing");
  bool display_reload_needed = false;
  cJSON *asset = nullptr;
  cJSON_ArrayForEach(asset, assets) {
    char media_type[16] = {};
    char filename[128] = {};
    char sha256[65] = {};
    if (!copy_json_string(asset, "media_type", media_type, sizeof(media_type)) ||
        !copy_asset_json_string(asset, "sha256", sha256, sizeof(sha256))) {
      ++g_failed_count;
      continue;
    }
    if (!copy_json_string(asset, "filename", filename, sizeof(filename)) &&
        !copy_json_string(asset, "source_filename", filename, sizeof(filename))) {
      ++g_failed_count;
      continue;
    }
    cJSON *size = asset_json_number(asset, "size_bytes");
    const char *directory = destination_dir(media_type);
    if (!cJSON_IsNumber(size) || size->valueint <= 0 || directory == nullptr || !is_safe_filename(filename) ||
        std::strlen(sha256) != 64) {
      ++g_failed_count;
      continue;
    }

    ++g_checked_count;
    char final_path[256] = {};
    char temp_path[280] = {};
    char url[256] = {};
    const int final_written = std::snprintf(final_path, sizeof(final_path), "%s/%s", directory, filename);
    const int temp_written = std::snprintf(temp_path, sizeof(temp_path), "%s/.%s.tmp", directory, filename);
    if (final_written <= 0 || final_written >= static_cast<int>(sizeof(final_path)) ||
        temp_written <= 0 || temp_written >= static_cast<int>(sizeof(temp_path)) ||
        !build_file_url(url, sizeof(url), media_type, filename)) {
      ++g_failed_count;
      continue;
    }

    if (file_current(final_path, size->valueint, sha256)) {
      continue;
    }
    ESP_LOGI(kTag, "Downloading asset media_type=%s filename=%s size=%d", media_type, filename, size->valueint);
    if (download_asset_file(url, final_path, temp_path, size->valueint, sha256)) {
      ++g_downloaded_count;
      if (std::strcmp(media_type, "picture") == 0 || std::strcmp(media_type, "sprite") == 0) {
        display_reload_needed = true;
      }
    } else {
      ++g_failed_count;
    }
  }

  cJSON_Delete(root);
  if (g_failed_count == 0 && write_manifest(manifest_body)) {
    set_status(g_downloaded_count > 0 ? "updated" : "current");
  } else if (g_failed_count == 0) {
    set_status("manifest_store_failed");
  } else {
    set_status("partial");
  }
  if (display_reload_needed) {
    hexe::board::request_display_assets_reload();
  }
  ESP_LOGI(
      kTag,
      "Asset sync finished status=%s checked=%d downloaded=%d failed=%d version=%s",
      g_status,
      g_checked_count,
      g_downloaded_count,
      g_failed_count,
      g_manifest_version);
}

void asset_sync_task(void *arg) {
  (void)arg;
  g_active = true;
  set_status("waiting_for_wifi");
  int attempts = 0;
  while (!hexe::state().wifi_connected && attempts++ < kWifiWaitMaxAttempts) {
    vTaskDelay(pdMS_TO_TICKS(kWifiWaitDelayMs));
  }
  if (!hexe::state().wifi_connected) {
    set_status("wifi_unavailable");
    g_active = false;
    g_asset_sync_task = nullptr;
    vTaskDelete(nullptr);
    return;
  }

  sync_assets_once();
  g_active = false;
  g_asset_sync_task = nullptr;
  vTaskDelete(nullptr);
}
}  // namespace

namespace hexe::system {

void init_asset_sync() {
  if (g_asset_sync_task != nullptr) {
    return;
  }
  xTaskCreate(asset_sync_task, "hexe_asset_sync", kTaskStackBytes, nullptr, kTaskPriority, &g_asset_sync_task);
}

bool asset_sync_active() {
  return g_active;
}

const char *asset_sync_status() {
  return g_status;
}

const char *asset_sync_manifest_version() {
  return g_manifest_version;
}

int asset_sync_checked_count() {
  return g_checked_count;
}

int asset_sync_downloaded_count() {
  return g_downloaded_count;
}

int asset_sync_failed_count() {
  return g_failed_count;
}

}  // namespace hexe::system
