#include "board/display.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "app_state.h"
#include "assets/error_rgb565.h"
#include "assets/idle_rgb565.h"
#include "assets/listening_rgb565.h"
#include "assets/logo_rgb565.h"
#include "assets/talk_rgb565.h"
#include "assets/thinking_rgb565.h"
#include "assets/work_rgb565.h"
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

uint8_t glyph_bits(char ch, int row) {
  static constexpr uint8_t kUnknown[7] = {0x0E, 0x11, 0x01, 0x06, 0x04, 0x00, 0x04};
  static constexpr uint8_t kSpace[7] = {};
  static constexpr uint8_t kDot[7] = {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C};
  static constexpr uint8_t kDash[7] = {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00};
  static constexpr uint8_t kPercent[7] = {0x19, 0x19, 0x02, 0x04, 0x08, 0x13, 0x13};
  static constexpr uint8_t kDigits[10][7] = {
      {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E},
      {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},
      {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F},
      {0x1E, 0x01, 0x01, 0x0E, 0x01, 0x01, 0x1E},
      {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},
      {0x1F, 0x10, 0x10, 0x1E, 0x01, 0x01, 0x1E},
      {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E},
      {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},
      {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},
      {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},
  };
  static constexpr uint8_t kF[7] = {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10};
  static constexpr uint8_t kW[7] = {0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11};
  static constexpr uint8_t kV[7] = {0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04};

  const uint8_t *glyph = kUnknown;
  if (ch == ' ') {
    glyph = kSpace;
  } else if (ch == '.') {
    glyph = kDot;
  } else if (ch == '-') {
    glyph = kDash;
  } else if (ch == '%') {
    glyph = kPercent;
  } else if (ch >= '0' && ch <= '9') {
    glyph = kDigits[ch - '0'];
  } else if (ch == 'F' || ch == 'f') {
    glyph = kF;
  } else if (ch == 'W' || ch == 'w') {
    glyph = kW;
  } else if (ch == 'V' || ch == 'v') {
    glyph = kV;
  }
  return glyph[row];
}

void draw_text(int x, int y, const char *text, uint16_t color, int scale) {
  if (text == nullptr) {
    return;
  }
  int cursor = x;
  for (const char *p = text; *p != '\0'; ++p) {
    for (int row = 0; row < 7; ++row) {
      const uint8_t bits = glyph_bits(*p, row);
      for (int col = 0; col < 5; ++col) {
        if ((bits & (1 << (4 - col))) != 0) {
          fill_rect(cursor + (col * scale), y + (row * scale), scale, scale, color);
        }
      }
    }
    cursor += 6 * scale;
  }
}

void draw_firmware_version(const char *build_id) {
  char label[48];
  std::snprintf(label, sizeof(label), "FW %s", build_id == nullptr ? "unknown" : build_id);
  const uint16_t shadow = swap565(0x0000);
  const uint16_t text = swap565(0xFFFF);
  fill_rect(88, 216, 144, 15, shadow);
  draw_text(94, 219, label, text, 1);
}

void draw_ota_progress() {
  const auto &app_state = hexe::state();
  int percent = app_state.ota_progress_percent;
  if (percent < 0) {
    percent = 0;
  } else if (percent > 100) {
    percent = 100;
  }

  constexpr int kBarX = 54;
  constexpr int kBarY = 205;
  constexpr int kBarW = 212;
  constexpr int kBarH = 16;
  const int fill_width = ((kBarW - 4) * percent) / 100;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0xFFFF);
  const uint16_t fill = swap565(0x07FF);
  const uint16_t text = swap565(0xFFFF);

  fill_rect(kBarX - 4, kBarY - 18, kBarW + 8, kBarH + 25, shadow);
  fill_rect(kBarX, kBarY, kBarW, kBarH, bg);
  draw_rect_outline(kBarX, kBarY, kBarW, kBarH, outline);
  fill_rect(kBarX + 2, kBarY + 2, fill_width, kBarH - 4, fill);

  char label[8];
  std::snprintf(label, sizeof(label), "%d%%", percent);
  const int label_width = static_cast<int>(std::strlen(label)) * 12;
  draw_text((kWidth - label_width) / 2, kBarY - 15, label, text, 2);
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
  const int bars = wifi_strength_bars(rssi);
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = swap565(0xFFFF);
  const uint16_t dim = swap565(0x7BEF);

  fill_rect(kIconX + 1, kIconY + 1, 22, 16, shadow);
  fill_rect(kIconX + 2, kIconY, 18, 1, bg);
  fill_rect(kIconX + 1, kIconY + 1, 20, 14, bg);
  fill_rect(kIconX + 2, kIconY + 15, 18, 1, bg);
  draw_rect_outline(kIconX + 1, kIconY + 1, 20, 14, outline);

  const uint16_t c1 = bars >= 1 ? fg : dim;
  const uint16_t c2 = bars >= 2 ? fg : dim;
  const uint16_t c3 = bars >= 3 ? fg : dim;

  fill_rect(kIconX + 10, kIconY + 11, 2, 2, fg);

  draw_hline(kIconX + 8, kIconY + 9, 6, c1);
  set_pixel(kIconX + 7, kIconY + 10, c1);
  set_pixel(kIconX + 14, kIconY + 10, c1);

  draw_hline(kIconX + 6, kIconY + 7, 10, c2);
  set_pixel(kIconX + 5, kIconY + 8, c2);
  set_pixel(kIconX + 16, kIconY + 8, c2);

  draw_hline(kIconX + 4, kIconY + 5, 14, c3);
  set_pixel(kIconX + 3, kIconY + 6, c3);
  set_pixel(kIconX + 18, kIconY + 6, c3);
}

void draw_audio_stream_icon(bool streaming) {
  if (!streaming) {
    return;
  }

  constexpr int kIconX = 286;
  constexpr int kIconY = 8;
  const uint16_t shadow = swap565(0x0000);
  const uint16_t bg = swap565(0x18C3);
  const uint16_t outline = swap565(0x7BEF);
  const uint16_t fg = swap565(0x07FF);
  const uint16_t pulse = (hexe::state().loading_frame % 24) < 12 ? swap565(0xFFFF) : fg;

  fill_rect(kIconX + 1, kIconY + 1, 26, 18, shadow);
  fill_rect(kIconX + 2, kIconY, 22, 1, bg);
  fill_rect(kIconX + 1, kIconY + 1, 24, 16, bg);
  fill_rect(kIconX + 2, kIconY + 17, 22, 1, bg);
  draw_rect_outline(kIconX + 1, kIconY + 1, 24, 16, outline);

  fill_rect(kIconX + 7, kIconY + 4, 6, 8, fg);
  fill_rect(kIconX + 8, kIconY + 3, 4, 1, fg);
  fill_rect(kIconX + 8, kIconY + 12, 4, 1, fg);
  fill_rect(kIconX + 9, kIconY + 13, 2, 2, fg);
  draw_hline(kIconX + 6, kIconY + 15, 8, fg);

  fill_rect(kIconX + 17, kIconY + 5, 2, 8, pulse);
  fill_rect(kIconX + 21, kIconY + 3, 2, 12, pulse);
}

struct ScreenAsset {
  const uint16_t *pixels;
  int width;
  int height;
};

ScreenAsset asset_for_phase(hexe::AppPhase phase) {
  switch (phase) {
    case hexe::AppPhase::kBooting:
    case hexe::AppPhase::kWiFiConnecting:
      return {hexe::assets::kLogoRgb565, hexe::assets::kLogoWidth, hexe::assets::kLogoHeight};
    case hexe::AppPhase::kIdle:
    case hexe::AppPhase::kMuted:
    case hexe::AppPhase::kTimerFinished:
      return {hexe::assets::kIdleRgb565, hexe::assets::kIdleWidth, hexe::assets::kIdleHeight};
    case hexe::AppPhase::kListening:
      return {hexe::assets::kListeningRgb565, hexe::assets::kListeningWidth, hexe::assets::kListeningHeight};
    case hexe::AppPhase::kBackendConnecting:
    case hexe::AppPhase::kUpdating:
      return {hexe::assets::kWorkRgb565, hexe::assets::kWorkWidth, hexe::assets::kWorkHeight};
    case hexe::AppPhase::kThinking:
      return {hexe::assets::kThinkingRgb565, hexe::assets::kThinkingWidth, hexe::assets::kThinkingHeight};
    case hexe::AppPhase::kReplying:
      return {hexe::assets::kTalkRgb565, hexe::assets::kTalkWidth, hexe::assets::kTalkHeight};
    case hexe::AppPhase::kError:
      return {hexe::assets::kErrorRgb565, hexe::assets::kErrorWidth, hexe::assets::kErrorHeight};
  }

  return {hexe::assets::kIdleRgb565, hexe::assets::kIdleWidth, hexe::assets::kIdleHeight};
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
  if (g_panel == nullptr || g_framebuffer == nullptr) {
    return;
  }

  const auto phase = hexe::state().phase;

  if (phase == hexe::AppPhase::kBooting && frame <= kFadeFrameCount) {
    blit_fullscreen_image(
        hexe::assets::kLogoRgb565, hexe::assets::kLogoWidth, hexe::assets::kLogoHeight, ease_in_out_alpha(frame));
  } else {
    const auto asset = asset_for_phase(phase);
    blit_fullscreen_image(asset.pixels, asset.width, asset.height, 255);
  }

  draw_wifi_icon(hexe::state().wifi_connected, hexe::state().wifi_rssi);
  draw_audio_stream_icon(hexe::state().audio_streaming);
  if (phase == hexe::AppPhase::kUpdating) {
    draw_ota_progress();
  }
  if (phase == hexe::AppPhase::kBooting || phase == hexe::AppPhase::kWiFiConnecting) {
    draw_firmware_version(build_id);
  }

  ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, 0, kWidth, kHeight, g_framebuffer));
}

}  // namespace hexe::board
