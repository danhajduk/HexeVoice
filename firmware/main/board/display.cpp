#include "board/display.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "app_state.h"
#include "board/storage.h"
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

void load_sd_ui_assets() {
  if (!hexe::board::sd_card_mounted()) {
    ESP_LOGI(kTag, "SD card is not mounted; drawing simple UI screens");
    return;
  }

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
    draw_ui_asset(asset_id_for_phase(phase), 255);
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
