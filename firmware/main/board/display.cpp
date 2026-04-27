#include "board/display.h"

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <cmath>

#include "app_state.h"
#include "board/storage.h"
#include "cJSON.h"
#include "esp_err.h"
#include "bsp/display.h"
#include "bsp/esp-box-3.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "system/clock.h"

namespace {
constexpr char kTag[] = "hexe_display";

constexpr int kWidth = 320;
constexpr int kHeight = 240;
constexpr int kFlushRows = 16;
constexpr uint16_t kBlack = 0x0000;
constexpr size_t kFullscreenAssetBytes = kWidth * kHeight * sizeof(uint16_t);
constexpr size_t kFlushBufferBytes = kWidth * kFlushRows * sizeof(uint16_t);
constexpr size_t kMaxSpriteBytes = 512 * 1024;
constexpr size_t kMaxSceneSprites = 8;
constexpr size_t kSceneManifestBytes = 4096;
constexpr int kDefaultClockIdleTimeoutMs = 120000;
esp_lcd_panel_handle_t g_panel = nullptr;
uint16_t *g_framebuffer = nullptr;
uint16_t *g_lcd_flush_buffer = nullptr;
SemaphoreHandle_t g_lcd_flush_done = nullptr;
bool g_backlight_enabled = false;
std::atomic<bool> g_display_assets_reload_requested{false};

constexpr uint16_t swap565(uint16_t value) {
  return static_cast<uint16_t>((value >> 8) | (value << 8));
}

constexpr uint8_t expand5(uint16_t value) {
  return static_cast<uint8_t>((value << 3) | (value >> 2));
}

constexpr uint8_t expand6(uint16_t value) {
  return static_cast<uint8_t>((value << 2) | (value >> 4));
}

void set_pixel(int x, int y, uint16_t color) {
  if (g_framebuffer == nullptr || x < 0 || y < 0 || x >= kWidth || y >= kHeight) {
    return;
  }
  g_framebuffer[y * kWidth + x] = color;
}

uint16_t blend_rgb565(uint16_t source_rgb565, uint16_t destination_panel_rgb565, uint8_t alpha) {
  if (alpha == 0) {
    return destination_panel_rgb565;
  }
  if (alpha == 255) {
    return swap565(source_rgb565);
  }

  const uint16_t destination_rgb565 = swap565(destination_panel_rgb565);
  const uint8_t sr = expand5(static_cast<uint16_t>((source_rgb565 >> 11) & 0x1F));
  const uint8_t sg = expand6(static_cast<uint16_t>((source_rgb565 >> 5) & 0x3F));
  const uint8_t sb = expand5(static_cast<uint16_t>(source_rgb565 & 0x1F));
  const uint8_t dr = expand5(static_cast<uint16_t>((destination_rgb565 >> 11) & 0x1F));
  const uint8_t dg = expand6(static_cast<uint16_t>((destination_rgb565 >> 5) & 0x3F));
  const uint8_t db = expand5(static_cast<uint16_t>(destination_rgb565 & 0x1F));

  const uint8_t r = static_cast<uint8_t>(((sr * alpha) + (dr * (255 - alpha))) / 255);
  const uint8_t g = static_cast<uint8_t>(((sg * alpha) + (dg * (255 - alpha))) / 255);
  const uint8_t b = static_cast<uint8_t>(((sb * alpha) + (db * (255 - alpha))) / 255);
  return swap565(static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)));
}

void blend_pixel(int x, int y, uint16_t source_rgb565, uint8_t alpha) {
  if (g_framebuffer == nullptr || x < 0 || y < 0 || x >= kWidth || y >= kHeight) {
    return;
  }
  const int index = y * kWidth + x;
  g_framebuffer[index] = blend_rgb565(source_rgb565, g_framebuffer[index], alpha);
}

void fill_frame(uint16_t color) {
  if (g_framebuffer == nullptr) {
    return;
  }

  for (int i = 0; i < kWidth * kHeight; ++i) {
    g_framebuffer[i] = color;
  }
}

void fill_rect(int x, int y, int width, int height, uint16_t color) {
  for (int row = 0; row < height; ++row) {
    for (int col = 0; col < width; ++col) {
      set_pixel(x + col, y + row, color);
    }
  }
}

void flush_framebuffer() {
  if (g_panel == nullptr || g_framebuffer == nullptr || g_lcd_flush_buffer == nullptr) {
    return;
  }

  for (int y = 0; y < kHeight; y += kFlushRows) {
    const int rows = (y + kFlushRows) <= kHeight ? kFlushRows : (kHeight - y);
    const size_t bytes = static_cast<size_t>(kWidth) * static_cast<size_t>(rows) * sizeof(uint16_t);
    std::memcpy(g_lcd_flush_buffer, g_framebuffer + (y * kWidth), bytes);
    while (g_lcd_flush_done != nullptr && xSemaphoreTake(g_lcd_flush_done, 0) == pdTRUE) {
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, y, kWidth, y + rows, g_lcd_flush_buffer));
    if (g_lcd_flush_done != nullptr && xSemaphoreTake(g_lcd_flush_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
      ESP_LOGW(kTag, "Timed out waiting for LCD flush completion");
    }
  }
}

bool on_lcd_color_transfer_done(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx) {
  (void)panel_io;
  (void)edata;
  auto *done = static_cast<SemaphoreHandle_t *>(user_ctx);
  if (done == nullptr || *done == nullptr) {
    return false;
  }
  BaseType_t high_task_woken = pdFALSE;
  xSemaphoreGiveFromISR(*done, &high_task_woken);
  return high_task_woken == pdTRUE;
}

void draw_rect_outline(int x, int y, int width, int height, uint16_t color) {
  fill_rect(x, y, width, 1, color);
  fill_rect(x, y + height - 1, width, 1, color);
  fill_rect(x, y, 1, height, color);
  fill_rect(x + width - 1, y, 1, height, color);
}

void draw_hline(int x, int y, int width, uint16_t color) {
  fill_rect(x, y, width, 1, color);
}

int wifi_strength_bars(int rssi) {
  if (rssi >= -67) {
    return 3;
  }
  if (rssi >= -75) {
    return 2;
  }
  if (rssi >= -85) {
    return 1;
  }
  return 0;
}

void draw_wifi_icon(bool connected, int rssi) {
  if (!connected) {
    return;
  }

  constexpr int kIconX = 15;
  constexpr int kIconY = 11;
  constexpr int kIconW = 24;
  constexpr int kIconH = 18;
  const int bars = wifi_strength_bars(rssi);
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = swap565(0xFFFF);
  const uint16_t dim = swap565(0x7BEF);

  fill_rect(kIconX + 1, kIconY + 1, kIconW, kIconH, shadow);
  fill_rect(kIconX, kIconY, kIconW, kIconH, bg);
  draw_rect_outline(kIconX, kIconY, kIconW, kIconH, outline);

  const uint16_t c1 = bars >= 1 ? fg : dim;
  const uint16_t c2 = bars >= 2 ? fg : dim;
  const uint16_t c3 = bars >= 3 ? fg : dim;

  fill_rect(kIconX + 11, kIconY + 13, 2, 2, fg);

  draw_hline(kIconX + 9, kIconY + 11, 6, c1);
  set_pixel(kIconX + 8, kIconY + 12, c1);
  set_pixel(kIconX + 15, kIconY + 12, c1);

  draw_hline(kIconX + 7, kIconY + 9, 10, c2);
  set_pixel(kIconX + 6, kIconY + 10, c2);
  set_pixel(kIconX + 17, kIconY + 10, c2);

  draw_hline(kIconX + 5, kIconY + 7, 14, c3);
  set_pixel(kIconX + 4, kIconY + 8, c3);
  set_pixel(kIconX + 19, kIconY + 8, c3);
}

void draw_audio_stream_icon(bool streaming) {
  if (!streaming) {
    return;
  }

  constexpr int kIconX = 45;
  constexpr int kIconY = 11;
  constexpr int kIconW = 24;
  constexpr int kIconH = 18;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = swap565(0x07FF);
  const uint16_t pulse = ((xTaskGetTickCount() / pdMS_TO_TICKS(500)) % 2) == 0 ? swap565(0xFFFF) : fg;

  fill_rect(kIconX + 1, kIconY + 1, kIconW, kIconH, shadow);
  fill_rect(kIconX, kIconY, kIconW, kIconH, bg);
  draw_rect_outline(kIconX, kIconY, kIconW, kIconH, outline);

  fill_rect(kIconX + 6, kIconY + 5, 6, 8, fg);
  fill_rect(kIconX + 7, kIconY + 4, 4, 1, fg);
  fill_rect(kIconX + 7, kIconY + 13, 4, 1, fg);
  fill_rect(kIconX + 8, kIconY + 14, 2, 2, fg);
  draw_hline(kIconX + 5, kIconY + 16, 8, fg);

  fill_rect(kIconX + 16, kIconY + 6, 2, 7, pulse);
  fill_rect(kIconX + 20, kIconY + 4, 2, 11, pulse);
}

void draw_volume_indicator() {
  const auto &app_state = hexe::state();
  int percent = app_state.output_volume_percent;
  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  constexpr int kIconX = 233;
  constexpr int kIconY = 11;
  constexpr int kBarX = 251;
  constexpr int kBarY = 15;
  constexpr int kBarW = 54;
  constexpr int kBarH = 8;
  constexpr int kPanelW = 75;
  constexpr int kPanelH = 18;
  const int fill_width = ((kBarW - 4) * percent) / 100;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = app_state.muted ? swap565(0xF800) : swap565(0xFFFF);
  const uint16_t fill = app_state.muted ? swap565(0xF800) : swap565(0x07FF);

  fill_rect(kIconX - 2, kIconY + 1, kPanelW - 1, kPanelH, shadow);
  fill_rect(kIconX - 3, kIconY, kPanelW, kPanelH, bg);
  draw_rect_outline(kIconX - 3, kIconY, kPanelW, kPanelH, outline);

  fill_rect(kIconX, kIconY + 5, 5, 5, fg);
  fill_rect(kIconX + 5, kIconY + 3, 3, 9, fg);

  draw_rect_outline(kBarX, kBarY, kBarW, kBarH, outline);
  if (fill_width > 0) {
    fill_rect(kBarX + 2, kBarY + 2, fill_width, kBarH - 4, fill);
  }
}

enum class UiAssetId : uint8_t {
  kLogo = 0,
  kIdle,
  kListening,
  kWork,
  kThinking,
  kTalk,
  kError,
  kClock,
  kOta,
  kCount,
};

struct DisplayFrameSignature {
  hexe::AppPhase phase{hexe::AppPhase::kBooting};
  UiAssetId asset_id{UiAssetId::kLogo};
  bool wifi_connected{false};
  int wifi_bars{0};
  bool audio_streaming{false};
  int audio_pulse_phase{0};
  bool muted{false};
  int output_volume_percent{0};
  bool ota_active{false};
  int ota_progress_percent{0};
  bool media_transfer_active{false};
  int clock_tick{-1};
};

DisplayFrameSignature g_last_frame_signature = {};
bool g_last_frame_signature_valid = false;

enum class AlphaFormat : uint8_t {
  kNone = 0,
  kAlpha8,
  kAlpha1,
};

struct LayerAsset {
  uint16_t *pixels{nullptr};
  uint8_t *alpha{nullptr};
  AlphaFormat alpha_format{AlphaFormat::kNone};
  int width{0};
  int height{0};
  int x{0};
  int y{0};
  bool clip{false};
  bool transparent_enabled{false};
  uint16_t transparent_color{0};
  char path[256]{};
  char alpha_path[256]{};
};

struct ClockSceneConfig {
  bool enabled{false};
  bool date{false};
  bool frame{false};
  int cx{160};
  int cy{110};
  int hands_dx{0};
  int hands_dy{0};
  int radius{62};
  int hour_radius_percent{50};
  int minute_radius_percent{75};
  bool seconds{true};
  int second_radius_percent{82};
  uint16_t color{0xFFFF};
  uint16_t second_color{0xF800};
  int idle_timeout_ms{kDefaultClockIdleTimeoutMs};
  bool date_split{false};
  int day_x{-1};
  int day_y{202};
  int day_scale_percent{200};
  bool day_long{false};
  char day_text[16]{};
  int date_x{-1};
  int date_y{202};
  int date_scale_percent{200};
};

struct OtaProgressConfig {
  bool enabled{true};
  bool frame{true};
  bool vertical{false};
  int x{58};
  int y{205};
  int width{204};
  int height{12};
  int padding{2};
  int shadow_margin{3};
  uint16_t shadow_color{0x0000};
  uint16_t background_color{0x18C3};
  uint16_t outline_color{0xFFFF};
  uint16_t fill_color{0x07FF};
};

struct ComposedScene {
  bool loaded{false};
  char type[16]{};
  LayerAsset background;
  LayerAsset avatars[static_cast<uint8_t>(UiAssetId::kCount)];
  LayerAsset sprites[kMaxSceneSprites];
  size_t sprite_count{0};
  ClockSceneConfig clock;
  OtaProgressConfig ota_progress;
};

ComposedScene g_scene = {};

int clock_tick_signature(UiAssetId id) {
  if (id != UiAssetId::kClock || !g_scene.clock.enabled) {
    return -1;
  }
  std::tm local = {};
  if (!hexe::system::current_local_time(&local)) {
    return -1;
  }
  const int minute = (local.tm_yday * 24 * 60) + (local.tm_hour * 60) + local.tm_min;
  return g_scene.clock.seconds ? (minute * 60) + local.tm_sec : minute;
}

DisplayFrameSignature make_frame_signature(hexe::AppPhase phase, UiAssetId asset_id) {
  const auto &app_state = hexe::state();
  int volume = app_state.output_volume_percent;
  if (volume < 0) {
    volume = 0;
  } else if (volume > 100) {
    volume = 100;
  }

  int ota_progress = app_state.ota_progress_percent;
  if (ota_progress < 0) {
    ota_progress = 0;
  } else if (ota_progress > 100) {
    ota_progress = 100;
  }

  return {
      .phase = phase,
      .asset_id = asset_id,
      .wifi_connected = app_state.wifi_connected,
      .wifi_bars = wifi_strength_bars(app_state.wifi_rssi),
      .audio_streaming = app_state.audio_streaming,
      .audio_pulse_phase = app_state.audio_streaming ? static_cast<int>((xTaskGetTickCount() / pdMS_TO_TICKS(500)) % 2) : 0,
      .muted = app_state.muted,
      .output_volume_percent = volume,
      .ota_active = app_state.ota_active,
      .ota_progress_percent = ota_progress,
      .media_transfer_active = app_state.media_transfer_active,
      .clock_tick = clock_tick_signature(asset_id),
  };
}

bool same_frame_signature(const DisplayFrameSignature &left, const DisplayFrameSignature &right) {
  return left.phase == right.phase &&
      left.asset_id == right.asset_id &&
      left.wifi_connected == right.wifi_connected &&
      left.wifi_bars == right.wifi_bars &&
      left.audio_streaming == right.audio_streaming &&
      left.audio_pulse_phase == right.audio_pulse_phase &&
      left.muted == right.muted &&
      left.output_volume_percent == right.output_volume_percent &&
      left.ota_active == right.ota_active &&
      left.ota_progress_percent == right.ota_progress_percent &&
      left.media_transfer_active == right.media_transfer_active &&
      left.clock_tick == right.clock_tick;
}

bool should_render_frame(const DisplayFrameSignature &signature) {
  return !g_last_frame_signature_valid || !same_frame_signature(signature, g_last_frame_signature);
}

void remember_frame_signature(const DisplayFrameSignature &signature) {
  g_last_frame_signature = signature;
  g_last_frame_signature_valid = true;
}

UiAssetId asset_id_for_phase(hexe::AppPhase phase) {
  switch (phase) {
    case hexe::AppPhase::kBooting:
    case hexe::AppPhase::kWiFiConnecting:
      return UiAssetId::kLogo;
    case hexe::AppPhase::kIdle:
    case hexe::AppPhase::kMuted:
    case hexe::AppPhase::kTimerFinished:
      return UiAssetId::kIdle;
    case hexe::AppPhase::kListening:
      return UiAssetId::kListening;
    case hexe::AppPhase::kBackendConnecting:
    case hexe::AppPhase::kUpdating:
      return UiAssetId::kWork;
    case hexe::AppPhase::kThinking:
      return UiAssetId::kThinking;
    case hexe::AppPhase::kReplying:
      return UiAssetId::kTalk;
    case hexe::AppPhase::kError:
      return UiAssetId::kError;
  }

  return UiAssetId::kIdle;
}

bool is_safe_sprite_filename(const char *filename) {
  if (filename == nullptr || filename[0] == '\0' || filename[0] == '.' || std::strlen(filename) >= 120) {
    return false;
  }
  for (const char *cursor = filename; *cursor != '\0'; ++cursor) {
    if (*cursor == '/' || *cursor == '\\' || *cursor < 32) {
      return false;
    }
    if (*cursor == '.' && cursor[1] == '.') {
      return false;
    }
  }
  return true;
}

bool read_small_text_file(const char *path, char *buffer, size_t buffer_size) {
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }
  const size_t read_bytes = std::fread(buffer, 1, buffer_size - 1, file);
  const bool full = !std::feof(file);
  std::fclose(file);
  if (full || read_bytes == 0) {
    buffer[0] = '\0';
    return false;
  }
  buffer[read_bytes] = '\0';
  return true;
}

char *allocate_text_buffer(size_t bytes) {
  char *buffer = static_cast<char *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (buffer == nullptr) {
    buffer = static_cast<char *>(heap_caps_malloc(bytes, MALLOC_CAP_DEFAULT));
  }
  if (buffer != nullptr) {
    buffer[0] = '\0';
  }
  return buffer;
}

ComposedScene *allocate_scene_scratch() {
  ComposedScene *scene = static_cast<ComposedScene *>(heap_caps_calloc(1, sizeof(ComposedScene), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (scene == nullptr) {
    scene = static_cast<ComposedScene *>(heap_caps_calloc(1, sizeof(ComposedScene), MALLOC_CAP_DEFAULT));
  }
  return scene;
}

void free_layer_asset(LayerAsset &asset) {
  if (asset.pixels != nullptr) {
    heap_caps_free(asset.pixels);
  }
  if (asset.alpha != nullptr) {
    heap_caps_free(asset.alpha);
  }
  asset = {};
}

void free_composed_scene(ComposedScene &scene) {
  free_layer_asset(scene.background);
  for (auto &avatar : scene.avatars) {
    free_layer_asset(avatar);
  }
  for (auto &sprite : scene.sprites) {
    free_layer_asset(sprite);
  }
  scene = {};
}

bool build_media_path(char *target, size_t target_size, const char *directory, const char *filename) {
  if (!is_safe_sprite_filename(filename)) {
    return false;
  }
  const int written = std::snprintf(target, target_size, "%s/%s", directory, filename);
  return written >= 0 && written < static_cast<int>(target_size);
}

void *load_binary_asset(const char *path, size_t expected_bytes, size_t max_bytes) {
  if (expected_bytes == 0 || expected_bytes > max_bytes) {
    ESP_LOGW(kTag, "Ignoring asset %s: expected size %u is outside limit", path, static_cast<unsigned>(expected_bytes));
    return nullptr;
  }

  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Asset file not found: %s", path);
    return nullptr;
  }
  if (std::fseek(file, 0, SEEK_END) != 0) {
    std::fclose(file);
    return nullptr;
  }
  const long file_size = std::ftell(file);
  if (file_size != static_cast<long>(expected_bytes)) {
    ESP_LOGW(kTag, "Ignoring asset %s: expected %u bytes, got %ld", path, static_cast<unsigned>(expected_bytes), file_size);
    std::fclose(file);
    return nullptr;
  }
  std::rewind(file);

  void *data = heap_caps_malloc(expected_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (data == nullptr) {
    data = heap_caps_malloc(expected_bytes, MALLOC_CAP_DEFAULT);
  }
  if (data == nullptr) {
    ESP_LOGW(kTag, "Could not allocate %u bytes for %s", static_cast<unsigned>(expected_bytes), path);
    std::fclose(file);
    return nullptr;
  }

  const size_t read_bytes = std::fread(data, 1, expected_bytes, file);
  std::fclose(file);
  if (read_bytes != expected_bytes) {
    heap_caps_free(data);
    ESP_LOGW(kTag, "Could not read asset %s", path);
    return nullptr;
  }
  return data;
}

AlphaFormat parse_alpha_format(cJSON *item) {
  if (!cJSON_IsString(item) || item->valuestring == nullptr) {
    return AlphaFormat::kNone;
  }
  if (std::strcmp(item->valuestring, "alpha8") == 0) {
    return AlphaFormat::kAlpha8;
  }
  if (std::strcmp(item->valuestring, "alpha1") == 0) {
    return AlphaFormat::kAlpha1;
  }
  return AlphaFormat::kNone;
}

bool load_layer_asset(cJSON *node, const char *directory, LayerAsset &asset, bool allow_fullscreen) {
  if (!cJSON_IsObject(node)) {
    return false;
  }
  cJSON *filename_item = cJSON_GetObjectItem(node, "filename");
  const char *filename = cJSON_IsString(filename_item) ? filename_item->valuestring : nullptr;
  if (!build_media_path(asset.path, sizeof(asset.path), directory, filename)) {
    return false;
  }

  cJSON *width_item = cJSON_GetObjectItem(node, "width");
  cJSON *height_item = cJSON_GetObjectItem(node, "height");
  asset.width = cJSON_IsNumber(width_item) ? width_item->valueint : (allow_fullscreen ? kWidth : 0);
  asset.height = cJSON_IsNumber(height_item) ? height_item->valueint : (allow_fullscreen ? kHeight : 0);
  asset.x = cJSON_IsNumber(cJSON_GetObjectItem(node, "x")) ? cJSON_GetObjectItem(node, "x")->valueint : 0;
  asset.y = cJSON_IsNumber(cJSON_GetObjectItem(node, "y")) ? cJSON_GetObjectItem(node, "y")->valueint : 0;
  asset.clip = cJSON_IsBool(cJSON_GetObjectItem(node, "clip")) && cJSON_IsTrue(cJSON_GetObjectItem(node, "clip"));
  if (asset.width <= 0 || asset.height <= 0) {
    ESP_LOGW(kTag, "Ignoring layer %s: invalid size %dx%d", asset.path, asset.width, asset.height);
    return false;
  }
  if (asset.x >= kWidth || asset.y >= kHeight || asset.x + asset.width <= 0 || asset.y + asset.height <= 0) {
    ESP_LOGW(kTag, "Ignoring layer %s: geometry %dx%d at %d,%d is outside the screen", asset.path, asset.width, asset.height, asset.x, asset.y);
    return false;
  }
  if (!asset.clip && (asset.x < 0 || asset.y < 0 || asset.x + asset.width > kWidth || asset.y + asset.height > kHeight)) {
    ESP_LOGW(kTag, "Ignoring layer %s: geometry %dx%d at %d,%d requires clip=true", asset.path, asset.width, asset.height, asset.x, asset.y);
    return false;
  }

  const size_t pixel_bytes = static_cast<size_t>(asset.width) * static_cast<size_t>(asset.height) * sizeof(uint16_t);
  const size_t max_bytes = allow_fullscreen ? kFullscreenAssetBytes : kMaxSpriteBytes;
  asset.pixels = static_cast<uint16_t *>(load_binary_asset(asset.path, pixel_bytes, max_bytes));
  if (asset.pixels == nullptr) {
    return false;
  }

  cJSON *transparent_item = cJSON_GetObjectItem(node, "transparent_rgb565");
  asset.transparent_enabled = cJSON_IsNumber(transparent_item);
  asset.transparent_color = asset.transparent_enabled ? static_cast<uint16_t>(transparent_item->valueint & 0xFFFF) : 0;

  cJSON *alpha_item = cJSON_GetObjectItem(node, "alpha");
  const char *alpha_name = cJSON_IsString(alpha_item) ? alpha_item->valuestring : nullptr;
  if (alpha_name != nullptr && build_media_path(asset.alpha_path, sizeof(asset.alpha_path), directory, alpha_name)) {
    asset.alpha_format = parse_alpha_format(cJSON_GetObjectItem(node, "alpha_format"));
    const size_t pixel_count = static_cast<size_t>(asset.width) * static_cast<size_t>(asset.height);
    const size_t alpha_bytes = asset.alpha_format == AlphaFormat::kAlpha1 ? (pixel_count + 7) / 8 : pixel_count;
    if (asset.alpha_format != AlphaFormat::kNone) {
      asset.alpha = static_cast<uint8_t *>(load_binary_asset(asset.alpha_path, alpha_bytes, kMaxSpriteBytes));
      if (asset.alpha == nullptr) {
        ESP_LOGW(kTag, "Layer %s will draw without alpha mask", asset.path);
        asset.alpha_format = AlphaFormat::kNone;
      }
    }
  }

  return true;
}

uint8_t layer_alpha_at(const LayerAsset &asset, size_t pixel_index) {
  if (asset.alpha == nullptr || asset.alpha_format == AlphaFormat::kNone) {
    return 255;
  }
  if (asset.alpha_format == AlphaFormat::kAlpha1) {
    return (asset.alpha[pixel_index / 8] & (1 << (pixel_index % 8))) ? 255 : 0;
  }
  return asset.alpha[pixel_index];
}

void draw_layer_asset(const LayerAsset &asset) {
  if (asset.pixels == nullptr) {
    return;
  }
  for (int row = 0; row < asset.height; ++row) {
    for (int col = 0; col < asset.width; ++col) {
      const size_t source_index = static_cast<size_t>(row) * static_cast<size_t>(asset.width) + static_cast<size_t>(col);
      const uint16_t color = asset.pixels[source_index];
      if (asset.transparent_enabled && color == asset.transparent_color) {
        continue;
      }
      blend_pixel(asset.x + col, asset.y + row, color, layer_alpha_at(asset, source_index));
    }
  }
}

UiAssetId avatar_id_for_key(const char *key) {
  if (key == nullptr) {
    return UiAssetId::kIdle;
  }
  if (std::strcmp(key, "logo") == 0) {
    return UiAssetId::kLogo;
  }
  if (std::strcmp(key, "listening") == 0 || std::strcmp(key, "listen") == 0) {
    return UiAssetId::kListening;
  }
  if (std::strcmp(key, "work") == 0) {
    return UiAssetId::kWork;
  }
  if (std::strcmp(key, "thinking") == 0) {
    return UiAssetId::kThinking;
  }
  if (std::strcmp(key, "talk") == 0 || std::strcmp(key, "replying") == 0) {
    return UiAssetId::kTalk;
  }
  if (std::strcmp(key, "error") == 0) {
    return UiAssetId::kError;
  }
  if (std::strcmp(key, "clock") == 0) {
    return UiAssetId::kClock;
  }
  if (std::strcmp(key, "ota") == 0 || std::strcmp(key, "updating") == 0) {
    return UiAssetId::kOta;
  }
  return UiAssetId::kIdle;
}

void draw_line(int x0, int y0, int x1, int y1, uint16_t color) {
  const int dx = x1 > x0 ? x1 - x0 : x0 - x1;
  const int sx = x0 < x1 ? 1 : -1;
  const int dy = y1 > y0 ? y0 - y1 : y1 - y0;
  const int sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;
  while (true) {
    set_pixel(x0, y0, color);
    if (x0 == x1 && y0 == y1) {
      break;
    }
    const int e2 = 2 * err;
    if (e2 >= dy) {
      err += dy;
      x0 += sx;
    }
    if (e2 <= dx) {
      err += dx;
      y0 += sy;
    }
  }
}

void draw_clock_hand(int cx, int cy, int radius, int numerator, int denominator, uint16_t color, int thickness) {
  if (denominator <= 0 || radius <= 0) {
    return;
  }
  constexpr double kPi = 3.14159265358979323846;
  const double angle = (static_cast<double>(numerator) * 2.0 * kPi) / static_cast<double>(denominator);
  const int x = cx + static_cast<int>(std::lround(static_cast<double>(radius) * std::sin(angle)));
  const int y = cy - static_cast<int>(std::lround(static_cast<double>(radius) * std::cos(angle)));
  draw_line(cx, cy, x, y, color);
  if (thickness >= 2) {
    draw_line(cx + 1, cy, x + 1, y, color);
  }
  if (thickness >= 3) {
    draw_line(cx, cy + 1, x, y + 1, color);
  }
}

const uint8_t *font5x7_glyph(char ch) {
  static constexpr uint8_t kDigits[][5] = {
      {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
      {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
      {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39},
      {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
      {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E},
  };
  static constexpr uint8_t kLetters[][5] = {
      {0x7E, 0x11, 0x11, 0x11, 0x7E}, {0x7F, 0x49, 0x49, 0x49, 0x36},
      {0x3E, 0x41, 0x41, 0x41, 0x22}, {0x7F, 0x41, 0x41, 0x22, 0x1C},
      {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01},
      {0x3E, 0x41, 0x49, 0x49, 0x7A}, {0x7F, 0x08, 0x08, 0x08, 0x7F},
      {0x00, 0x41, 0x7F, 0x41, 0x00}, {0x20, 0x40, 0x41, 0x3F, 0x01},
      {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40},
      {0x7F, 0x02, 0x0C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F},
      {0x3E, 0x41, 0x41, 0x41, 0x3E}, {0x7F, 0x09, 0x09, 0x09, 0x06},
      {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46},
      {0x46, 0x49, 0x49, 0x49, 0x31}, {0x01, 0x01, 0x7F, 0x01, 0x01},
      {0x3F, 0x40, 0x40, 0x40, 0x3F}, {0x1F, 0x20, 0x40, 0x20, 0x1F},
      {0x3F, 0x40, 0x38, 0x40, 0x3F}, {0x63, 0x14, 0x08, 0x14, 0x63},
      {0x07, 0x08, 0x70, 0x08, 0x07}, {0x61, 0x51, 0x49, 0x45, 0x43},
  };
  static constexpr uint8_t kLowercase[][5] = {
      {0x20, 0x54, 0x54, 0x54, 0x78}, {0x7F, 0x48, 0x44, 0x44, 0x38},
      {0x38, 0x44, 0x44, 0x44, 0x20}, {0x38, 0x44, 0x44, 0x48, 0x7F},
      {0x38, 0x54, 0x54, 0x54, 0x18}, {0x08, 0x7E, 0x09, 0x01, 0x02},
      {0x0C, 0x52, 0x52, 0x52, 0x3E}, {0x7F, 0x08, 0x04, 0x04, 0x78},
      {0x00, 0x44, 0x7D, 0x40, 0x00}, {0x20, 0x40, 0x44, 0x3D, 0x00},
      {0x7F, 0x10, 0x28, 0x44, 0x00}, {0x00, 0x41, 0x7F, 0x40, 0x00},
      {0x7C, 0x04, 0x18, 0x04, 0x78}, {0x7C, 0x08, 0x04, 0x04, 0x78},
      {0x38, 0x44, 0x44, 0x44, 0x38}, {0x7C, 0x14, 0x14, 0x14, 0x08},
      {0x08, 0x14, 0x14, 0x18, 0x7C}, {0x7C, 0x08, 0x04, 0x04, 0x08},
      {0x48, 0x54, 0x54, 0x54, 0x20}, {0x04, 0x3F, 0x44, 0x40, 0x20},
      {0x3C, 0x40, 0x40, 0x20, 0x7C}, {0x1C, 0x20, 0x40, 0x20, 0x1C},
      {0x3C, 0x40, 0x30, 0x40, 0x3C}, {0x44, 0x28, 0x10, 0x28, 0x44},
      {0x0C, 0x50, 0x50, 0x50, 0x3C}, {0x44, 0x64, 0x54, 0x4C, 0x44},
  };
  static constexpr uint8_t kDot[5] = {0x00, 0x60, 0x60, 0x00, 0x00};
  static constexpr uint8_t kDash[5] = {0x08, 0x08, 0x08, 0x08, 0x08};

  if (ch >= '0' && ch <= '9') {
    return kDigits[ch - '0'];
  }
  if (ch >= 'A' && ch <= 'Z') {
    return kLetters[ch - 'A'];
  }
  if (ch >= 'a' && ch <= 'z') {
    return kLowercase[ch - 'a'];
  }
  if (ch == '.') {
    return kDot;
  }
  if (ch == '-') {
    return kDash;
  }
  return nullptr;
}

int scaled_units(int units, int scale_percent) {
  if (units <= 0 || scale_percent <= 0) {
    return 0;
  }
  return (units * scale_percent + 99) / 100;
}

int scaled_pixel_start(int unit, int scale_percent) {
  return (unit * scale_percent) / 100;
}

int scaled_pixel_end(int unit, int scale_percent) {
  return (unit * scale_percent) / 100;
}

int text5x7_width(const char *text, int scale_percent) {
  if (text == nullptr || text[0] == '\0') {
    return 0;
  }
  int width = 0;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    width += scaled_units(*cursor == ' ' ? 3 : 6, scale_percent);
  }
  return width > 0 ? width - scaled_units(1, scale_percent) : 0;
}

void draw_char5x7(int x, int y, char ch, int scale_percent, uint16_t color) {
  const uint8_t *glyph = font5x7_glyph(ch);
  if (glyph == nullptr || scale_percent <= 0) {
    return;
  }
  for (int col = 0; col < 5; ++col) {
    for (int row = 0; row < 7; ++row) {
      if ((glyph[col] & (1 << row)) != 0) {
        const int x0 = scaled_pixel_start(col, scale_percent);
        int x1 = scaled_pixel_end(col + 1, scale_percent);
        const int y0 = scaled_pixel_start(row, scale_percent);
        int y1 = scaled_pixel_end(row + 1, scale_percent);
        if (x1 <= x0) {
          x1 = x0 + 1;
        }
        if (y1 <= y0) {
          y1 = y0 + 1;
        }
        fill_rect(x + x0, y + y0, x1 - x0, y1 - y0, color);
      }
    }
  }
}

void draw_text5x7(int x, int y, const char *text, int scale_percent, uint16_t color) {
  if (text == nullptr || scale_percent <= 0) {
    return;
  }
  int cursor_x = x;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor != ' ') {
      draw_char5x7(cursor_x, y, *cursor, scale_percent, color);
    }
    cursor_x += scaled_units(*cursor == ' ' ? 3 : 6, scale_percent);
  }
}

void format_clock_date_parts(const std::tm &local, bool long_day, char *day, size_t day_size, char *date, size_t date_size) {
  static constexpr const char *kShortWeekdays[] = {"Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"};
  static constexpr const char *kLongWeekdays[] = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
  static constexpr const char *kMonths[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};
  if (day == nullptr || day_size == 0 || date == nullptr || date_size == 0) {
    return;
  }
  const int weekday = (local.tm_wday >= 0 && local.tm_wday < 7) ? local.tm_wday : 0;
  const int month = (local.tm_mon >= 0 && local.tm_mon < 12) ? local.tm_mon : 0;
  std::snprintf(day, day_size, "%s", long_day ? kLongWeekdays[weekday] : kShortWeekdays[weekday]);
  std::snprintf(date, date_size, "%s %d", kMonths[month], local.tm_mday);
}

void draw_clock_overlay() {
  if (!g_scene.clock.enabled) {
    return;
  }
  std::tm local = {};
  if (!hexe::system::current_local_time(&local)) {
    return;
  }
  const uint16_t color = swap565(g_scene.clock.color);
  const int hand_cx = g_scene.clock.cx + g_scene.clock.hands_dx;
  const int hand_cy = g_scene.clock.cy + g_scene.clock.hands_dy;
  if (g_scene.clock.frame) {
    draw_rect_outline(
        g_scene.clock.cx - g_scene.clock.radius,
        g_scene.clock.cy - g_scene.clock.radius,
        g_scene.clock.radius * 2,
        g_scene.clock.radius * 2,
        color);
  }
  const int hour_position = ((local.tm_hour % 12) * 3600) + (local.tm_min * 60) + local.tm_sec;
  const int minute_position = (local.tm_min * 60) + local.tm_sec;
  draw_clock_hand(hand_cx, hand_cy, (g_scene.clock.radius * g_scene.clock.hour_radius_percent) / 100, hour_position, 12 * 3600, color, 3);
  draw_clock_hand(hand_cx, hand_cy, (g_scene.clock.radius * g_scene.clock.minute_radius_percent) / 100, minute_position, 3600, color, 2);
  if (g_scene.clock.seconds) {
    draw_clock_hand(
        hand_cx,
        hand_cy,
        (g_scene.clock.radius * g_scene.clock.second_radius_percent) / 100,
        local.tm_sec,
        60,
        swap565(g_scene.clock.second_color),
        1);
  }

  if (g_scene.clock.date) {
    char day[16] = {};
    char date[16] = {};
    format_clock_date_parts(local, g_scene.clock.day_long, day, sizeof(day), date, sizeof(date));
    if (g_scene.clock.day_text[0] != '\0') {
      std::snprintf(day, sizeof(day), "%s", g_scene.clock.day_text);
    }
    if (g_scene.clock.date_split) {
      int day_x = g_scene.clock.day_x;
      if (day_x < 0) {
        day_x = (kWidth - text5x7_width(day, g_scene.clock.day_scale_percent)) / 2;
      }
      int date_x = g_scene.clock.date_x;
      if (date_x < 0) {
        date_x = (kWidth - text5x7_width(date, g_scene.clock.date_scale_percent)) / 2;
      }
      draw_text5x7(day_x, g_scene.clock.day_y, day, g_scene.clock.day_scale_percent, color);
      draw_text5x7(date_x, g_scene.clock.date_y, date, g_scene.clock.date_scale_percent, color);
    } else {
      char full_date[40] = {};
      std::snprintf(full_date, sizeof(full_date), "%s %s", day, date);
      int x = g_scene.clock.date_x;
      if (x < 0) {
        x = (kWidth - text5x7_width(full_date, g_scene.clock.date_scale_percent)) / 2;
      }
      draw_text5x7(x, g_scene.clock.date_y, full_date, g_scene.clock.date_scale_percent, color);
    }
  }
}

void draw_ota_progress() {
  if (!g_scene.ota_progress.enabled) {
    return;
  }
  const auto &app_state = hexe::state();
  int percent = app_state.ota_progress_percent;
  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  const auto &bar = g_scene.ota_progress;
  const int padding = bar.padding < 0 ? 0 : bar.padding;
  const int shadow_margin = bar.shadow_margin < 0 ? 0 : bar.shadow_margin;
  const int inner_w = bar.width - (padding * 2);
  const int inner_h = bar.height - (padding * 2);
  if (bar.width <= 0 || bar.height <= 0 || inner_w <= 0 || inner_h <= 0) {
    return;
  }

  if (bar.frame) {
    fill_rect(
        bar.x - shadow_margin,
        bar.y - shadow_margin,
        bar.width + (shadow_margin * 2),
        bar.height + (shadow_margin * 2),
        swap565(bar.shadow_color));
    fill_rect(bar.x, bar.y, bar.width, bar.height, swap565(bar.background_color));
    draw_rect_outline(bar.x, bar.y, bar.width, bar.height, swap565(bar.outline_color));
  }
  if (bar.vertical) {
    const int fill_height = (inner_h * percent) / 100;
    fill_rect(bar.x + padding, bar.y + bar.height - padding - fill_height, inner_w, fill_height, swap565(bar.fill_color));
  } else {
    const int fill_width = (inner_w * percent) / 100;
    fill_rect(bar.x + padding, bar.y + padding, fill_width, inner_h, swap565(bar.fill_color));
  }
}

bool load_composed_scene() {
  if (!hexe::board::sd_card_mounted()) {
    return false;
  }
  char manifest_path[256] = {};
  const int written = std::snprintf(manifest_path, sizeof(manifest_path), "%s/ui_manifest.json", hexe::board::sd_card_sprites_path());
  if (written < 0 || written >= static_cast<int>(sizeof(manifest_path))) {
    return false;
  }
  char *manifest = allocate_text_buffer(kSceneManifestBytes);
  if (manifest == nullptr) {
    ESP_LOGW(kTag, "Could not allocate composited UI manifest buffer");
    return false;
  }
  if (!read_small_text_file(manifest_path, manifest, kSceneManifestBytes)) {
    heap_caps_free(manifest);
    return false;
  }

  cJSON *root = cJSON_Parse(manifest);
  heap_caps_free(manifest);
  if (root == nullptr) {
    ESP_LOGW(kTag, "Invalid composited UI manifest: %s", manifest_path);
    return false;
  }

  ComposedScene *scene = allocate_scene_scratch();
  if (scene == nullptr) {
    ESP_LOGW(kTag, "Could not allocate composited UI scene scratch");
    cJSON_Delete(root);
    return false;
  }
  cJSON *type_item = cJSON_GetObjectItem(root, "type");
  std::snprintf(scene->type, sizeof(scene->type), "%s", cJSON_IsString(type_item) ? type_item->valuestring : "avatar");

  cJSON *background_item = cJSON_GetObjectItem(root, "background");
  cJSON *background_node = cJSON_CreateObject();
  if (cJSON_IsString(background_item)) {
    cJSON_AddStringToObject(background_node, "filename", background_item->valuestring);
  } else if (cJSON_IsObject(background_item)) {
    cJSON_Delete(background_node);
    background_node = cJSON_Duplicate(background_item, true);
  }
  if (background_node != nullptr && !load_layer_asset(background_node, hexe::board::sd_card_pictures_path(), scene->background, true)) {
    free_layer_asset(scene->background);
  }
  cJSON_Delete(background_node);

  cJSON *avatars = cJSON_GetObjectItem(root, "avatars");
  if (cJSON_IsObject(avatars)) {
    cJSON *avatar = nullptr;
    cJSON_ArrayForEach(avatar, avatars) {
      const UiAssetId id = avatar_id_for_key(avatar->string);
      load_layer_asset(avatar, hexe::board::sd_card_sprites_path(), scene->avatars[static_cast<uint8_t>(id)], false);
    }
  }

  cJSON *sprites = cJSON_GetObjectItem(root, "sprites");
  if (cJSON_IsArray(sprites)) {
    cJSON *sprite = nullptr;
    cJSON_ArrayForEach(sprite, sprites) {
      if (scene->sprite_count >= kMaxSceneSprites) {
        break;
      }
      if (load_layer_asset(sprite, hexe::board::sd_card_sprites_path(), scene->sprites[scene->sprite_count], false)) {
        ++scene->sprite_count;
      }
    }
  }

  cJSON *clock = cJSON_GetObjectItem(root, "clock");
  if (std::strcmp(scene->type, "clock") == 0 || cJSON_IsObject(clock)) {
    scene->clock.enabled = true;
    if (cJSON_IsObject(clock)) {
      scene->clock.cx = cJSON_IsNumber(cJSON_GetObjectItem(clock, "cx")) ? cJSON_GetObjectItem(clock, "cx")->valueint : scene->clock.cx;
      scene->clock.cy = cJSON_IsNumber(cJSON_GetObjectItem(clock, "cy")) ? cJSON_GetObjectItem(clock, "cy")->valueint : scene->clock.cy;
      scene->clock.hands_dx = cJSON_IsNumber(cJSON_GetObjectItem(clock, "hands_dx")) ? cJSON_GetObjectItem(clock, "hands_dx")->valueint : scene->clock.hands_dx;
      scene->clock.hands_dy = cJSON_IsNumber(cJSON_GetObjectItem(clock, "hands_dy")) ? cJSON_GetObjectItem(clock, "hands_dy")->valueint : scene->clock.hands_dy;
      scene->clock.radius = cJSON_IsNumber(cJSON_GetObjectItem(clock, "radius")) ? cJSON_GetObjectItem(clock, "radius")->valueint : scene->clock.radius;
      scene->clock.hour_radius_percent = cJSON_IsNumber(cJSON_GetObjectItem(clock, "hour_radius_percent")) ? cJSON_GetObjectItem(clock, "hour_radius_percent")->valueint : scene->clock.hour_radius_percent;
      scene->clock.minute_radius_percent = cJSON_IsNumber(cJSON_GetObjectItem(clock, "minute_radius_percent")) ? cJSON_GetObjectItem(clock, "minute_radius_percent")->valueint : scene->clock.minute_radius_percent;
      scene->clock.seconds = cJSON_IsBool(cJSON_GetObjectItem(clock, "seconds")) ? cJSON_IsTrue(cJSON_GetObjectItem(clock, "seconds")) : scene->clock.seconds;
      scene->clock.second_radius_percent = cJSON_IsNumber(cJSON_GetObjectItem(clock, "second_radius_percent")) ? cJSON_GetObjectItem(clock, "second_radius_percent")->valueint : scene->clock.second_radius_percent;
      scene->clock.color = cJSON_IsNumber(cJSON_GetObjectItem(clock, "color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(clock, "color_rgb565")->valueint & 0xFFFF) : scene->clock.color;
      scene->clock.second_color = cJSON_IsNumber(cJSON_GetObjectItem(clock, "second_color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(clock, "second_color_rgb565")->valueint & 0xFFFF) : scene->clock.second_color;
      cJSON *idle_timeout_ms = cJSON_GetObjectItem(clock, "idle_timeout_ms");
      scene->clock.idle_timeout_ms = cJSON_IsNumber(idle_timeout_ms) && idle_timeout_ms->valueint >= 0 ? idle_timeout_ms->valueint : scene->clock.idle_timeout_ms;
      scene->clock.frame = cJSON_IsBool(cJSON_GetObjectItem(clock, "frame")) && cJSON_IsTrue(cJSON_GetObjectItem(clock, "frame"));
      scene->clock.date = cJSON_IsBool(cJSON_GetObjectItem(clock, "date")) && cJSON_IsTrue(cJSON_GetObjectItem(clock, "date"));
      scene->clock.date_split = cJSON_IsBool(cJSON_GetObjectItem(clock, "date_split")) && cJSON_IsTrue(cJSON_GetObjectItem(clock, "date_split"));
      scene->clock.day_x = cJSON_IsNumber(cJSON_GetObjectItem(clock, "day_x")) ? cJSON_GetObjectItem(clock, "day_x")->valueint : scene->clock.day_x;
      scene->clock.day_y = cJSON_IsNumber(cJSON_GetObjectItem(clock, "day_y")) ? cJSON_GetObjectItem(clock, "day_y")->valueint : scene->clock.day_y;
      cJSON *day_scale_percent = cJSON_GetObjectItem(clock, "day_scale_percent");
      scene->clock.day_scale_percent = cJSON_IsNumber(day_scale_percent) && day_scale_percent->valueint > 0 ? day_scale_percent->valueint : scene->clock.day_scale_percent;
      cJSON *day_format = cJSON_GetObjectItem(clock, "day_format");
      scene->clock.day_long = cJSON_IsString(day_format) && day_format->valuestring != nullptr && std::strcmp(day_format->valuestring, "long") == 0;
      cJSON *day_text = cJSON_GetObjectItem(clock, "day_text");
      if (cJSON_IsString(day_text) && day_text->valuestring != nullptr) {
        std::snprintf(scene->clock.day_text, sizeof(scene->clock.day_text), "%s", day_text->valuestring);
      }
      scene->clock.date_x = cJSON_IsNumber(cJSON_GetObjectItem(clock, "date_x")) ? cJSON_GetObjectItem(clock, "date_x")->valueint : scene->clock.date_x;
      scene->clock.date_y = cJSON_IsNumber(cJSON_GetObjectItem(clock, "date_y")) ? cJSON_GetObjectItem(clock, "date_y")->valueint : scene->clock.date_y;
      cJSON *date_scale_percent = cJSON_GetObjectItem(clock, "date_scale_percent");
      scene->clock.date_scale_percent = cJSON_IsNumber(date_scale_percent) && date_scale_percent->valueint > 0 ? date_scale_percent->valueint : scene->clock.date_scale_percent;
    }
  }

  cJSON *ota_progress = cJSON_GetObjectItem(root, "ota_progress");
  if (cJSON_IsObject(ota_progress)) {
    scene->ota_progress.enabled = cJSON_IsBool(cJSON_GetObjectItem(ota_progress, "enabled")) ? cJSON_IsTrue(cJSON_GetObjectItem(ota_progress, "enabled")) : scene->ota_progress.enabled;
    scene->ota_progress.frame = cJSON_IsBool(cJSON_GetObjectItem(ota_progress, "frame")) ? cJSON_IsTrue(cJSON_GetObjectItem(ota_progress, "frame")) : scene->ota_progress.frame;
    cJSON *orientation = cJSON_GetObjectItem(ota_progress, "orientation");
    scene->ota_progress.vertical = cJSON_IsString(orientation) && orientation->valuestring != nullptr && std::strcmp(orientation->valuestring, "vertical") == 0;
    scene->ota_progress.x = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "x")) ? cJSON_GetObjectItem(ota_progress, "x")->valueint : scene->ota_progress.x;
    scene->ota_progress.y = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "y")) ? cJSON_GetObjectItem(ota_progress, "y")->valueint : scene->ota_progress.y;
    scene->ota_progress.width = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "width")) ? cJSON_GetObjectItem(ota_progress, "width")->valueint : scene->ota_progress.width;
    scene->ota_progress.height = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "height")) ? cJSON_GetObjectItem(ota_progress, "height")->valueint : scene->ota_progress.height;
    scene->ota_progress.padding = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "padding")) ? cJSON_GetObjectItem(ota_progress, "padding")->valueint : scene->ota_progress.padding;
    scene->ota_progress.shadow_margin = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "shadow_margin")) ? cJSON_GetObjectItem(ota_progress, "shadow_margin")->valueint : scene->ota_progress.shadow_margin;
    scene->ota_progress.shadow_color = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "shadow_color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(ota_progress, "shadow_color_rgb565")->valueint & 0xFFFF) : scene->ota_progress.shadow_color;
    scene->ota_progress.background_color = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "background_color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(ota_progress, "background_color_rgb565")->valueint & 0xFFFF) : scene->ota_progress.background_color;
    scene->ota_progress.outline_color = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "outline_color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(ota_progress, "outline_color_rgb565")->valueint & 0xFFFF) : scene->ota_progress.outline_color;
    scene->ota_progress.fill_color = cJSON_IsNumber(cJSON_GetObjectItem(ota_progress, "fill_color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(ota_progress, "fill_color_rgb565")->valueint & 0xFFFF) : scene->ota_progress.fill_color;
  }

  cJSON_Delete(root);
  g_scene = *scene;
  heap_caps_free(scene);
  g_scene.loaded = true;
  ESP_LOGI(
      kTag,
      "Loaded composited UI scene type=%s background=%s sprites=%u",
      g_scene.type,
      g_scene.background.pixels == nullptr ? "missing" : "loaded",
      static_cast<unsigned>(g_scene.sprite_count));
  return true;
}

bool draw_composed_scene(UiAssetId id) {
  if (!g_scene.loaded) {
    return false;
  }

  draw_layer_asset(g_scene.background);
  const LayerAsset &avatar = g_scene.avatars[static_cast<uint8_t>(id)];
  draw_layer_asset(avatar);
  if (id == UiAssetId::kClock) {
    draw_clock_overlay();
  }
  for (size_t index = 0; index < g_scene.sprite_count; ++index) {
    draw_layer_asset(g_scene.sprites[index]);
  }
  return true;
}

void load_sd_ui_assets() {
  free_composed_scene(g_scene);
  if (!hexe::board::sd_card_mounted()) {
    return;
  }
  load_composed_scene();
}

bool idle_clock_due(hexe::AppPhase phase) {
  static bool idle_tracking = false;
  static TickType_t idle_started_tick = 0;

  if (phase != hexe::AppPhase::kIdle) {
    idle_tracking = false;
    idle_started_tick = 0;
    return false;
  }

  const TickType_t now = xTaskGetTickCount();
  if (!idle_tracking) {
    idle_tracking = true;
    idle_started_tick = now;
    return false;
  }

  return (now - idle_started_tick) >= pdMS_TO_TICKS(g_scene.clock.idle_timeout_ms);
}

UiAssetId asset_id_for_display(hexe::AppPhase phase) {
  if (hexe::state().ota_active) {
    return UiAssetId::kOta;
  }
  return idle_clock_due(phase) ? UiAssetId::kClock : asset_id_for_phase(phase);
}
}

namespace hexe::board {

void init_display() {
  if (g_panel != nullptr) {
    return;
  }

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  const bsp_display_config_t display_config = {
      .max_transfer_sz = static_cast<int>(kFlushBufferBytes),
  };
  ESP_ERROR_CHECK(bsp_display_new(&display_config, &g_panel, &io_handle));
  g_lcd_flush_done = xSemaphoreCreateBinary();
  ESP_RETURN_VOID_ON_FALSE(g_lcd_flush_done != nullptr, ESP_ERR_NO_MEM, kTag, "Failed to create LCD flush semaphore");
  const esp_lcd_panel_io_callbacks_t io_callbacks = {
      .on_color_trans_done = on_lcd_color_transfer_done,
  };
  ESP_ERROR_CHECK(esp_lcd_panel_io_register_event_callbacks(io_handle, &io_callbacks, &g_lcd_flush_done));
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));
  vTaskDelay(pdMS_TO_TICKS(1));

  g_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(
      kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_framebuffer == nullptr) {
    g_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_DEFAULT));
  }
  ESP_RETURN_VOID_ON_FALSE(g_framebuffer != nullptr, ESP_ERR_NO_MEM, kTag, "Failed to allocate display framebuffer");

  g_lcd_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(kFlushBufferBytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
  ESP_RETURN_VOID_ON_FALSE(g_lcd_flush_buffer != nullptr, ESP_ERR_NO_MEM, kTag, "Failed to allocate display flush buffer");

  load_sd_ui_assets();
}

void show_black_frame() {
  if (g_panel == nullptr || g_framebuffer == nullptr) {
    return;
  }

  fill_frame(kBlack);
  flush_framebuffer();
}

void turn_on_backlight() {
  if (g_panel == nullptr || g_backlight_enabled) {
    return;
  }

  ESP_ERROR_CHECK(bsp_display_backlight_on());
  g_backlight_enabled = true;
}

void render_boot_frame(int frame, const char *build_id) {
  (void)frame;
  (void)build_id;
  if (g_panel == nullptr || g_framebuffer == nullptr) {
    return;
  }
  if (g_display_assets_reload_requested.exchange(false, std::memory_order_relaxed)) {
    load_sd_ui_assets();
    g_last_frame_signature_valid = false;
  }

  const auto phase = hexe::state().phase;
  const UiAssetId asset_id = asset_id_for_display(phase);
  const DisplayFrameSignature signature = make_frame_signature(phase, asset_id);
  if (!should_render_frame(signature)) {
    return;
  }

  if (!draw_composed_scene(asset_id)) {
    return;
  }
  draw_wifi_icon(hexe::state().wifi_connected, hexe::state().wifi_rssi);
  draw_audio_stream_icon(hexe::state().audio_streaming);
  if (phase != hexe::AppPhase::kBooting) {
    draw_volume_indicator();
  }
  if (hexe::state().ota_active) {
    draw_ota_progress();
  }
  flush_framebuffer();
  remember_frame_signature(signature);
}

bool display_ready() {
  return g_panel != nullptr && g_framebuffer != nullptr && g_lcd_flush_buffer != nullptr;
}

int display_width() {
  return kWidth;
}

int display_height() {
  return kHeight;
}

const char *display_pixel_format() {
  return "rgb565";
}

void request_display_assets_reload() {
  g_display_assets_reload_requested.store(true, std::memory_order_relaxed);
}

}  // namespace hexe::board
