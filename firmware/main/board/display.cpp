#include "board/display.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>

#include "app_state.h"
#include "board/storage.h"
#include "cJSON.h"
#include "esp_err.h"
#include "bsp/display.h"
#include "bsp/esp-box-3.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_display";

constexpr int kWidth = 320;
constexpr int kHeight = 240;
constexpr int kFadeFrameCount = 18;
constexpr uint16_t kBlack = 0x0000;
constexpr size_t kFullscreenAssetBytes = kWidth * kHeight * sizeof(uint16_t);
constexpr size_t kMaxSpriteBytes = 512 * 1024;
constexpr size_t kMaxSceneSprites = 8;
esp_lcd_panel_handle_t g_panel = nullptr;
uint16_t *g_framebuffer = nullptr;
bool g_backlight_enabled = false;

constexpr uint16_t swap565(uint16_t value) {
  return static_cast<uint16_t>((value >> 8) | (value << 8));
}

constexpr uint8_t expand5(uint16_t value) {
  return static_cast<uint8_t>((value << 3) | (value >> 2));
}

constexpr uint8_t expand6(uint16_t value) {
  return static_cast<uint8_t>((value << 2) | (value >> 4));
}

uint16_t scale_rgb565(uint16_t color, uint16_t alpha) {
  const uint16_t r5 = static_cast<uint16_t>((color >> 11) & 0x1F);
  const uint16_t g6 = static_cast<uint16_t>((color >> 5) & 0x3F);
  const uint16_t b5 = static_cast<uint16_t>(color & 0x1F);

  const uint8_t r = static_cast<uint8_t>((expand5(r5) * alpha) / 256);
  const uint8_t g = static_cast<uint8_t>((expand6(g6) * alpha) / 256);
  const uint8_t b = static_cast<uint8_t>((expand5(b5) * alpha) / 256);

  const uint16_t scaled = static_cast<uint16_t>(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
  return swap565(scaled);
}

uint16_t ease_in_out_alpha(int frame) {
  const int clamped_frame = frame < 0 ? 0 : (frame > kFadeFrameCount ? kFadeFrameCount : frame);
  const uint32_t t = static_cast<uint32_t>((clamped_frame * 256) / kFadeFrameCount);
  const uint32_t eased = (t * t * ((3 * 256) - (2 * t))) / (256 * 256);
  return static_cast<uint16_t>(eased);
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

void draw_rect_outline(int x, int y, int width, int height, uint16_t color) {
  fill_rect(x, y, width, 1, color);
  fill_rect(x, y + height - 1, width, 1, color);
  fill_rect(x, y, 1, height, color);
  fill_rect(x + width - 1, y, 1, height, color);
}

void draw_hline(int x, int y, int width, uint16_t color) {
  fill_rect(x, y, width, 1, color);
}

void draw_ota_progress() {
  const auto &app_state = hexe::state();
  int percent = app_state.ota_progress_percent;
  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  constexpr int kBarX = 302;
  constexpr int kBarY = 42;
  constexpr int kBarW = 12;
  constexpr int kBarH = 156;
  const int fill_height = ((kBarH - 4) * percent) / 100;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0xFFFF);
  const uint16_t fill = swap565(0x07FF);

  fill_rect(kBarX - 3, kBarY - 3, kBarW + 6, kBarH + 6, shadow);
  fill_rect(kBarX, kBarY, kBarW, kBarH, bg);
  draw_rect_outline(kBarX, kBarY, kBarW, kBarH, outline);
  fill_rect(kBarX + 2, kBarY + kBarH - 2 - fill_height, kBarW - 4, fill_height, fill);
}

void blit_fullscreen_image(const uint16_t *pixels, int width, int height, uint16_t alpha) {
  for (int row = 0; row < height; ++row) {
    for (int col = 0; col < width; ++col) {
      const int index = row * width + col;
      set_pixel(col, row, scale_rgb565(pixels[index], alpha));
    }
  }
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

  constexpr int kIconX = 8;
  constexpr int kIconY = 8;
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

  constexpr int kIconX = 38;
  constexpr int kIconY = 8;
  constexpr int kIconW = 24;
  constexpr int kIconH = 18;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = swap565(0x07FF);
  const uint16_t pulse = (hexe::state().loading_frame % 24) < 12 ? swap565(0xFFFF) : fg;

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

  constexpr int kIconX = 240;
  constexpr int kIconY = 8;
  constexpr int kBarX = 262;
  constexpr int kBarY = 13;
  constexpr int kBarW = 48;
  constexpr int kBarH = 6;
  const int fill_width = ((kBarW - 4) * percent) / 100;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = app_state.muted ? swap565(0xF800) : swap565(0xFFFF);
  const uint16_t fill = app_state.muted ? swap565(0xF800) : swap565(0x07FF);

  fill_rect(kIconX - 3, kIconY - 3, 76, 18, shadow);
  fill_rect(kIconX - 2, kIconY - 4, 74, 1, bg);
  fill_rect(kIconX - 3, kIconY - 3, 76, 16, bg);
  fill_rect(kIconX - 2, kIconY + 13, 74, 1, bg);

  fill_rect(kIconX, kIconY + 5, 5, 5, fg);
  fill_rect(kIconX + 5, kIconY + 3, 3, 9, fg);
  if (percent > 0 && !app_state.muted) {
    draw_hline(kIconX + 10, kIconY + 5, 4, fg);
    draw_hline(kIconX + 10, kIconY + 9, 4, fg);
    draw_hline(kIconX + 15, kIconY + 3, 4, fg);
    draw_hline(kIconX + 15, kIconY + 11, 4, fg);
  }

  draw_rect_outline(kBarX, kBarY, kBarW, kBarH, outline);
  if (fill_width > 0) {
    fill_rect(kBarX + 2, kBarY + 2, fill_width, kBarH - 4, fill);
  }
}

struct ScreenAsset {
  const uint16_t *pixels;
  int width;
  int height;
};

enum class UiAssetId : uint8_t {
  kLogo = 0,
  kIdle,
  kListening,
  kWork,
  kThinking,
  kTalk,
  kError,
  kCount,
};

struct SdUiAsset {
  const char *label;
  const char *filenames[3];
  uint16_t *sd_pixels;
  char sd_path[256];
};

struct SpriteOverlay {
  uint16_t *pixels;
  int width;
  int height;
  int x;
  int y;
  bool transparent_enabled;
  uint16_t transparent_color;
  char path[256];
};

SpriteOverlay g_sprite_overlay = {};

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
  bool transparent_enabled{false};
  uint16_t transparent_color{0};
  char path[256]{};
  char alpha_path[256]{};
};

struct ClockSceneConfig {
  bool enabled{false};
  bool date{false};
  int cx{160};
  int cy{110};
  int radius{62};
  uint16_t color{0xFFFF};
};

struct ComposedScene {
  bool loaded{false};
  char type[16]{};
  LayerAsset background;
  LayerAsset avatars[static_cast<uint8_t>(UiAssetId::kCount)];
  LayerAsset sprites[kMaxSceneSprites];
  size_t sprite_count{0};
  ClockSceneConfig clock;
};

ComposedScene g_scene = {};

SdUiAsset g_ui_assets[] = {
    {
        "Logo",
        {"Logo 320x240.rgb565", "Logo.rgb565", nullptr},
        nullptr,
        {},
    },
    {
        "Idle",
        {"Idle.rgb565", nullptr, nullptr},
        nullptr,
        {},
    },
    {
        "Listening",
        {"Listen.rgb565", "Listening.rgb565", nullptr},
        nullptr,
        {},
    },
    {
        "Work",
        {"Work.rgb565", nullptr, nullptr},
        nullptr,
        {},
    },
    {
        "Thinking",
        {"Thinking.rgb565", nullptr, nullptr},
        nullptr,
        {},
    },
    {
        "Talk",
        {"Talk.rgb565", nullptr, nullptr},
        nullptr,
        {},
    },
    {
        "Error",
        {"Error.rgb565", nullptr, nullptr},
        nullptr,
        {},
    },
};

static_assert(
    sizeof(g_ui_assets) / sizeof(g_ui_assets[0]) == static_cast<size_t>(UiAssetId::kCount),
    "UI asset table must match UiAssetId");

SdUiAsset &ui_asset(UiAssetId id) {
  return g_ui_assets[static_cast<uint8_t>(id)];
}

ScreenAsset asset_for_id(UiAssetId id) {
  auto &asset = ui_asset(id);
  if (asset.sd_pixels != nullptr) {
    return {asset.sd_pixels, kWidth, kHeight};
  }
  return {nullptr, 0, 0};
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

struct SimpleUiStyle {
  uint16_t background;
  uint16_t accent;
  uint16_t secondary;
};

SimpleUiStyle simple_style_for_asset(UiAssetId id) {
  switch (id) {
    case UiAssetId::kLogo:
      return {0x0002, 0x07FF, 0x8410};
    case UiAssetId::kIdle:
      return {0x0000, 0x07E0, 0x39E7};
    case UiAssetId::kListening:
      return {0x0010, 0x07FF, 0xFFFF};
    case UiAssetId::kWork:
      return {0x1082, 0xFFE0, 0x7BEF};
    case UiAssetId::kThinking:
      return {0x0808, 0xF81F, 0x7BEF};
    case UiAssetId::kTalk:
      return {0x1000, 0xF800, 0xFFFF};
    case UiAssetId::kError:
      return {0x2000, 0xF800, 0xFFE0};
    case UiAssetId::kCount:
      break;
  }

  return {0x0000, 0xFFFF, 0x7BEF};
}

void draw_simple_ui_asset(UiAssetId id, uint16_t alpha) {
  const auto style = simple_style_for_asset(id);
  const uint16_t bg = scale_rgb565(style.background, alpha);
  const uint16_t accent = scale_rgb565(style.accent, alpha);
  const uint16_t secondary = scale_rgb565(style.secondary, alpha);

  fill_frame(bg);
  fill_rect(0, 0, kWidth, 8, accent);
  fill_rect(0, kHeight - 8, kWidth, 8, accent);
  fill_rect(36, 56, 248, 4, secondary);
  fill_rect(36, 180, 248, 4, secondary);
  fill_rect(64, 80, 192, 80, secondary);
  draw_rect_outline(64, 80, 192, 80, accent);

  const int pulse = hexe::state().loading_frame % 48;
  const int marker_x = 78 + ((pulse < 24 ? pulse : 47 - pulse) * 6);
  fill_rect(marker_x, 110, 28, 20, accent);

  if (id == UiAssetId::kListening || id == UiAssetId::kTalk) {
    fill_rect(122, 94, 18, 52, accent);
    fill_rect(150, 104, 18, 32, accent);
    fill_rect(178, 90, 18, 60, accent);
  } else if (id == UiAssetId::kError) {
    fill_rect(148, 94, 24, 56, accent);
    fill_rect(148, 160, 24, 12, accent);
  } else {
    fill_rect(92, 104, 136, 12, accent);
    fill_rect(112, 128, 96, 12, accent);
  }
}

void draw_ui_asset(UiAssetId id, uint16_t alpha) {
  const auto asset = asset_for_id(id);
  if (asset.pixels != nullptr) {
    blit_fullscreen_image(asset.pixels, asset.width, asset.height, alpha);
    return;
  }

  draw_simple_ui_asset(id, alpha);
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

void draw_sprite_overlay() {
  if (g_sprite_overlay.pixels == nullptr) {
    return;
  }

  for (int row = 0; row < g_sprite_overlay.height; ++row) {
    for (int col = 0; col < g_sprite_overlay.width; ++col) {
      const uint16_t color = g_sprite_overlay.pixels[row * g_sprite_overlay.width + col];
      if (g_sprite_overlay.transparent_enabled && color == g_sprite_overlay.transparent_color) {
        continue;
      }
      set_pixel(g_sprite_overlay.x + col, g_sprite_overlay.y + row, color);
    }
  }
}

bool try_load_sd_ui_asset_file(SdUiAsset &asset, const char *path) {
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }

  if (std::fseek(file, 0, SEEK_END) != 0) {
    ESP_LOGW(kTag, "Could not inspect SD UI asset %s", path);
    std::fclose(file);
    return false;
  }

  const long file_size = std::ftell(file);
  if (file_size != static_cast<long>(kFullscreenAssetBytes)) {
    ESP_LOGW(kTag, "Ignoring SD UI asset %s: expected %u bytes, got %ld", path, static_cast<unsigned>(kFullscreenAssetBytes), file_size);
    std::fclose(file);
    return false;
  }

  std::rewind(file);

  uint16_t *pixels = static_cast<uint16_t *>(heap_caps_malloc(kFullscreenAssetBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (pixels == nullptr) {
    pixels = static_cast<uint16_t *>(heap_caps_malloc(kFullscreenAssetBytes, MALLOC_CAP_DEFAULT));
  }
  if (pixels == nullptr) {
    ESP_LOGW(kTag, "Could not allocate %u bytes for SD UI asset %s", static_cast<unsigned>(kFullscreenAssetBytes), asset.label);
    std::fclose(file);
    return false;
  }

  const size_t read_bytes = std::fread(pixels, 1, kFullscreenAssetBytes, file);
  std::fclose(file);

  if (read_bytes != kFullscreenAssetBytes) {
    ESP_LOGW(kTag, "Could not read SD UI asset %s: read %u of %u bytes", path, static_cast<unsigned>(read_bytes), static_cast<unsigned>(kFullscreenAssetBytes));
    heap_caps_free(pixels);
    return false;
  }

  asset.sd_pixels = pixels;
  std::snprintf(asset.sd_path, sizeof(asset.sd_path), "%s", path);
  ESP_LOGI(kTag, "Loaded SD UI asset %s from %s", asset.label, asset.sd_path);
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

void free_layer_asset(LayerAsset &asset) {
  if (asset.pixels != nullptr) {
    heap_caps_free(asset.pixels);
  }
  if (asset.alpha != nullptr) {
    heap_caps_free(asset.alpha);
  }
  asset = {};
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
  if (asset.width <= 0 || asset.height <= 0 || asset.x < 0 || asset.y < 0 || asset.x + asset.width > kWidth || asset.y + asset.height > kHeight) {
    ESP_LOGW(kTag, "Ignoring layer %s: invalid geometry %dx%d at %d,%d", asset.path, asset.width, asset.height, asset.x, asset.y);
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

void draw_clock_hand(int cx, int cy, int radius, int numerator, int denominator, uint16_t color) {
  constexpr int kSin[] = {0, 50, 87, 100, 87, 50, 0, -50, -87, -100, -87, -50};
  constexpr int kCos[] = {100, 87, 50, 0, -50, -87, -100, -87, -50, 0, 50, 87};
  const int index = ((numerator * 12) / denominator) % 12;
  const int x = cx + (radius * kSin[index]) / 100;
  const int y = cy - (radius * kCos[index]) / 100;
  draw_line(cx, cy, x, y, color);
  draw_line(cx + 1, cy, x + 1, y, color);
}

void draw_digit_7seg(int x, int y, int digit, uint16_t color) {
  constexpr uint8_t segments[] = {
      0b0111111, 0b0000110, 0b1011011, 0b1001111, 0b1100110,
      0b1101101, 0b1111101, 0b0000111, 0b1111111, 0b1101111,
  };
  if (digit < 0 || digit > 9) {
    return;
  }
  const uint8_t mask = segments[digit];
  if (mask & 0b0000001) fill_rect(x + 2, y, 8, 2, color);
  if (mask & 0b0000010) fill_rect(x + 10, y + 2, 2, 8, color);
  if (mask & 0b0000100) fill_rect(x + 10, y + 12, 2, 8, color);
  if (mask & 0b0001000) fill_rect(x + 2, y + 20, 8, 2, color);
  if (mask & 0b0010000) fill_rect(x, y + 12, 2, 8, color);
  if (mask & 0b0100000) fill_rect(x, y + 2, 2, 8, color);
  if (mask & 0b1000000) fill_rect(x + 2, y + 10, 8, 2, color);
}

void draw_clock_overlay() {
  if (!g_scene.clock.enabled) {
    return;
  }
  const std::time_t now = std::time(nullptr);
  std::tm local = {};
  localtime_r(&now, &local);
  const uint16_t color = swap565(g_scene.clock.color);
  draw_rect_outline(
      g_scene.clock.cx - g_scene.clock.radius,
      g_scene.clock.cy - g_scene.clock.radius,
      g_scene.clock.radius * 2,
      g_scene.clock.radius * 2,
      color);
  draw_clock_hand(g_scene.clock.cx, g_scene.clock.cy, (g_scene.clock.radius * 50) / 100, local.tm_hour % 12, 12, color);
  draw_clock_hand(g_scene.clock.cx, g_scene.clock.cy, (g_scene.clock.radius * 75) / 100, local.tm_min, 60, color);

  if (g_scene.clock.date) {
    const int month = local.tm_mon + 1;
    const int day = local.tm_mday;
    const int x = g_scene.clock.cx - 30;
    const int y = g_scene.clock.cy + g_scene.clock.radius + 12;
    draw_digit_7seg(x, y, month / 10, color);
    draw_digit_7seg(x + 14, y, month % 10, color);
    fill_rect(x + 28, y + 10, 4, 2, color);
    draw_digit_7seg(x + 36, y, day / 10, color);
    draw_digit_7seg(x + 50, y, day % 10, color);
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
  char manifest[4096] = {};
  if (!read_small_text_file(manifest_path, manifest, sizeof(manifest))) {
    ESP_LOGI(kTag, "No composited UI manifest at %s", manifest_path);
    return false;
  }

  cJSON *root = cJSON_Parse(manifest);
  if (root == nullptr) {
    ESP_LOGW(kTag, "Invalid composited UI manifest: %s", manifest_path);
    return false;
  }

  ComposedScene scene = {};
  cJSON *type_item = cJSON_GetObjectItem(root, "type");
  std::snprintf(scene.type, sizeof(scene.type), "%s", cJSON_IsString(type_item) ? type_item->valuestring : "avatar");

  cJSON *background_item = cJSON_GetObjectItem(root, "background");
  cJSON *background_node = cJSON_CreateObject();
  if (cJSON_IsString(background_item)) {
    cJSON_AddStringToObject(background_node, "filename", background_item->valuestring);
  } else if (cJSON_IsObject(background_item)) {
    cJSON_Delete(background_node);
    background_node = cJSON_Duplicate(background_item, true);
  }
  if (background_node != nullptr && !load_layer_asset(background_node, hexe::board::sd_card_pictures_path(), scene.background, true)) {
    free_layer_asset(scene.background);
  }
  cJSON_Delete(background_node);

  cJSON *avatars = cJSON_GetObjectItem(root, "avatars");
  if (cJSON_IsObject(avatars)) {
    cJSON *avatar = nullptr;
    cJSON_ArrayForEach(avatar, avatars) {
      const UiAssetId id = avatar_id_for_key(avatar->string);
      load_layer_asset(avatar, hexe::board::sd_card_sprites_path(), scene.avatars[static_cast<uint8_t>(id)], false);
    }
  }

  cJSON *sprites = cJSON_GetObjectItem(root, "sprites");
  if (cJSON_IsArray(sprites)) {
    cJSON *sprite = nullptr;
    cJSON_ArrayForEach(sprite, sprites) {
      if (scene.sprite_count >= kMaxSceneSprites) {
        break;
      }
      if (load_layer_asset(sprite, hexe::board::sd_card_sprites_path(), scene.sprites[scene.sprite_count], false)) {
        ++scene.sprite_count;
      }
    }
  }

  cJSON *clock = cJSON_GetObjectItem(root, "clock");
  if (std::strcmp(scene.type, "clock") == 0 || cJSON_IsObject(clock)) {
    scene.clock.enabled = true;
    if (cJSON_IsObject(clock)) {
      scene.clock.cx = cJSON_IsNumber(cJSON_GetObjectItem(clock, "cx")) ? cJSON_GetObjectItem(clock, "cx")->valueint : scene.clock.cx;
      scene.clock.cy = cJSON_IsNumber(cJSON_GetObjectItem(clock, "cy")) ? cJSON_GetObjectItem(clock, "cy")->valueint : scene.clock.cy;
      scene.clock.radius = cJSON_IsNumber(cJSON_GetObjectItem(clock, "radius")) ? cJSON_GetObjectItem(clock, "radius")->valueint : scene.clock.radius;
      scene.clock.color = cJSON_IsNumber(cJSON_GetObjectItem(clock, "color_rgb565")) ? static_cast<uint16_t>(cJSON_GetObjectItem(clock, "color_rgb565")->valueint & 0xFFFF) : scene.clock.color;
      scene.clock.date = cJSON_IsBool(cJSON_GetObjectItem(clock, "date")) && cJSON_IsTrue(cJSON_GetObjectItem(clock, "date"));
    }
  }

  cJSON_Delete(root);
  if (scene.background.pixels == nullptr) {
    ESP_LOGW(kTag, "Composited UI manifest did not load a valid background");
    for (auto &avatar : scene.avatars) {
      free_layer_asset(avatar);
    }
    for (auto &sprite : scene.sprites) {
      free_layer_asset(sprite);
    }
    return false;
  }

  g_scene = scene;
  g_scene.loaded = true;
  ESP_LOGI(kTag, "Loaded composited UI scene type=%s sprites=%u", g_scene.type, static_cast<unsigned>(g_scene.sprite_count));
  return true;
}

bool draw_composed_scene(UiAssetId id) {
  if (!g_scene.loaded || g_scene.background.pixels == nullptr) {
    return false;
  }
  draw_layer_asset(g_scene.background);
  LayerAsset &avatar = g_scene.avatars[static_cast<uint8_t>(id)].pixels != nullptr
      ? g_scene.avatars[static_cast<uint8_t>(id)]
      : g_scene.avatars[static_cast<uint8_t>(UiAssetId::kIdle)];
  draw_layer_asset(avatar);
  draw_clock_overlay();
  for (size_t index = 0; index < g_scene.sprite_count; ++index) {
    draw_layer_asset(g_scene.sprites[index]);
  }
  return true;
}

bool try_load_sprite_overlay_file(
    const char *path,
    int width,
    int height,
    int x,
    int y,
    bool transparent_enabled,
    uint16_t transparent_color) {
  if (width <= 0 || height <= 0 || x < 0 || y < 0 || x + width > kWidth || y + height > kHeight) {
    ESP_LOGW(kTag, "Ignoring sprite overlay %s: invalid geometry %dx%d at %d,%d", path, width, height, x, y);
    return false;
  }

  const size_t expected_bytes = static_cast<size_t>(width) * static_cast<size_t>(height) * sizeof(uint16_t);
  if (expected_bytes == 0 || expected_bytes > kMaxSpriteBytes) {
    ESP_LOGW(kTag, "Ignoring sprite overlay %s: expected size %u is outside limit", path, static_cast<unsigned>(expected_bytes));
    return false;
  }

  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Sprite overlay file not found: %s", path);
    return false;
  }

  if (std::fseek(file, 0, SEEK_END) != 0) {
    std::fclose(file);
    return false;
  }
  const long file_size = std::ftell(file);
  if (file_size != static_cast<long>(expected_bytes)) {
    ESP_LOGW(kTag, "Ignoring sprite overlay %s: expected %u bytes, got %ld", path, static_cast<unsigned>(expected_bytes), file_size);
    std::fclose(file);
    return false;
  }
  std::rewind(file);

  uint16_t *pixels = static_cast<uint16_t *>(heap_caps_malloc(expected_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (pixels == nullptr) {
    pixels = static_cast<uint16_t *>(heap_caps_malloc(expected_bytes, MALLOC_CAP_DEFAULT));
  }
  if (pixels == nullptr) {
    ESP_LOGW(kTag, "Could not allocate %u bytes for sprite overlay", static_cast<unsigned>(expected_bytes));
    std::fclose(file);
    return false;
  }

  const size_t read_bytes = std::fread(pixels, 1, expected_bytes, file);
  std::fclose(file);
  if (read_bytes != expected_bytes) {
    heap_caps_free(pixels);
    ESP_LOGW(kTag, "Could not read sprite overlay %s", path);
    return false;
  }

  if (g_sprite_overlay.pixels != nullptr) {
    heap_caps_free(g_sprite_overlay.pixels);
  }
  g_sprite_overlay = {
      .pixels = pixels,
      .width = width,
      .height = height,
      .x = x,
      .y = y,
      .transparent_enabled = transparent_enabled,
      .transparent_color = transparent_color,
      .path = {},
  };
  std::snprintf(g_sprite_overlay.path, sizeof(g_sprite_overlay.path), "%s", path);
  ESP_LOGI(kTag, "Loaded sprite overlay from %s (%dx%d at %d,%d)", path, width, height, x, y);
  return true;
}

void load_sd_sprite_overlay() {
  if (!hexe::board::sd_card_mounted()) {
    return;
  }

  char manifest_path[256] = {};
  const int manifest_written = std::snprintf(
      manifest_path, sizeof(manifest_path), "%s/overlay.json", hexe::board::sd_card_sprites_path());
  if (manifest_written < 0 || manifest_written >= static_cast<int>(sizeof(manifest_path))) {
    ESP_LOGW(kTag, "Sprite overlay manifest path is too long");
    return;
  }

  char manifest[2048] = {};
  if (!read_small_text_file(manifest_path, manifest, sizeof(manifest))) {
    ESP_LOGI(kTag, "No sprite overlay manifest at %s", manifest_path);
    return;
  }

  cJSON *root = cJSON_Parse(manifest);
  if (root == nullptr) {
    ESP_LOGW(kTag, "Invalid sprite overlay manifest: %s", manifest_path);
    return;
  }

  cJSON *filename_item = cJSON_GetObjectItem(root, "filename");
  cJSON *width_item = cJSON_GetObjectItem(root, "width");
  cJSON *height_item = cJSON_GetObjectItem(root, "height");
  cJSON *x_item = cJSON_GetObjectItem(root, "x");
  cJSON *y_item = cJSON_GetObjectItem(root, "y");
  cJSON *transparent_item = cJSON_GetObjectItem(root, "transparent_rgb565");
  const char *filename = cJSON_IsString(filename_item) ? filename_item->valuestring : nullptr;
  if (!is_safe_sprite_filename(filename) || !cJSON_IsNumber(width_item) || !cJSON_IsNumber(height_item)) {
    ESP_LOGW(kTag, "Sprite overlay manifest is missing filename, width, or height");
    cJSON_Delete(root);
    return;
  }

  char sprite_path[256] = {};
  const int sprite_written = std::snprintf(
      sprite_path, sizeof(sprite_path), "%s/%s", hexe::board::sd_card_sprites_path(), filename);
  if (sprite_written < 0 || sprite_written >= static_cast<int>(sizeof(sprite_path))) {
    ESP_LOGW(kTag, "Sprite overlay path is too long");
    cJSON_Delete(root);
    return;
  }

  const int width = width_item->valueint;
  const int height = height_item->valueint;
  const int x = cJSON_IsNumber(x_item) ? x_item->valueint : 0;
  const int y = cJSON_IsNumber(y_item) ? y_item->valueint : 0;
  const bool transparent_enabled = cJSON_IsNumber(transparent_item);
  const uint16_t transparent_color = transparent_enabled ? static_cast<uint16_t>(transparent_item->valueint & 0xFFFF) : 0;
  try_load_sprite_overlay_file(sprite_path, width, height, x, y, transparent_enabled, transparent_color);
  cJSON_Delete(root);
}

void load_sd_ui_assets() {
  if (!hexe::board::sd_card_mounted()) {
    ESP_LOGI(kTag, "SD card is not mounted; drawing simple UI screens");
    return;
  }

  const bool composed_loaded = load_composed_scene();
  int loaded_count = 0;
  for (auto &asset : g_ui_assets) {
    if (asset.sd_pixels != nullptr) {
      ++loaded_count;
      continue;
    }

    bool loaded = false;
    for (const char *filename : asset.filenames) {
      if (filename == nullptr) {
        break;
      }

      char path[256] = {};
      const int written = std::snprintf(path, sizeof(path), "%s/%s", hexe::board::sd_card_pictures_path(), filename);
      if (written < 0 || written >= static_cast<int>(sizeof(path))) {
        ESP_LOGW(kTag, "Skipping SD UI asset %s: path too long for %s", asset.label, filename);
        continue;
      }

      if (try_load_sd_ui_asset_file(asset, path)) {
        loaded = true;
        ++loaded_count;
        break;
      }
    }

    if (!loaded) {
      ESP_LOGW(kTag, "No valid SD UI asset for %s; drawing simple screen", asset.label);
    }
  }

  ESP_LOGI(kTag, "Loaded %d/%u UI assets from SD", loaded_count, static_cast<unsigned>(UiAssetId::kCount));
  if (!composed_loaded) {
    load_sd_sprite_overlay();
  }
}
}

namespace hexe::board {

void init_display() {
  if (g_panel != nullptr) {
    return;
  }

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  const bsp_display_config_t display_config = {
      .max_transfer_sz = kWidth * kHeight * static_cast<int>(sizeof(uint16_t)),
  };
  ESP_ERROR_CHECK(bsp_display_new(&display_config, &g_panel, &io_handle));
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));

  g_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(
      kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
  ESP_RETURN_VOID_ON_FALSE(g_framebuffer != nullptr, ESP_ERR_NO_MEM, kTag, "Failed to allocate display framebuffer");

  load_sd_ui_assets();
  ESP_LOGI(kTag, "Display initialized");
}

void show_black_frame() {
  if (g_panel == nullptr || g_framebuffer == nullptr) {
    return;
  }

  fill_frame(kBlack);
  ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, 0, kWidth, kHeight, g_framebuffer));
}

void turn_on_backlight() {
  if (g_panel == nullptr || g_backlight_enabled) {
    return;
  }

  ESP_ERROR_CHECK(bsp_display_backlight_on());
  g_backlight_enabled = true;
}

void render_boot_frame(int frame, const char *build_id) {
  (void)build_id;
  if (g_panel == nullptr || g_framebuffer == nullptr) {
    return;
  }

  const auto phase = hexe::state().phase;

  if (phase == hexe::AppPhase::kBooting && frame <= kFadeFrameCount) {
    draw_ui_asset(UiAssetId::kLogo, ease_in_out_alpha(frame));
  } else {
    const UiAssetId asset_id = asset_id_for_phase(phase);
    if (!draw_composed_scene(asset_id)) {
      draw_ui_asset(asset_id, 255);
    }
  }

  if (phase != hexe::AppPhase::kBooting && !g_scene.loaded) {
    draw_sprite_overlay();
  }
  draw_wifi_icon(hexe::state().wifi_connected, hexe::state().wifi_rssi);
  draw_audio_stream_icon(hexe::state().audio_streaming);
  if (phase != hexe::AppPhase::kBooting) {
    draw_volume_indicator();
  }
  if (phase == hexe::AppPhase::kUpdating) {
    draw_ota_progress();
  }
  ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, 0, kWidth, kHeight, g_framebuffer));
}

bool display_ready() {
  return g_panel != nullptr && g_framebuffer != nullptr;
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

}  // namespace hexe::board
