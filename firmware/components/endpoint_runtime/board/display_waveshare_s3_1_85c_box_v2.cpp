#include "board/display.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <ctime>

#include "app_state.h"
#include "board/pins.h"
#include "board/storage.h"
#include "board/waveshare_s3_1_85c_bus.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_st77916.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "system/clock.h"
#include "system/settings.h"

namespace {
constexpr char kTag[] = "hexe_display_ws185";
constexpr int kWidth = 360;
constexpr int kHeight = 360;
// Allocate enough DMA space for tuning while keeping the default conservative.
constexpr int kMaxFlushRows = 32;
constexpr int kStatusSpriteWidth = 128;
constexpr int kStatusSpriteHeight = 128;
constexpr int kStatusSpriteX = (kWidth - kStatusSpriteWidth) / 2;
constexpr int kStatusSpriteY = (kHeight - kStatusSpriteHeight) / 2;
constexpr uint16_t kBlack = 0x0000;
constexpr spi_host_device_t kDisplaySpiHost = SPI2_HOST;
constexpr int kDisplayReadIdClockHz = 3 * 1000 * 1000;
constexpr int kLcdOpcodeReadCommand = 0x0B;

static const st77916_lcd_init_cmd_t kWavesharePanelInit[] = {
    {0xF0, "\x28", 1, 0}, {0xF2, "\x28", 1, 0}, {0x73, "\xF0", 1, 0}, {0x7C, "\xD1", 1, 0},
    {0x83, "\xE0", 1, 0}, {0x84, "\x61", 1, 0}, {0xF2, "\x82", 1, 0}, {0xF0, "\x00", 1, 0},
    {0xF0, "\x01", 1, 0}, {0xF1, "\x01", 1, 0}, {0xB0, "\x56", 1, 0}, {0xB1, "\x4D", 1, 0},
    {0xB2, "\x24", 1, 0}, {0xB4, "\x87", 1, 0}, {0xB5, "\x44", 1, 0}, {0xB6, "\x8B", 1, 0},
    {0xB7, "\x40", 1, 0}, {0xB8, "\x86", 1, 0}, {0xBA, "\x00", 1, 0}, {0xBB, "\x08", 1, 0},
    {0xBC, "\x08", 1, 0}, {0xBD, "\x00", 1, 0}, {0xC0, "\x80", 1, 0}, {0xC1, "\x10", 1, 0},
    {0xC2, "\x37", 1, 0}, {0xC3, "\x80", 1, 0}, {0xC4, "\x10", 1, 0}, {0xC5, "\x37", 1, 0},
    {0xC6, "\xA9", 1, 0}, {0xC7, "\x41", 1, 0}, {0xC8, "\x01", 1, 0}, {0xC9, "\xA9", 1, 0},
    {0xCA, "\x41", 1, 0}, {0xCB, "\x01", 1, 0}, {0xD0, "\x91", 1, 0}, {0xD1, "\x68", 1, 0},
    {0xD2, "\x68", 1, 0}, {0xF5, "\x00\xA5", 2, 0}, {0xDD, "\x4F", 1, 0}, {0xDE, "\x4F", 1, 0},
    {0xF1, "\x10", 1, 0}, {0xF0, "\x00", 1, 0}, {0xF0, "\x02", 1, 0},
    {0xE0, "\xF0\x0A\x10\x09\x09\x36\x35\x33\x4A\x29\x15\x15\x2E\x34", 14, 0},
    {0xE1, "\xF0\x0A\x0F\x08\x08\x05\x34\x33\x4A\x39\x15\x15\x2D\x33", 14, 0},
    {0xF0, "\x10", 1, 0}, {0xF3, "\x10", 1, 0}, {0xE0, "\x07", 1, 0}, {0xE1, "\x00", 1, 0},
    {0xE2, "\x00", 1, 0}, {0xE3, "\x00", 1, 0}, {0xE4, "\xE0", 1, 0}, {0xE5, "\x06", 1, 0},
    {0xE6, "\x21", 1, 0}, {0xE7, "\x01", 1, 0}, {0xE8, "\x05", 1, 0}, {0xE9, "\x02", 1, 0},
    {0xEA, "\xDA", 1, 0}, {0xEB, "\x00", 1, 0}, {0xEC, "\x00", 1, 0}, {0xED, "\x0F", 1, 0},
    {0xEE, "\x00", 1, 0}, {0xEF, "\x00", 1, 0}, {0xF8, "\x00", 1, 0}, {0xF9, "\x00", 1, 0},
    {0xFA, "\x00", 1, 0}, {0xFB, "\x00", 1, 0}, {0xFC, "\x00", 1, 0}, {0xFD, "\x00", 1, 0},
    {0xFE, "\x00", 1, 0}, {0xFF, "\x00", 1, 0}, {0x60, "\x40", 1, 0}, {0x61, "\x04", 1, 0},
    {0x62, "\x00", 1, 0}, {0x63, "\x42", 1, 0}, {0x64, "\xD9", 1, 0}, {0x65, "\x00", 1, 0},
    {0x66, "\x00", 1, 0}, {0x67, "\x00", 1, 0}, {0x68, "\x00", 1, 0}, {0x69, "\x00", 1, 0},
    {0x6A, "\x00", 1, 0}, {0x6B, "\x00", 1, 0}, {0x70, "\x40", 1, 0}, {0x71, "\x03", 1, 0},
    {0x72, "\x00", 1, 0}, {0x73, "\x42", 1, 0}, {0x74, "\xD8", 1, 0}, {0x75, "\x00", 1, 0},
    {0x76, "\x00", 1, 0}, {0x77, "\x00", 1, 0}, {0x78, "\x00", 1, 0}, {0x79, "\x00", 1, 0},
    {0x7A, "\x00", 1, 0}, {0x7B, "\x00", 1, 0}, {0x80, "\x48", 1, 0}, {0x81, "\x00", 1, 0},
    {0x82, "\x06", 1, 0}, {0x83, "\x02", 1, 0}, {0x84, "\xD6", 1, 0}, {0x85, "\x04", 1, 0},
    {0x86, "\x00", 1, 0}, {0x87, "\x00", 1, 0}, {0x88, "\x48", 1, 0}, {0x89, "\x00", 1, 0},
    {0x8A, "\x08", 1, 0}, {0x8B, "\x02", 1, 0}, {0x8C, "\xD8", 1, 0}, {0x8D, "\x04", 1, 0},
    {0x8E, "\x00", 1, 0}, {0x8F, "\x00", 1, 0}, {0x90, "\x48", 1, 0}, {0x91, "\x00", 1, 0},
    {0x92, "\x0A", 1, 0}, {0x93, "\x02", 1, 0}, {0x94, "\xDA", 1, 0}, {0x95, "\x04", 1, 0},
    {0x96, "\x00", 1, 0}, {0x97, "\x00", 1, 0}, {0x98, "\x48", 1, 0}, {0x99, "\x00", 1, 0},
    {0x9A, "\x0C", 1, 0}, {0x9B, "\x02", 1, 0}, {0x9C, "\xDC", 1, 0}, {0x9D, "\x04", 1, 0},
    {0x9E, "\x00", 1, 0}, {0x9F, "\x00", 1, 0}, {0xA0, "\x48", 1, 0}, {0xA1, "\x00", 1, 0},
    {0xA2, "\x05", 1, 0}, {0xA3, "\x02", 1, 0}, {0xA4, "\xD5", 1, 0}, {0xA5, "\x04", 1, 0},
    {0xA6, "\x00", 1, 0}, {0xA7, "\x00", 1, 0}, {0xA8, "\x48", 1, 0}, {0xA9, "\x00", 1, 0},
    {0xAA, "\x07", 1, 0}, {0xAB, "\x02", 1, 0}, {0xAC, "\xD7", 1, 0}, {0xAD, "\x04", 1, 0},
    {0xAE, "\x00", 1, 0}, {0xAF, "\x00", 1, 0}, {0xB0, "\x48", 1, 0}, {0xB1, "\x00", 1, 0},
    {0xB2, "\x09", 1, 0}, {0xB3, "\x02", 1, 0}, {0xB4, "\xD9", 1, 0}, {0xB5, "\x04", 1, 0},
    {0xB6, "\x00", 1, 0}, {0xB7, "\x00", 1, 0}, {0xB8, "\x48", 1, 0}, {0xB9, "\x00", 1, 0},
    {0xBA, "\x0B", 1, 0}, {0xBB, "\x02", 1, 0}, {0xBC, "\xDB", 1, 0}, {0xBD, "\x04", 1, 0},
    {0xBE, "\x00", 1, 0}, {0xBF, "\x00", 1, 0}, {0xC0, "\x10", 1, 0}, {0xC1, "\x47", 1, 0},
    {0xC2, "\x56", 1, 0}, {0xC3, "\x65", 1, 0}, {0xC4, "\x74", 1, 0}, {0xC5, "\x88", 1, 0},
    {0xC6, "\x99", 1, 0}, {0xC7, "\x01", 1, 0}, {0xC8, "\xBB", 1, 0}, {0xC9, "\xAA", 1, 0},
    {0xD0, "\x10", 1, 0}, {0xD1, "\x47", 1, 0}, {0xD2, "\x56", 1, 0}, {0xD3, "\x65", 1, 0},
    {0xD4, "\x74", 1, 0}, {0xD5, "\x88", 1, 0}, {0xD6, "\x99", 1, 0}, {0xD7, "\x01", 1, 0},
    {0xD8, "\xBB", 1, 0}, {0xD9, "\xAA", 1, 0}, {0xF3, "\x01", 1, 0}, {0xF0, "\x00", 1, 0},
    {0x21, "\x00", 1, 0}, {0x11, "\x00", 1, 120}, {0x29, "\x00", 1, 0},
};

esp_lcd_panel_handle_t g_panel = nullptr;
uint16_t *g_framebuffer = nullptr;
uint16_t *g_base_framebuffer = nullptr;
uint16_t *g_flush_buffer = nullptr;
uint16_t *g_status_sprite_pixels = nullptr;
uint8_t *g_status_sprite_alpha = nullptr;
SemaphoreHandle_t g_flush_done = nullptr;
bool g_display_ready = false;
bool g_backlight_on = false;
std::atomic<bool> g_force_redraw{true};
hexe::AppPhase g_last_phase = hexe::AppPhase::kBooting;
bool g_last_muted = false;
bool g_last_ota_active = false;
int g_last_ota_progress = -1;
int g_last_clock_minute_signature = -2;
int g_last_animation_tick = -1;
char g_last_asset_filename[96] = "";
char g_last_sprite_filename[96] = "";
char g_loaded_base_asset_filename[96] = "";
char g_loaded_sprite_filename[96] = "";
int g_last_asset_read_ms = 0;
int g_last_sprite_read_ms = 0;
int g_last_flush_ms = 0;
int g_last_render_ms = 0;

struct DirtyRect {
  int x;
  int y;
  int width;
  int height;
};

struct RenderPlan {
  const char *background_asset;
  const char *sprite_asset;
  const char *fallback_asset;
  bool layered;
};

void flush_framebuffer_region(int x, int y, int width, int height);

DirtyRect combine_dirty_rects(const DirtyRect &left, const DirtyRect &right) {
  const int x0 = std::min(left.x, right.x);
  const int y0 = std::min(left.y, right.y);
  const int x1 = std::max(left.x + left.width, right.x + right.width);
  const int y1 = std::max(left.y + left.height, right.y + right.height);
  return DirtyRect{x0, y0, x1 - x0, y1 - y0};
}

DirtyRect status_sprite_rect() {
  return DirtyRect{kStatusSpriteX, kStatusSpriteY, kStatusSpriteWidth, kStatusSpriteHeight};
}

DirtyRect ota_progress_rect() {
  return DirtyRect{90, 254, 180, 8};
}

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

constexpr uint16_t swap565(uint16_t value) {
  return static_cast<uint16_t>((value >> 8) | (value << 8));
}

uint16_t phase_color(hexe::AppPhase phase) {
  switch (phase) {
    case hexe::AppPhase::kBooting:
      return 0x39E7;
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
      return 0x001F;
    case hexe::AppPhase::kIdle:
      return 0x07E0;
    case hexe::AppPhase::kListening:
      return 0x07FF;
    case hexe::AppPhase::kThinking:
      return 0xFFE0;
    case hexe::AppPhase::kReplying:
      return 0xFD20;
    case hexe::AppPhase::kUpdating:
      return 0xF81F;
    case hexe::AppPhase::kMuted:
      return 0x8410;
    case hexe::AppPhase::kTimerFinished:
      return 0xF800;
    case hexe::AppPhase::kError:
      return 0xF800;
  }
  return 0xFFFF;
}

void set_pixel(int x, int y, uint16_t color) {
  if (g_framebuffer == nullptr || x < 0 || y < 0 || x >= kWidth || y >= kHeight) {
    return;
  }
  g_framebuffer[y * kWidth + x] = color;
}

uint16_t blend_rgb565(uint16_t base, uint16_t color, uint8_t alpha) {
  const int inverse = 255 - alpha;
  const int base_r = (base >> 11) & 0x1F;
  const int base_g = (base >> 5) & 0x3F;
  const int base_b = base & 0x1F;
  const int color_r = (color >> 11) & 0x1F;
  const int color_g = (color >> 5) & 0x3F;
  const int color_b = color & 0x1F;
  const int r = ((base_r * inverse) + (color_r * alpha) + 127) / 255;
  const int g = ((base_g * inverse) + (color_g * alpha) + 127) / 255;
  const int b = ((base_b * inverse) + (color_b * alpha) + 127) / 255;
  return static_cast<uint16_t>((r << 11) | (g << 5) | b);
}

void blend_pixel(int x, int y, uint16_t color, uint8_t alpha) {
  if (g_framebuffer == nullptr || alpha == 0 || x < 0 || y < 0 || x >= kWidth || y >= kHeight) {
    return;
  }
  if (alpha >= 255) {
    g_framebuffer[y * kWidth + x] = color;
    return;
  }
  auto *pixel = g_framebuffer + (y * kWidth) + x;
  *pixel = blend_rgb565(*pixel, color, alpha);
}

void fill_frame(uint16_t color) {
  if (g_framebuffer == nullptr) {
    return;
  }
  for (int index = 0; index < kWidth * kHeight; ++index) {
    g_framebuffer[index] = color;
  }
}

void fill_rect(int x, int y, int width, int height, uint16_t color) {
  for (int row = 0; row < height; ++row) {
    for (int col = 0; col < width; ++col) {
      set_pixel(x + col, y + row, color);
    }
  }
}

void draw_ring(int center_x, int center_y, int radius, int thickness, uint16_t color) {
  const int outer_r2 = radius * radius;
  const int inner = radius - thickness;
  const int inner_r2 = inner * inner;
  for (int y = center_y - radius; y <= center_y + radius; ++y) {
    for (int x = center_x - radius; x <= center_x + radius; ++x) {
      const int dx = x - center_x;
      const int dy = y - center_y;
      const int d2 = (dx * dx) + (dy * dy);
      if (d2 <= outer_r2 && d2 >= inner_r2) {
        set_pixel(x, y, color);
      }
    }
  }
}

void draw_disc(int center_x, int center_y, int radius, uint16_t color) {
  const int r2 = radius * radius;
  for (int y = center_y - radius; y <= center_y + radius; ++y) {
    for (int x = center_x - radius; x <= center_x + radius; ++x) {
      const int dx = x - center_x;
      const int dy = y - center_y;
      if ((dx * dx) + (dy * dy) <= r2) {
        set_pixel(x, y, color);
      }
    }
  }
}

void draw_blended_disc(int center_x, int center_y, int radius, uint16_t color, uint8_t alpha) {
  const int r2 = radius * radius;
  for (int y = center_y - radius; y <= center_y + radius; ++y) {
    for (int x = center_x - radius; x <= center_x + radius; ++x) {
      const int dx = x - center_x;
      const int dy = y - center_y;
      if ((dx * dx) + (dy * dy) <= r2) {
        blend_pixel(x, y, color, alpha);
      }
    }
  }
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

void draw_thick_line(int x0, int y0, int x1, int y1, uint16_t color, int thickness) {
  if (thickness <= 1) {
    draw_line(x0, y0, x1, y1, color);
    return;
  }
  const int radius = thickness / 2;
  for (int dy = -radius; dy <= radius; ++dy) {
    for (int dx = -radius; dx <= radius; ++dx) {
      if ((dx * dx) + (dy * dy) <= radius * radius) {
        draw_line(x0 + dx, y0 + dy, x1 + dx, y1 + dy, color);
      }
    }
  }
}

void draw_blended_line(int x0, int y0, int x1, int y1, uint16_t color, uint8_t alpha) {
  const int dx = x1 > x0 ? x1 - x0 : x0 - x1;
  const int sx = x0 < x1 ? 1 : -1;
  const int dy = y1 > y0 ? y0 - y1 : y1 - y0;
  const int sy = y0 < y1 ? 1 : -1;
  int err = dx + dy;
  while (true) {
    blend_pixel(x0, y0, color, alpha);
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

void draw_blended_thick_line(int x0, int y0, int x1, int y1, uint16_t color, int thickness, uint8_t alpha) {
  if (thickness <= 1) {
    draw_blended_line(x0, y0, x1, y1, color, alpha);
    return;
  }
  const int radius = thickness / 2;
  for (int dy = -radius; dy <= radius; ++dy) {
    for (int dx = -radius; dx <= radius; ++dx) {
      if ((dx * dx) + (dy * dy) <= radius * radius) {
        draw_blended_line(x0 + dx, y0 + dy, x1 + dx, y1 + dy, color, alpha);
      }
    }
  }
}

void draw_blended_arc(int center_x, int center_y, int radius, int start_degrees, int sweep_degrees, int thickness, uint16_t color, uint8_t alpha) {
  if (radius <= 0 || sweep_degrees <= 0 || thickness <= 0 || alpha == 0) {
    return;
  }
  constexpr double kPi = 3.14159265358979323846;
  const int segments = std::max(4, sweep_degrees / 4);
  int previous_x = 0;
  int previous_y = 0;
  bool has_previous = false;
  for (int step = 0; step <= segments; ++step) {
    const double degrees = static_cast<double>(start_degrees) +
        (static_cast<double>(sweep_degrees) * static_cast<double>(step)) / static_cast<double>(segments);
    const double radians = (degrees * kPi) / 180.0;
    const int x = center_x + static_cast<int>(std::lround(static_cast<double>(radius) * std::sin(radians)));
    const int y = center_y - static_cast<int>(std::lround(static_cast<double>(radius) * std::cos(radians)));
    if (has_previous) {
      draw_blended_thick_line(previous_x, previous_y, x, y, color, thickness, alpha);
    }
    previous_x = x;
    previous_y = y;
    has_previous = true;
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
  draw_thick_line(cx, cy, x, y, color, thickness);
}

const uint8_t *digit5x7_glyph(char ch) {
  static constexpr uint8_t kDigits[][5] = {
      {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
      {0x42, 0x61, 0x51, 0x49, 0x46}, {0x21, 0x41, 0x45, 0x4B, 0x31},
      {0x18, 0x14, 0x12, 0x7F, 0x10}, {0x27, 0x45, 0x45, 0x45, 0x39},
      {0x3C, 0x4A, 0x49, 0x49, 0x30}, {0x01, 0x71, 0x09, 0x05, 0x03},
      {0x36, 0x49, 0x49, 0x49, 0x36}, {0x06, 0x49, 0x49, 0x29, 0x1E},
  };
  static constexpr uint8_t kSlash[5] = {0x40, 0x30, 0x08, 0x06, 0x01};
  if (ch >= '0' && ch <= '9') {
    return kDigits[ch - '0'];
  }
  if (ch == '/') {
    return kSlash;
  }
  return nullptr;
}

int scaled_units(int units, int scale_percent) {
  if (units <= 0 || scale_percent <= 0) {
    return 0;
  }
  return (units * scale_percent + 99) / 100;
}

int text5x7_width(const char *text, int scale_percent) {
  if (text == nullptr || text[0] == '\0') {
    return 0;
  }
  int width = 0;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    width += scaled_units(6, scale_percent);
  }
  return width > 0 ? width - scaled_units(1, scale_percent) : 0;
}

void draw_char5x7(int x, int y, char ch, int scale_percent, uint16_t color) {
  const uint8_t *glyph = digit5x7_glyph(ch);
  if (glyph == nullptr || scale_percent <= 0) {
    return;
  }
  for (int col = 0; col < 5; ++col) {
    for (int row = 0; row < 7; ++row) {
      if ((glyph[col] & (1 << row)) == 0) {
        continue;
      }
      const int x0 = (col * scale_percent) / 100;
      int x1 = ((col + 1) * scale_percent) / 100;
      const int y0 = (row * scale_percent) / 100;
      int y1 = ((row + 1) * scale_percent) / 100;
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

void draw_text5x7(int x, int y, const char *text, int scale_percent, uint16_t color) {
  if (text == nullptr || scale_percent <= 0) {
    return;
  }
  int cursor_x = x;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    draw_char5x7(cursor_x, y, *cursor, scale_percent, color);
    cursor_x += scaled_units(6, scale_percent);
  }
}

bool on_color_transfer_done(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx) {
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

esp_lcd_panel_io_spi_config_t make_panel_io_config(int clock_hz, bool transfer_callback) {
  esp_lcd_panel_io_spi_config_t io_config = {};
  io_config.cs_gpio_num = gpio_pin(hexe::board::pins::kWs185DisplayCs);
  io_config.dc_gpio_num = GPIO_NUM_NC;
  io_config.spi_mode = 0;
  io_config.pclk_hz = clock_hz;
  io_config.trans_queue_depth = 10;
  io_config.on_color_trans_done = transfer_callback ? on_color_transfer_done : nullptr;
  io_config.user_ctx = transfer_callback ? &g_flush_done : nullptr;
  io_config.lcd_cmd_bits = 32;
  io_config.lcd_param_bits = 8;
  io_config.flags.quad_mode = 1;
  return io_config;
}

bool read_panel_id(uint8_t register_data[4]) {
  esp_lcd_panel_io_handle_t id_io = nullptr;
  esp_lcd_panel_io_spi_config_t id_config = make_panel_io_config(kDisplayReadIdClockHz, false);
  esp_err_t result = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kDisplaySpiHost), &id_config, &id_io);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Failed to create temporary ST77916 ID IO: %s", esp_err_to_name(result));
    return false;
  }
  int lcd_cmd = 0x04;
  lcd_cmd &= 0xFF;
  lcd_cmd <<= 8;
  lcd_cmd |= kLcdOpcodeReadCommand << 24;
  result = esp_lcd_panel_io_rx_param(id_io, lcd_cmd, register_data, 4);
  ESP_ERROR_CHECK(esp_lcd_panel_io_del(id_io));
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Failed to read ST77916 register 0x04: %s", esp_err_to_name(result));
    return false;
  }
  ESP_LOGI(
      kTag,
      "ST77916 register 0x04 data: %02x %02x %02x %02x",
      register_data[0],
      register_data[1],
      register_data[2],
      register_data[3]);
  return true;
}

void apply_display_rotation() {
  constexpr int rotation = hexe::board::display_config::kRotationDeg;
  static_assert(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270, "unsupported display rotation");
  const bool swap_xy = rotation == 90 || rotation == 270;
  const bool mirror_x = rotation == 90 || rotation == 180;
  const bool mirror_y = rotation == 180 || rotation == 270;
  ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(g_panel, swap_xy));
  ESP_ERROR_CHECK(esp_lcd_panel_mirror(g_panel, mirror_x, mirror_y));
  ESP_LOGI(kTag, "Waveshare display rotation configured: %d deg", rotation);
}

void flush_framebuffer() {
  if (g_panel == nullptr || g_framebuffer == nullptr || g_flush_buffer == nullptr) {
    return;
  }
  flush_framebuffer_region(0, 0, kWidth, kHeight);
}

void flush_framebuffer_region(int x, int y, int width, int height) {
  if (g_panel == nullptr || g_framebuffer == nullptr || g_flush_buffer == nullptr || width <= 0 || height <= 0) {
    return;
  }
  const int clipped_x = std::clamp(x, 0, kWidth);
  const int clipped_y = std::clamp(y, 0, kHeight);
  const int clipped_right = std::clamp(x + width, 0, kWidth);
  const int clipped_bottom = std::clamp(y + height, 0, kHeight);
  const int clipped_width = clipped_right - clipped_x;
  const int clipped_height = clipped_bottom - clipped_y;
  if (clipped_width <= 0 || clipped_height <= 0) {
    return;
  }
  const int64_t started_us = esp_timer_get_time();
  const int flush_rows = std::clamp(hexe::system::display_flush_rows(), 1, kMaxFlushRows);
  for (int row_y = clipped_y; row_y < clipped_bottom; row_y += flush_rows) {
    const int rows = (row_y + flush_rows) <= clipped_bottom ? flush_rows : (clipped_bottom - row_y);
    for (int row = 0; row < rows; ++row) {
      const auto *source = g_framebuffer + ((row_y + row) * kWidth) + clipped_x;
      auto *target = g_flush_buffer + (row * clipped_width);
      for (int col = 0; col < clipped_width; ++col) {
        target[col] = swap565(source[col]);
      }
    }
    while (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, 0) == pdTRUE) {
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, clipped_x, row_y, clipped_right, row_y + rows, g_flush_buffer));
    if (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
      ESP_LOGW(kTag, "Timed out waiting for LCD flush completion");
    }
  }
  g_last_flush_ms = static_cast<int>((esp_timer_get_time() - started_us) / 1000);
}

void draw_status_frame() {
  const auto &state = hexe::state();
  const uint16_t background = state.muted ? 0x2104 : 0x0841;
  const uint16_t accent = phase_color(state.phase);
  fill_frame(background);
  draw_ring(kWidth / 2, kHeight / 2, 154, 12, accent);
  draw_ring(kWidth / 2, kHeight / 2, 118, 6, 0x7BEF);

  const int volume = std::clamp(state.output_volume_percent, 0, 100);
  fill_rect(110, 250, 140, 8, 0x3186);
  fill_rect(110, 250, (140 * volume) / 100, 8, state.muted ? 0xF800 : accent);

  if (state.audio_streaming || state.vad_speaking || state.tts_playback_active) {
    draw_disc(kWidth / 2, kHeight / 2, 38, accent);
  } else {
    draw_ring(kWidth / 2, kHeight / 2, 44, 8, accent);
  }

  if (state.ota_active) {
    const int progress = std::clamp(state.ota_progress_percent, 0, 100);
    fill_rect(90, 254, 180, 8, 0x3186);
    fill_rect(90, 254, (180 * progress) / 100, 8, 0xFFFF);
  }
}

const char *fallback_status_asset_for_state(const hexe::AppState &state) {
  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    return "updating.rgb565";
  }
  if (state.muted || state.phase == hexe::AppPhase::kMuted) {
    return "idle.rgb565";
  }
  switch (state.phase) {
    case hexe::AppPhase::kBooting:
      return "idle.rgb565";
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
      return "connecting.rgb565";
    case hexe::AppPhase::kIdle:
    case hexe::AppPhase::kTimerFinished:
      return "idle.rgb565";
    case hexe::AppPhase::kListening:
      return "listening.rgb565";
    case hexe::AppPhase::kThinking:
      return "thinking.rgb565";
    case hexe::AppPhase::kReplying:
      return "replying.rgb565";
    case hexe::AppPhase::kError:
      return "error.rgb565";
    case hexe::AppPhase::kUpdating:
    case hexe::AppPhase::kMuted:
      break;
  }
  return "idle.rgb565";
}

const char *status_sprite_for_state(const hexe::AppState &state) {
  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    return "updating.rgb565";
  }
  if (state.muted || state.phase == hexe::AppPhase::kMuted) {
    return nullptr;
  }
  switch (state.phase) {
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
      return "connecting.rgb565";
    case hexe::AppPhase::kListening:
      return "listening.rgb565";
    case hexe::AppPhase::kThinking:
      return "thinking.rgb565";
    case hexe::AppPhase::kReplying:
      return "replying.rgb565";
    case hexe::AppPhase::kError:
      return "error.rgb565";
    case hexe::AppPhase::kBooting:
    case hexe::AppPhase::kIdle:
    case hexe::AppPhase::kTimerFinished:
    case hexe::AppPhase::kUpdating:
    case hexe::AppPhase::kMuted:
      break;
  }
  return nullptr;
}

RenderPlan render_plan_for_state(const hexe::AppState &state) {
  const char *fallback = fallback_status_asset_for_state(state);
  const char *sprite = status_sprite_for_state(state);
  if (sprite == nullptr) {
    return RenderPlan{"idle.rgb565", nullptr, fallback, false};
  }
  return RenderPlan{"active.rgb565", sprite, fallback, true};
}

bool should_draw_idle_clock_overlay(const hexe::AppState &state, const char *asset_filename) {
  return asset_filename != nullptr && std::strcmp(asset_filename, "idle.rgb565") == 0 &&
      (state.phase == hexe::AppPhase::kIdle || state.phase == hexe::AppPhase::kBooting ||
       state.phase == hexe::AppPhase::kTimerFinished || state.phase == hexe::AppPhase::kMuted || state.muted);
}

int idle_clock_overlay_signature(const hexe::AppState &state, const char *asset_filename) {
  if (!should_draw_idle_clock_overlay(state, asset_filename) || !hexe::system::clock_synced()) {
    return -1;
  }
  return hexe::system::current_local_minute_signature();
}

bool should_draw_listening_overlay(const hexe::AppState &state, const char *asset_filename) {
  (void)asset_filename;
  return state.phase == hexe::AppPhase::kListening || state.audio_streaming || state.vad_speaking;
}

bool should_draw_thinking_overlay(const hexe::AppState &state, const char *asset_filename) {
  (void)asset_filename;
  return state.phase == hexe::AppPhase::kThinking;
}

bool should_draw_replying_overlay(const hexe::AppState &state, const char *asset_filename) {
  (void)asset_filename;
  return state.phase == hexe::AppPhase::kReplying || state.tts_playback_active;
}

bool should_draw_animated_status_overlay(const hexe::AppState &state, const char *asset_filename) {
  return should_draw_listening_overlay(state, asset_filename) || should_draw_thinking_overlay(state, asset_filename) ||
      should_draw_replying_overlay(state, asset_filename);
}

int active_animation_tick(const hexe::AppState &state, const char *asset_filename) {
  if (should_draw_listening_overlay(state, asset_filename)) {
    constexpr int64_t kListeningFrameUs = 66667;
    return static_cast<int>((esp_timer_get_time() / kListeningFrameUs) % 120);
  }
  if (should_draw_thinking_overlay(state, asset_filename)) {
    constexpr int64_t kThinkingFrameUs = 83333;
    return static_cast<int>((esp_timer_get_time() / kThinkingFrameUs) % 120);
  }
  if (should_draw_replying_overlay(state, asset_filename)) {
    constexpr int64_t kReplyingFrameUs = 55556;
    return static_cast<int>((esp_timer_get_time() / kReplyingFrameUs) % 120);
  }
  if (!should_draw_animated_status_overlay(state, asset_filename)) {
    return -1;
  }
  return -1;
}

int pulse_0_255(int tick, int phase_offset) {
  constexpr double kPi = 3.14159265358979323846;
  const double phase = (static_cast<double>((tick + phase_offset + 120) % 120) * 2.0 * kPi) / 120.0;
  return static_cast<int>(std::lround((std::sin(phase) + 1.0) * 127.5));
}

DirtyRect listening_overlay_rect() {
  return DirtyRect{58, 58, 244, 244};
}

DirtyRect thinking_overlay_rect() {
  return DirtyRect{64, 64, 232, 232};
}

DirtyRect replying_overlay_rect() {
  return DirtyRect{74, 68, 212, 224};
}

DirtyRect animated_status_overlay_rect(const hexe::AppState &state, const char *asset_filename) {
  if (should_draw_replying_overlay(state, asset_filename)) {
    return replying_overlay_rect();
  }
  if (should_draw_thinking_overlay(state, asset_filename)) {
    return thinking_overlay_rect();
  }
  return listening_overlay_rect();
}

DirtyRect layered_overlay_rect(const hexe::AppState &state, const char *asset_filename) {
  DirtyRect rect = status_sprite_rect();
  if (should_draw_animated_status_overlay(state, asset_filename)) {
    rect = combine_dirty_rects(rect, animated_status_overlay_rect(state, asset_filename));
  }
  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    rect = combine_dirty_rects(rect, ota_progress_rect());
  }
  return rect;
}

void draw_listening_overlay(const hexe::AppState &state, int animation_tick) {
  if (animation_tick < 0) {
    return;
  }
  constexpr int kCenterX = 180;
  constexpr int kCenterY = 180;
  constexpr uint16_t kCyan = 0x07FF;
  constexpr uint16_t kSoftCyan = 0x4DFF;
  constexpr uint16_t kWhiteCyan = 0xE7FF;

  const int pulse = pulse_0_255(animation_tick, 0);
  const int level = std::clamp(state.vad_level, 0, 3600);
  const int level_boost = std::max(state.vad_speaking ? 80 : 0, (level * 255) / 3600);
  const int energy = std::clamp((pulse / 3) + level_boost, 40, 255);
  const int orb_radius = 12 + ((pulse * 5) / 255) + ((level_boost * 5) / 255);

  draw_blended_disc(kCenterX, kCenterY, orb_radius + 18, kCyan, static_cast<uint8_t>(20 + (energy / 8)));
  draw_blended_disc(kCenterX, kCenterY, orb_radius + 9, kCyan, static_cast<uint8_t>(42 + (energy / 5)));
  draw_blended_disc(kCenterX, kCenterY, orb_radius, kCyan, static_cast<uint8_t>(145 + (energy / 3)));
  draw_blended_disc(kCenterX - 4, kCenterY - 5, std::max(4, orb_radius / 2), kWhiteCyan, 110);

  for (int rail = 0; rail < 3; ++rail) {
    const int rail_pulse = pulse_0_255(animation_tick, rail * 23);
    const int radius = 44 + (rail * 25) + ((rail_pulse * 12) / 255);
    const int sweep = 34 + ((energy * 18) / 255) + (rail * 3);
    const int thickness = 3 + ((energy + rail_pulse) / 220);
    const uint8_t alpha = static_cast<uint8_t>(60 + ((energy * (3 - rail)) / 5));
    const int drift = ((animation_tick * (rail + 1)) / 3) % 24;
    for (int quadrant = 0; quadrant < 4; ++quadrant) {
      const int center_degrees = 45 + (quadrant * 90) + ((quadrant % 2 == 0) ? drift : -drift);
      draw_blended_arc(kCenterX, kCenterY, radius, center_degrees - (sweep / 2), sweep, thickness, rail == 0 ? kCyan : kSoftCyan, alpha);
    }
  }
}

void draw_thinking_overlay(int animation_tick) {
  if (animation_tick < 0) {
    return;
  }
  constexpr int kCenterX = 180;
  constexpr int kCenterY = 180;
  constexpr uint16_t kCyan = 0x07FF;
  constexpr uint16_t kViolet = 0x895F;
  constexpr uint16_t kSoftWhite = 0xD6FF;

  for (int rail = 0; rail < 3; ++rail) {
    const int radius = 45 + (rail * 25);
    const int sweep = 34 - (rail * 3);
    const int spin = (animation_tick * (rail + 1) * 2) % 360;
    const uint16_t color = rail == 1 ? kViolet : kCyan;
    const uint8_t alpha = static_cast<uint8_t>(88 - (rail * 12));
    for (int segment = 0; segment < 3; ++segment) {
      const int start = spin + (segment * 120) + (rail * 18);
      draw_blended_arc(kCenterX, kCenterY, radius, start, sweep, 3, color, alpha);
    }
  }

  constexpr int kDotSpacing = 21;
  for (int dot = 0; dot < 3; ++dot) {
    const int pulse = pulse_0_255(animation_tick, dot * 20);
    const int radius = 6 + ((pulse * 3) / 255);
    const uint8_t glow_alpha = static_cast<uint8_t>(28 + (pulse / 8));
    const uint8_t dot_alpha = static_cast<uint8_t>(110 + (pulse / 3));
    const int x = kCenterX + ((dot - 1) * kDotSpacing);
    draw_blended_disc(x, kCenterY, radius + 9, dot == 1 ? kViolet : kCyan, glow_alpha);
    draw_blended_disc(x, kCenterY, radius, dot == 1 ? kSoftWhite : kCyan, dot_alpha);
  }
}

void draw_blended_vertical_bar(int center_x, int center_y, int width, int height, uint16_t color, uint8_t alpha) {
  if (width <= 0 || height <= 0 || alpha == 0) {
    return;
  }
  const int radius = width / 2;
  const int x = center_x - radius;
  const int y = center_y - (height / 2);
  for (int row = 0; row < height; ++row) {
    for (int col = 0; col < width; ++col) {
      blend_pixel(x + col, y + row, color, alpha);
    }
  }
  draw_blended_disc(center_x, y, radius, color, alpha);
  draw_blended_disc(center_x, y + height - 1, radius, color, alpha);
}

void draw_replying_overlay(int animation_tick) {
  if (animation_tick < 0) {
    return;
  }
  constexpr int kCenterX = 180;
  constexpr int kCenterY = 180;
  constexpr uint16_t kCyan = 0x07FF;
  constexpr uint16_t kViolet = 0xA51F;
  constexpr uint16_t kSoftCyan = 0x4DFF;

  const int center_pulse = pulse_0_255(animation_tick, 8);
  draw_blended_disc(kCenterX, kCenterY, 24 + ((center_pulse * 4) / 255), kCyan, static_cast<uint8_t>(24 + (center_pulse / 8)));
  draw_blended_disc(kCenterX, kCenterY, 12 + ((center_pulse * 2) / 255), kCyan, static_cast<uint8_t>(70 + (center_pulse / 5)));

  constexpr int kBarCount = 6;
  constexpr int kBarSpacing = 10;
  constexpr int kLeftInnerX = 141;
  constexpr int kRightInnerX = 219;
  for (int side = -1; side <= 1; side += 2) {
    for (int bar = 0; bar < kBarCount; ++bar) {
      const int pulse = pulse_0_255(animation_tick, (bar * 13) + (side > 0 ? 20 : 0));
      const int center_offset = bar * kBarSpacing;
      const int x = side < 0 ? kLeftInnerX - center_offset : kRightInnerX + center_offset;
      const int height = 38 + ((pulse * (42 - (bar * 3))) / 255);
      const int width = bar < 2 ? 5 : 4;
      const uint16_t color = bar >= 4 ? kViolet : (bar == 3 ? kSoftCyan : kCyan);
      const uint8_t alpha = static_cast<uint8_t>(92 + ((pulse * 110) / 255));
      draw_blended_vertical_bar(x, kCenterY, width, height, color, alpha);
    }
  }
}

void draw_animated_status_overlay(const hexe::AppState &state, const char *asset_filename, int animation_tick) {
  if (should_draw_listening_overlay(state, asset_filename)) {
    draw_listening_overlay(state, animation_tick);
  } else if (should_draw_thinking_overlay(state, asset_filename)) {
    draw_thinking_overlay(animation_tick);
  } else if (should_draw_replying_overlay(state, asset_filename)) {
    draw_replying_overlay(animation_tick);
  }
}

void draw_centered_date_text(const char *date) {
  constexpr int kDateCenterX = 180;
  constexpr int kDateY = 256;
  constexpr int kDateScalePercent = 250;
  constexpr uint16_t kDateShadow = 0x0000;
  constexpr uint16_t kDateColor = 0xE7FF;
  const int width = text5x7_width(date, kDateScalePercent);
  const int x = kDateCenterX - (width / 2);
  draw_text5x7(x + 1, kDateY + 1, date, kDateScalePercent, kDateShadow);
  draw_text5x7(x, kDateY, date, kDateScalePercent, kDateColor);
}

void draw_idle_clock_overlay(const hexe::AppState &state, const char *asset_filename) {
  if (!should_draw_idle_clock_overlay(state, asset_filename) || !hexe::system::clock_synced()) {
    return;
  }
  std::tm local = {};
  if (!hexe::system::current_local_time(&local)) {
    return;
  }

  constexpr int kCenterX = 180;
  constexpr int kCenterY = 180;
  constexpr uint16_t kHandShadow = 0x0000;
  constexpr uint16_t kHourColor = 0xE7FF;
  constexpr uint16_t kMinuteColor = 0x07FF;
  const int hour_position = ((local.tm_hour % 12) * 60) + local.tm_min;
  draw_clock_hand(kCenterX + 1, kCenterY + 1, 70, hour_position, 12 * 60, kHandShadow, 7);
  draw_clock_hand(kCenterX + 1, kCenterY + 1, 104, local.tm_min, 60, kHandShadow, 5);
  draw_clock_hand(kCenterX, kCenterY, 70, hour_position, 12 * 60, kHourColor, 5);
  draw_clock_hand(kCenterX, kCenterY, 104, local.tm_min, 60, kMinuteColor, 3);
  draw_disc(kCenterX, kCenterY, 7, kHandShadow);
  draw_disc(kCenterX, kCenterY, 5, kMinuteColor);

  const int month = std::clamp(local.tm_mon + 1, 1, 12);
  const int day = std::clamp(local.tm_mday, 1, 31);
  char date[6] = {};
  std::snprintf(date, sizeof(date), "%02d/%02d", month, day);
  draw_centered_date_text(date);
}

bool should_render_status_asset(
    const hexe::AppState &state,
    const char *asset_filename,
    const char *sprite_filename,
    int clock_minute_signature,
    int animation_tick) {
  return g_force_redraw.load() || state.phase != g_last_phase || state.muted != g_last_muted ||
      state.ota_active != g_last_ota_active || state.ota_progress_percent != g_last_ota_progress ||
      std::strcmp(g_last_asset_filename, asset_filename) != 0 ||
      std::strcmp(g_last_sprite_filename, sprite_filename == nullptr ? "" : sprite_filename) != 0 ||
      g_last_clock_minute_signature != clock_minute_signature || g_last_animation_tick != animation_tick;
}

bool needs_full_status_render(const hexe::AppState &state, const char *asset_filename, int clock_minute_signature) {
  return g_force_redraw.load() || state.phase != g_last_phase || state.muted != g_last_muted ||
      state.ota_active != g_last_ota_active || state.ota_progress_percent != g_last_ota_progress ||
      std::strcmp(g_last_asset_filename, asset_filename) != 0 ||
      g_last_clock_minute_signature != clock_minute_signature;
}

void remember_status_render(
    const hexe::AppState &state,
    const char *asset_filename,
    const char *sprite_filename,
    int clock_minute_signature,
    int animation_tick) {
  g_last_phase = state.phase;
  g_last_muted = state.muted;
  g_last_ota_active = state.ota_active;
  g_last_ota_progress = state.ota_progress_percent;
  g_last_clock_minute_signature = clock_minute_signature;
  g_last_animation_tick = animation_tick;
  std::snprintf(g_last_asset_filename, sizeof(g_last_asset_filename), "%s", asset_filename);
  std::snprintf(g_last_sprite_filename, sizeof(g_last_sprite_filename), "%s", sprite_filename == nullptr ? "" : sprite_filename);
  g_force_redraw = false;
}

bool load_rgb565_status_asset(const char *asset_filename) {
  if (!hexe::board::sd_card_mounted() || asset_filename == nullptr || asset_filename[0] == '\0') {
    ESP_LOGW(kTag, "Cannot load status asset filename=%s sd_mounted=%d", asset_filename == nullptr ? "null" : asset_filename, hexe::board::sd_card_mounted());
    return false;
  }
  char path[192] = {};
  const int written = std::snprintf(path, sizeof(path), "%s/%s", hexe::board::sd_card_pictures_path(), asset_filename);
  if (written <= 0 || written >= static_cast<int>(sizeof(path))) {
    ESP_LOGW(kTag, "Status asset path too long filename=%s", asset_filename);
    return false;
  }
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Status asset missing path=%s", path);
    return false;
  }
  const size_t expected = static_cast<size_t>(kWidth) * static_cast<size_t>(kHeight) * sizeof(uint16_t);
  const int64_t started_us = esp_timer_get_time();
  const size_t read = std::fread(g_framebuffer, 1, expected, file);
  g_last_asset_read_ms = static_cast<int>((esp_timer_get_time() - started_us) / 1000);
  const int extra = std::fgetc(file);
  std::fclose(file);
  if (read != expected || extra != EOF) {
    ESP_LOGW(kTag, "Status asset size mismatch path=%s read=%u expected=%u extra=%d", path, static_cast<unsigned>(read), static_cast<unsigned>(expected), extra);
    g_loaded_base_asset_filename[0] = '\0';
    return false;
  }
  if (g_base_framebuffer != nullptr) {
    std::memcpy(g_base_framebuffer, g_framebuffer, expected);
    std::snprintf(g_loaded_base_asset_filename, sizeof(g_loaded_base_asset_filename), "%s", asset_filename);
  }
  ESP_LOGI(kTag, "Loaded status asset path=%s read_ms=%d bytes=%u", path, g_last_asset_read_ms, static_cast<unsigned>(read));
  return true;
}

bool load_status_sprite(const char *sprite_filename) {
  if (sprite_filename == nullptr || sprite_filename[0] == '\0') {
    g_loaded_sprite_filename[0] = '\0';
    return true;
  }
  if (g_status_sprite_pixels == nullptr || g_status_sprite_alpha == nullptr || !hexe::board::sd_card_mounted()) {
    ESP_LOGW(
        kTag,
        "Cannot load status sprite filename=%s pixels=%d alpha=%d sd_mounted=%d",
        sprite_filename,
        g_status_sprite_pixels != nullptr,
        g_status_sprite_alpha != nullptr,
        hexe::board::sd_card_mounted());
    return false;
  }
  if (std::strcmp(g_loaded_sprite_filename, sprite_filename) == 0) {
    g_last_sprite_read_ms = 0;
    return true;
  }

  char rgb_path[192] = {};
  char alpha_path[192] = {};
  const int rgb_written = std::snprintf(rgb_path, sizeof(rgb_path), "%s/%s", hexe::board::sd_card_sprites_path(), sprite_filename);
  if (rgb_written <= 0 || rgb_written >= static_cast<int>(sizeof(rgb_path))) {
    ESP_LOGW(kTag, "Status sprite path too long filename=%s", sprite_filename);
    return false;
  }
  const char *extension = std::strstr(sprite_filename, ".rgb565");
  if (extension == nullptr || extension[7] != '\0') {
    ESP_LOGW(kTag, "Status sprite must be rgb565 filename=%s", sprite_filename);
    return false;
  }
  char alpha_filename[96] = {};
  const int stem_length = static_cast<int>(extension - sprite_filename);
  if (stem_length <= 0 || stem_length >= static_cast<int>(sizeof(alpha_filename) - 8)) {
    ESP_LOGW(kTag, "Status sprite alpha filename too long filename=%s", sprite_filename);
    return false;
  }
  std::memcpy(alpha_filename, sprite_filename, stem_length);
  std::snprintf(alpha_filename + stem_length, sizeof(alpha_filename) - stem_length, ".alpha8");
  const int alpha_written = std::snprintf(alpha_path, sizeof(alpha_path), "%s/%s", hexe::board::sd_card_sprites_path(), alpha_filename);
  if (alpha_written <= 0 || alpha_written >= static_cast<int>(sizeof(alpha_path))) {
    ESP_LOGW(kTag, "Status sprite alpha path too long filename=%s", alpha_filename);
    return false;
  }

  FILE *rgb_file = std::fopen(rgb_path, "rb");
  if (rgb_file == nullptr) {
    ESP_LOGW(kTag, "Status sprite missing path=%s", rgb_path);
    return false;
  }
  const int64_t started_us = esp_timer_get_time();
  constexpr size_t rgb_expected = static_cast<size_t>(kStatusSpriteWidth) * static_cast<size_t>(kStatusSpriteHeight) * sizeof(uint16_t);
  const size_t rgb_read = std::fread(g_status_sprite_pixels, 1, rgb_expected, rgb_file);
  const int rgb_extra = std::fgetc(rgb_file);
  std::fclose(rgb_file);

  FILE *alpha_file = std::fopen(alpha_path, "rb");
  if (alpha_file == nullptr) {
    ESP_LOGW(kTag, "Status sprite alpha missing path=%s", alpha_path);
    return false;
  }
  constexpr size_t alpha_expected = static_cast<size_t>(kStatusSpriteWidth) * static_cast<size_t>(kStatusSpriteHeight);
  const size_t alpha_read = std::fread(g_status_sprite_alpha, 1, alpha_expected, alpha_file);
  const int alpha_extra = std::fgetc(alpha_file);
  std::fclose(alpha_file);
  g_last_sprite_read_ms = static_cast<int>((esp_timer_get_time() - started_us) / 1000);

  if (rgb_read != rgb_expected || rgb_extra != EOF || alpha_read != alpha_expected || alpha_extra != EOF) {
    ESP_LOGW(
        kTag,
        "Status sprite size mismatch rgb=%s read=%u expected=%u extra=%d alpha=%s read=%u expected=%u extra=%d",
        rgb_path,
        static_cast<unsigned>(rgb_read),
        static_cast<unsigned>(rgb_expected),
        rgb_extra,
        alpha_path,
        static_cast<unsigned>(alpha_read),
        static_cast<unsigned>(alpha_expected),
        alpha_extra);
    g_loaded_sprite_filename[0] = '\0';
    return false;
  }
  std::snprintf(g_loaded_sprite_filename, sizeof(g_loaded_sprite_filename), "%s", sprite_filename);
  ESP_LOGI(
      kTag,
      "Loaded status sprite rgb=%s alpha=%s read_ms=%d bytes=%u",
      rgb_path,
      alpha_path,
      g_last_sprite_read_ms,
      static_cast<unsigned>(rgb_read + alpha_read));
  return true;
}

void draw_status_sprite(const char *sprite_filename) {
  if (sprite_filename == nullptr || sprite_filename[0] == '\0') {
    return;
  }
  if (!load_status_sprite(sprite_filename)) {
    return;
  }
  for (int row = 0; row < kStatusSpriteHeight; ++row) {
    for (int col = 0; col < kStatusSpriteWidth; ++col) {
      const int source_index = (row * kStatusSpriteWidth) + col;
      blend_pixel(kStatusSpriteX + col, kStatusSpriteY + row, g_status_sprite_pixels[source_index], g_status_sprite_alpha[source_index]);
    }
  }
}

bool restore_status_asset_from_base(const char *asset_filename) {
  if (g_framebuffer == nullptr || g_base_framebuffer == nullptr || asset_filename == nullptr ||
      std::strcmp(g_loaded_base_asset_filename, asset_filename) != 0) {
    return false;
  }
  constexpr size_t expected = static_cast<size_t>(kWidth) * static_cast<size_t>(kHeight) * sizeof(uint16_t);
  std::memcpy(g_framebuffer, g_base_framebuffer, expected);
  g_last_asset_read_ms = 0;
  return true;
}

bool restore_rect_from_base(const DirtyRect &rect, const char *asset_filename) {
  if (g_framebuffer == nullptr || g_base_framebuffer == nullptr || asset_filename == nullptr ||
      std::strcmp(g_loaded_base_asset_filename, asset_filename) != 0) {
    return false;
  }
  const int clipped_x = std::clamp(rect.x, 0, kWidth);
  const int clipped_y = std::clamp(rect.y, 0, kHeight);
  const int clipped_right = std::clamp(rect.x + rect.width, 0, kWidth);
  const int clipped_bottom = std::clamp(rect.y + rect.height, 0, kHeight);
  const int clipped_width = clipped_right - clipped_x;
  if (clipped_width <= 0 || clipped_bottom <= clipped_y) {
    return false;
  }
  for (int y = clipped_y; y < clipped_bottom; ++y) {
    std::memcpy(
        g_framebuffer + (y * kWidth) + clipped_x,
        g_base_framebuffer + (y * kWidth) + clipped_x,
        static_cast<size_t>(clipped_width) * sizeof(uint16_t));
  }
  g_last_asset_read_ms = 0;
  return true;
}

bool prepare_status_asset_frame(const char *asset_filename, bool force_reload) {
  if (!force_reload && restore_status_asset_from_base(asset_filename)) {
    return true;
  }
  return load_rgb565_status_asset(asset_filename);
}

bool prepare_layered_status_frame(const RenderPlan &plan, bool force_reload) {
  if (!plan.layered) {
    return prepare_status_asset_frame(plan.background_asset, force_reload);
  }
  if (!prepare_status_asset_frame(plan.background_asset, force_reload)) {
    return false;
  }
  if (!load_status_sprite(plan.sprite_asset)) {
    return false;
  }
  draw_status_sprite(plan.sprite_asset);
  return true;
}

void draw_ota_progress_overlay() {
  const auto &state = hexe::state();
  if (!state.ota_active && state.phase != hexe::AppPhase::kUpdating) {
    return;
  }
  const int progress = std::clamp(state.ota_progress_percent, 0, 100);
  fill_rect(90, 254, 180, 8, 0x3186);
  fill_rect(90, 254, (180 * progress) / 100, 8, 0xFFFF);
}
}  // namespace

namespace hexe::board {

void init_display() {
  if (g_display_ready) {
    return;
  }

  gpio_config_t backlight_config = {};
  backlight_config.pin_bit_mask = 1ULL << pins::kWs185DisplayBacklight;
  backlight_config.mode = GPIO_MODE_OUTPUT;
  gpio_config(&backlight_config);
  gpio_set_level(gpio_pin(pins::kWs185DisplayBacklight), 0);

  g_flush_done = xSemaphoreCreateBinary();
  g_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_framebuffer == nullptr) {
    g_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_8BIT));
  }
  g_base_framebuffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kHeight * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_base_framebuffer == nullptr) {
    ESP_LOGW(kTag, "Waveshare display base-frame cache unavailable; animated overlays will use full redraws");
  }
  g_status_sprite_pixels = static_cast<uint16_t *>(heap_caps_malloc(
      kStatusSpriteWidth * kStatusSpriteHeight * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_status_sprite_pixels == nullptr) {
    g_status_sprite_pixels = static_cast<uint16_t *>(heap_caps_malloc(
        kStatusSpriteWidth * kStatusSpriteHeight * sizeof(uint16_t), MALLOC_CAP_8BIT));
  }
  g_status_sprite_alpha = static_cast<uint8_t *>(heap_caps_malloc(
      kStatusSpriteWidth * kStatusSpriteHeight, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_status_sprite_alpha == nullptr) {
    g_status_sprite_alpha = static_cast<uint8_t *>(heap_caps_malloc(kStatusSpriteWidth * kStatusSpriteHeight, MALLOC_CAP_8BIT));
  }
  if (g_status_sprite_pixels == nullptr || g_status_sprite_alpha == nullptr) {
    ESP_LOGW(kTag, "Waveshare display sprite cache unavailable; layered UI will use full-picture fallback");
  }
  g_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kMaxFlushRows * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_framebuffer == nullptr || g_flush_buffer == nullptr || g_flush_done == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate Waveshare display buffers");
    return;
  }

  spi_bus_config_t bus_config = {};
  bus_config.data0_io_num = pins::kWs185DisplayData0;
  bus_config.data1_io_num = pins::kWs185DisplayData1;
  bus_config.sclk_io_num = pins::kWs185DisplayClk;
  bus_config.data2_io_num = pins::kWs185DisplayData2;
  bus_config.data3_io_num = pins::kWs185DisplayData3;
  bus_config.data4_io_num = -1;
  bus_config.data5_io_num = -1;
  bus_config.data6_io_num = -1;
  bus_config.data7_io_num = -1;
  bus_config.max_transfer_sz = kWidth * kMaxFlushRows * sizeof(uint16_t);
  esp_err_t result = spi_bus_initialize(kDisplaySpiHost, &bus_config, SPI_DMA_CH_AUTO);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(kTag, "Failed to initialize ST77916 SPI bus: %s", esp_err_to_name(result));
    return;
  }

  if (!waveshare_185_reset_display()) {
    ESP_LOGW(kTag, "Waveshare display reset pulse failed; attempting panel init anyway");
  }
  uint8_t panel_id[4] = {};
  const bool panel_id_read = read_panel_id(panel_id);

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  esp_lcd_panel_io_spi_config_t io_config = make_panel_io_config(hexe::system::display_pixel_clock_hz(), true);
  result = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kDisplaySpiHost), &io_config, &io_handle);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create ST77916 panel IO: %s", esp_err_to_name(result));
    return;
  }

  st77916_vendor_config_t vendor_config = {};
  vendor_config.init_cmds = kWavesharePanelInit;
  vendor_config.init_cmds_size = sizeof(kWavesharePanelInit) / sizeof(kWavesharePanelInit[0]);
  vendor_config.flags.use_qspi_interface = 1;
  ESP_LOGI(
      kTag,
      "Using Waveshare ST77916 init table panel_id_read=%d panel_id=%02x:%02x:%02x:%02x",
      panel_id_read,
      panel_id[0],
      panel_id[1],
      panel_id[2],
      panel_id[3]);
  esp_lcd_panel_dev_config_t panel_config = {};
  panel_config.reset_gpio_num = GPIO_NUM_NC;
  panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
  panel_config.bits_per_pixel = 16;
  panel_config.vendor_config = &vendor_config;

  result = esp_lcd_new_panel_st77916(io_handle, &panel_config, &g_panel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create ST77916 panel: %s", esp_err_to_name(result));
    return;
  }
  ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
  apply_display_rotation();
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));

  g_display_ready = true;
  turn_on_backlight();
  show_black_frame();
  ESP_LOGI(kTag, "Waveshare ST77916 display initialized at %dx%d", kWidth, kHeight);
}

void show_black_frame() {
  if (!g_display_ready) {
    return;
  }
  fill_frame(kBlack);
  flush_framebuffer();
}

void turn_on_backlight() {
  if (g_backlight_on) {
    return;
  }
  gpio_set_level(gpio_pin(pins::kWs185DisplayBacklight), 1);
  g_backlight_on = true;
}

void render_boot_frame(int frame, const char *build_id) {
  (void)build_id;
  if (!g_display_ready) {
    return;
  }
  const int64_t render_started_us = esp_timer_get_time();
  const auto &state = hexe::state();
  const RenderPlan plan = render_plan_for_state(state);
  const char *asset_filename = plan.background_asset;
  const char *sprite_filename = plan.sprite_asset;
  const int clock_minute_signature = idle_clock_overlay_signature(state, asset_filename);
  const int animation_tick = active_animation_tick(state, asset_filename);
  if (!should_render_status_asset(state, asset_filename, sprite_filename, clock_minute_signature, animation_tick)) {
    return;
  }
  const bool full_render = needs_full_status_render(state, asset_filename, clock_minute_signature);
  const bool draw_animation = should_draw_animated_status_overlay(state, asset_filename);
  if (plan.layered && !full_render && restore_rect_from_base(layered_overlay_rect(state, asset_filename), asset_filename)) {
    draw_status_sprite(sprite_filename);
    if (draw_animation) {
      draw_animated_status_overlay(state, asset_filename, animation_tick);
    }
    draw_ota_progress_overlay();
    const DirtyRect rect = layered_overlay_rect(state, asset_filename);
    flush_framebuffer_region(rect.x, rect.y, rect.width, rect.height);
    g_last_render_ms = static_cast<int>((esp_timer_get_time() - render_started_us) / 1000);
    remember_status_render(state, asset_filename, sprite_filename, clock_minute_signature, animation_tick);
    return;
  }
  if (!plan.layered && !full_render && draw_animation && restore_rect_from_base(animated_status_overlay_rect(state, asset_filename), asset_filename)) {
    draw_animated_status_overlay(state, asset_filename, animation_tick);
    const DirtyRect rect = animated_status_overlay_rect(state, asset_filename);
    flush_framebuffer_region(rect.x, rect.y, rect.width, rect.height);
    g_last_render_ms = static_cast<int>((esp_timer_get_time() - render_started_us) / 1000);
    remember_status_render(state, asset_filename, sprite_filename, clock_minute_signature, animation_tick);
    return;
  }

  if (prepare_layered_status_frame(plan, g_force_redraw.load())) {
    draw_idle_clock_overlay(state, asset_filename);
    if (draw_animation) {
      draw_animated_status_overlay(state, asset_filename, animation_tick);
    }
    draw_ota_progress_overlay();
  } else if (plan.layered && prepare_status_asset_frame(plan.fallback_asset, true)) {
    asset_filename = plan.fallback_asset;
    sprite_filename = nullptr;
    draw_idle_clock_overlay(state, asset_filename);
    if (draw_animation) {
      draw_animated_status_overlay(state, asset_filename, animation_tick);
    }
    draw_ota_progress_overlay();
  } else {
    ESP_LOGW(kTag, "Falling back to procedural status frame for asset=%s", plan.fallback_asset);
    draw_status_frame();
    draw_idle_clock_overlay(state, asset_filename);
    if (draw_animation) {
      draw_animated_status_overlay(state, asset_filename, animation_tick);
    }
  }
  flush_framebuffer();
  g_last_render_ms = static_cast<int>((esp_timer_get_time() - render_started_us) / 1000);
  ESP_LOGI(
      kTag,
      "Rendered status frame asset=%s render_ms=%d read_ms=%d flush_ms=%d flush_rows=%d pixel_clock_hz=%d",
      asset_filename,
      g_last_render_ms,
      g_last_asset_read_ms,
      g_last_flush_ms,
      hexe::system::display_flush_rows(),
      hexe::system::display_pixel_clock_hz());
  remember_status_render(state, asset_filename, sprite_filename, clock_minute_signature, animation_tick);
  (void)frame;
}

void request_display_assets_reload() {
  g_force_redraw = true;
}

bool show_next_ui_page() {
  g_force_redraw = true;
  return false;
}

bool show_previous_ui_page() {
  g_force_redraw = true;
  return false;
}

bool display_ready() {
  return g_display_ready;
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

int display_last_asset_read_ms() {
  return g_last_asset_read_ms;
}

int display_last_flush_ms() {
  return g_last_flush_ms;
}

int display_last_render_ms() {
  return g_last_render_ms;
}

const char *display_last_asset_filename() {
  return g_last_asset_filename;
}

}  // namespace hexe::board
