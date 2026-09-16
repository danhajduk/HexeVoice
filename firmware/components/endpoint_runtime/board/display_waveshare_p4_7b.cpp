#include "board/display.h"

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/stat.h>

#include "app_state.h"
#include "board/storage.h"
#include "bsp/display.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_commands.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_display_p4_7b";
constexpr int kWidth = BSP_LCD_H_RES;
constexpr int kHeight = BSP_LCD_V_RES;
constexpr int kFlushRows = 24;
constexpr uint16_t kBlack = 0x0000;
constexpr uint16_t kCanvas = 0x08A4;
constexpr uint16_t kCanvasAlt = 0x10E6;
constexpr uint16_t kInk = 0xFFFF;
constexpr uint16_t kMuted = 0x7BEF;
constexpr uint16_t kCyan = 0x05FF;
constexpr uint16_t kGreen = 0x37E6;
constexpr uint16_t kYellow = 0xFEE0;
constexpr uint16_t kOrange = 0xFCA0;
constexpr uint16_t kRed = 0xF926;
constexpr uint16_t kMagenta = 0xD29F;
constexpr uint16_t kBlue = 0x03BF;
constexpr char kProceduralAssetName[] = "procedural-p4-7b-status";
constexpr char kSdTestBackgroundName[] = "bg.rgb565";
constexpr size_t kSdTestBackgroundBytes = static_cast<size_t>(kWidth) * static_cast<size_t>(kHeight) * sizeof(uint16_t);

esp_lcd_panel_handle_t g_panel = nullptr;
esp_lcd_panel_io_handle_t g_panel_io = nullptr;
uint16_t *g_flush_buffer = nullptr;
SemaphoreHandle_t g_refresh_done = nullptr;
bool g_display_ready = false;
bool g_backlight_on = false;
bool g_wait_for_refresh = false;
bool g_force_redraw = true;
int g_strip_y = 0;
int g_strip_rows = 0;
int g_last_signature = -1;
int g_last_asset_read_ms = 0;
int g_last_flush_ms = 0;
int g_last_render_ms = 0;
char g_last_asset_filename[128] = "procedural-p4-7b-status";
bool g_logged_sd_unavailable = false;
bool g_logged_bg_missing = false;
bool g_logged_bg_bad_size = false;
bool g_logged_bg_read_error = false;

bool on_color_done(esp_lcd_panel_handle_t panel, esp_lcd_dpi_panel_event_data_t *edata, void *user_ctx) {
  (void)panel;
  (void)edata;
  auto done = static_cast<SemaphoreHandle_t>(user_ctx);
  if (done == nullptr) {
    return false;
  }
  BaseType_t high_task_woken = pdFALSE;
  xSemaphoreGiveFromISR(done, &high_task_woken);
  return high_task_woken == pdTRUE;
}

uint16_t phase_color(hexe::AppPhase phase) {
  switch (phase) {
    case hexe::AppPhase::kBooting:
      return kCyan;
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
      return kBlue;
    case hexe::AppPhase::kIdle:
      return kGreen;
    case hexe::AppPhase::kListening:
      return kCyan;
    case hexe::AppPhase::kThinking:
      return kYellow;
    case hexe::AppPhase::kReplying:
      return kOrange;
    case hexe::AppPhase::kUpdating:
      return kMagenta;
    case hexe::AppPhase::kMuted:
      return kMuted;
    case hexe::AppPhase::kTimerFinished:
    case hexe::AppPhase::kError:
      return kRed;
  }
  return kInk;
}

const char *phase_text(const hexe::AppState &state) {
  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    return "Updating";
  }
  if (state.muted || state.phase == hexe::AppPhase::kMuted) {
    return "Muted";
  }
  switch (state.phase) {
    case hexe::AppPhase::kBooting:
      return "Booting";
    case hexe::AppPhase::kWiFiConnecting:
      return "WiFi setup pending";
    case hexe::AppPhase::kBackendConnecting:
      return "Core connecting";
    case hexe::AppPhase::kIdle:
      return "Ready";
    case hexe::AppPhase::kListening:
      return "Listening";
    case hexe::AppPhase::kThinking:
      return "Thinking";
    case hexe::AppPhase::kReplying:
      return "Replying";
    case hexe::AppPhase::kTimerFinished:
      return "Timer done";
    case hexe::AppPhase::kError:
      return "Device error";
    case hexe::AppPhase::kUpdating:
    case hexe::AppPhase::kMuted:
      break;
  }
  return "Hexe endpoint";
}

int frame_signature(int frame) {
  const auto &state = hexe::state();
  int signature = static_cast<int>(state.phase);
  signature = (signature * 131) + (state.muted ? 1 : 0);
  signature = (signature * 131) + (state.wifi_connected ? 1 : 0);
  signature = (signature * 131) + (state.backend_connected ? 1 : 0);
  signature = (signature * 131) + (state.voice_ws_connected ? 1 : 0);
  signature = (signature * 131) + (state.audio_streaming ? 1 : 0);
  signature = (signature * 131) + (state.tts_playback_active ? 1 : 0);
  signature = (signature * 131) + (state.ota_active ? 1 : 0);
  signature = (signature * 131) + std::clamp(state.ota_progress_percent, 0, 100);
  if (state.phase == hexe::AppPhase::kBooting || state.phase == hexe::AppPhase::kListening ||
      state.phase == hexe::AppPhase::kThinking || state.phase == hexe::AppPhase::kReplying) {
    signature = (signature * 131) + (frame % 64);
  }
  return signature;
}

void set_pixel(int x, int y, uint16_t color) {
  if (g_flush_buffer == nullptr || x < 0 || y < g_strip_y || x >= kWidth || y >= g_strip_y + g_strip_rows) {
    return;
  }
  g_flush_buffer[(y - g_strip_y) * kWidth + x] = color;
}

void fill_rect(int x, int y, int width, int height, uint16_t color) {
  const int x0 = std::clamp(x, 0, kWidth);
  const int y0 = std::clamp(y, g_strip_y, g_strip_y + g_strip_rows);
  const int x1 = std::clamp(x + width, 0, kWidth);
  const int y1 = std::clamp(y + height, g_strip_y, g_strip_y + g_strip_rows);
  for (int row = y0; row < y1; ++row) {
    uint16_t *target = g_flush_buffer + ((row - g_strip_y) * kWidth) + x0;
    for (int col = x0; col < x1; ++col) {
      *target++ = color;
    }
  }
}

void draw_hline(int x, int y, int width, uint16_t color) {
  fill_rect(x, y, width, 1, color);
}

void draw_vline(int x, int y, int height, uint16_t color) {
  fill_rect(x, y, 1, height, color);
}

void draw_rect_outline(int x, int y, int width, int height, uint16_t color) {
  draw_hline(x, y, width, color);
  draw_hline(x, y + height - 1, width, color);
  draw_vline(x, y, height, color);
  draw_vline(x + width - 1, y, height, color);
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

void draw_ring(int center_x, int center_y, int radius, int thickness, uint16_t color) {
  const int outer = radius * radius;
  const int inner_radius = radius - thickness;
  const int inner = inner_radius * inner_radius;
  for (int y = center_y - radius; y <= center_y + radius; ++y) {
    for (int x = center_x - radius; x <= center_x + radius; ++x) {
      const int dx = x - center_x;
      const int dy = y - center_y;
      const int distance = (dx * dx) + (dy * dy);
      if (distance <= outer && distance >= inner) {
        set_pixel(x, y, color);
      }
    }
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
  static constexpr uint8_t kColon[5] = {0x00, 0x36, 0x36, 0x00, 0x00};
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
  if (ch == '-' || ch == '_') {
    return kDash;
  }
  if (ch == ':') {
    return kColon;
  }
  return nullptr;
}

int scaled_units(int units, int scale_percent) {
  return (units * scale_percent + 99) / 100;
}

int text_width(const char *text, int scale_percent) {
  if (text == nullptr || scale_percent <= 0) {
    return 0;
  }
  int width = 0;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    width += scaled_units(*cursor == ' ' ? 3 : 6, scale_percent);
  }
  return width > 0 ? width - scaled_units(1, scale_percent) : 0;
}

void draw_char(int x, int y, char ch, int scale_percent, uint16_t color) {
  const uint8_t *glyph = font5x7_glyph(ch);
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

void draw_text(int x, int y, const char *text, int scale_percent, uint16_t color) {
  if (text == nullptr || scale_percent <= 0) {
    return;
  }
  int cursor_x = x;
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    if (*cursor != ' ') {
      draw_char(cursor_x, y, *cursor, scale_percent, color);
    }
    cursor_x += scaled_units(*cursor == ' ' ? 3 : 6, scale_percent);
  }
}

void draw_centered_text(int y, const char *text, int scale_percent, uint16_t color) {
  draw_text((kWidth - text_width(text, scale_percent)) / 2, y, text, scale_percent, color);
}

bool build_sd_test_background_path(char *path, size_t path_size) {
  if (path == nullptr || path_size == 0) {
    return false;
  }
  const int written =
      std::snprintf(path, path_size, "%s/%s", hexe::board::sd_card_pictures_path(), kSdTestBackgroundName);
  return written > 0 && written < static_cast<int>(path_size);
}

FILE *open_sd_test_background(char *path, size_t path_size) {
  if (!hexe::board::sd_card_mounted()) {
    if (!g_logged_sd_unavailable) {
      ESP_LOGI(kTag, "No microSD mounted; using procedural P4 display background");
      g_logged_sd_unavailable = true;
    }
    return nullptr;
  }
  if (!build_sd_test_background_path(path, path_size)) {
    ESP_LOGW(kTag, "Could not build P4 SD test background path");
    return nullptr;
  }

  struct stat info = {};
  if (stat(path, &info) != 0) {
    if (!g_logged_bg_missing) {
      ESP_LOGI(kTag, "P4 SD test background not found at %s: %s", path, std::strerror(errno));
      g_logged_bg_missing = true;
    }
    return nullptr;
  }
  if (static_cast<size_t>(info.st_size) != kSdTestBackgroundBytes) {
    if (!g_logged_bg_bad_size) {
      ESP_LOGW(
          kTag,
          "Ignoring P4 SD test background %s: expected %u bytes for %dx%d RGB565, got %ld",
          path,
          static_cast<unsigned>(kSdTestBackgroundBytes),
          kWidth,
          kHeight,
          static_cast<long>(info.st_size));
      g_logged_bg_bad_size = true;
    }
    return nullptr;
  }

  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Could not open P4 SD test background %s: %s", path, std::strerror(errno));
  }
  return file;
}

bool read_sd_background_strip(FILE *file, const char *path) {
  if (file == nullptr || g_flush_buffer == nullptr) {
    return false;
  }
  const size_t pixel_count = static_cast<size_t>(kWidth) * static_cast<size_t>(g_strip_rows);
  const size_t read_pixels = std::fread(g_flush_buffer, sizeof(uint16_t), pixel_count, file);
  if (read_pixels != pixel_count) {
    if (!g_logged_bg_read_error) {
      ESP_LOGW(
          kTag,
          "Could not read P4 SD test background strip from %s at y=%d: expected %u pixels, got %u",
          path == nullptr ? kSdTestBackgroundName : path,
          g_strip_y,
          static_cast<unsigned>(pixel_count),
          static_cast<unsigned>(read_pixels));
      g_logged_bg_read_error = true;
    }
    return false;
  }
  return true;
}

void clear_strip() {
  for (int row = 0; row < g_strip_rows; ++row) {
    const int y = g_strip_y + row;
    const bool alt_band = ((y / 36) % 2) == 0;
    const uint16_t base = alt_band ? kCanvas : kCanvasAlt;
    for (int x = 0; x < kWidth; ++x) {
      g_flush_buffer[row * kWidth + x] = base;
    }
  }
}

bool draw_status_frame(int frame, const char *build_id, FILE *background_file, const char *background_path) {
  const auto &state = hexe::state();
  const uint16_t accent = phase_color(state.phase);
  const bool drew_background = read_sd_background_strip(background_file, background_path);
  if (!drew_background) {
    clear_strip();
  } else {
    (void)frame;
    (void)build_id;
    return true;
  }

  for (int x = 64; x < kWidth; x += 64) {
    draw_vline(x, 0, kHeight, 0x1168);
  }
  for (int y = 48; y < kHeight; y += 48) {
    draw_hline(0, y, kWidth, 0x1168);
  }

  fill_rect(0, 0, kWidth, 12, accent);
  fill_rect(0, kHeight - 12, kWidth, 12, accent);
  draw_rect_outline(32, 32, kWidth - 64, kHeight - 64, 0x3A49);
  draw_rect_outline(44, 44, kWidth - 88, kHeight - 88, 0x19CC);

  draw_ring(kWidth / 2, 284, 154, 8, 0x29CF);
  draw_ring(kWidth / 2, 284, 126 + (frame % 5), 8, accent);
  draw_disc(kWidth / 2, 284, 34, accent);
  draw_disc(kWidth / 2, 284, 18, kCanvas);

  draw_centered_text(112, "HEXE", 1450, kInk);
  draw_centered_text(228, "endpoint", 420, accent);
  draw_centered_text(402, phase_text(state), 380, kInk);

  char detail[96] = {};
  std::snprintf(
      detail,
      sizeof(detail),
      "display 1024x600  touch pending  audio pending  hosted wifi pending");
  draw_centered_text(466, detail, 210, 0xBDF7);

  char version[96] = {};
  std::snprintf(version, sizeof(version), "build %s", build_id == nullptr ? "unknown" : build_id);
  draw_text(60, 548, version, 190, 0xBDF7);

  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    const int progress = std::clamp(state.ota_progress_percent, 0, 100);
    fill_rect(704, 548, 260, 16, 0x2965);
    fill_rect(704, 548, (260 * progress) / 100, 16, accent);
    draw_rect_outline(704, 548, 260, 16, kInk);
  } else {
    draw_text(720, 548, "single app", 190, 0xBDF7);
  }
  return false;
}

void wait_for_flush_ready() {
  if (!g_wait_for_refresh || g_refresh_done == nullptr) {
    return;
  }
  if (xSemaphoreTake(g_refresh_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
    ESP_LOGW(kTag, "Timed out waiting for P4 LCD refresh completion");
  }
}

bool flush_strip(int y, int rows) {
  if (g_panel == nullptr || g_flush_buffer == nullptr) {
    return false;
  }
  while (g_refresh_done != nullptr && xSemaphoreTake(g_refresh_done, 0) == pdTRUE) {
  }
  const esp_err_t result = esp_lcd_panel_draw_bitmap(g_panel, 0, y, kWidth, y + rows, g_flush_buffer);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "P4 LCD draw failed at y=%d rows=%d: %s", y, rows, esp_err_to_name(result));
    return false;
  }
  wait_for_flush_ready();
  return true;
}

bool render_frame(int frame, const char *build_id, bool black) {
  if (!g_display_ready || g_flush_buffer == nullptr) {
    return false;
  }
  const int64_t started_us = esp_timer_get_time();
  char background_path[128] = {};
  FILE *background_file = black ? nullptr : open_sd_test_background(background_path, sizeof(background_path));
  int asset_read_ms = 0;
  bool used_background = false;
  const int64_t asset_read_started_us = background_file != nullptr ? esp_timer_get_time() : 0;
  for (int y = 0; y < kHeight; y += kFlushRows) {
    g_strip_y = y;
    g_strip_rows = std::min(kFlushRows, kHeight - y);
    if (black) {
      fill_rect(0, y, kWidth, g_strip_rows, kBlack);
    } else {
      used_background = draw_status_frame(frame, build_id, background_file, background_path) || used_background;
    }
    if (!flush_strip(y, g_strip_rows)) {
      if (background_file != nullptr) {
        std::fclose(background_file);
      }
      return false;
    }
  }
  if (background_file != nullptr) {
    asset_read_ms = static_cast<int>((esp_timer_get_time() - asset_read_started_us) / 1000);
    std::fclose(background_file);
  }
  const int elapsed_ms = static_cast<int>((esp_timer_get_time() - started_us) / 1000);
  g_last_render_ms = elapsed_ms;
  g_last_flush_ms = elapsed_ms;
  g_last_asset_read_ms = asset_read_ms;
  if (used_background && background_path[0] != '\0') {
    std::snprintf(g_last_asset_filename, sizeof(g_last_asset_filename), "%s", background_path);
  } else {
    std::snprintf(g_last_asset_filename, sizeof(g_last_asset_filename), "%s", kProceduralAssetName);
  }
  return true;
}
}  // namespace

namespace hexe::board {

void init_display() {
  if (g_display_ready) {
    return;
  }

  g_refresh_done = xSemaphoreCreateBinary();
  g_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kFlushRows * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_flush_buffer == nullptr) {
    g_flush_buffer = static_cast<uint16_t *>(
        heap_caps_malloc(kWidth * kFlushRows * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  }
  if (g_flush_buffer == nullptr || g_refresh_done == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate P4 LCD strip buffer");
    return;
  }

  const esp_err_t result = bsp_display_new(nullptr, &g_panel, &g_panel_io);
  if (result != ESP_OK || g_panel == nullptr) {
    ESP_LOGE(kTag, "Failed to initialize Waveshare P4 7B display: %s", esp_err_to_name(result));
    return;
  }

  esp_lcd_dpi_panel_event_callbacks_t callbacks = {};
  callbacks.on_color_trans_done = on_color_done;
  const esp_err_t callback_result = esp_lcd_dpi_panel_register_event_callbacks(g_panel, &callbacks, g_refresh_done);
  if (callback_result == ESP_OK) {
    g_wait_for_refresh = true;
  } else {
    ESP_LOGW(kTag, "P4 LCD refresh callback unavailable: %s", esp_err_to_name(callback_result));
  }

  esp_err_t display_on_result = esp_lcd_panel_disp_on_off(g_panel, true);
  if (display_on_result == ESP_ERR_NOT_SUPPORTED && g_panel_io != nullptr) {
    ESP_LOGW(kTag, "Panel display-on callback unavailable; sending DCS display-on command");
    display_on_result = esp_lcd_panel_io_tx_param(g_panel_io, LCD_CMD_DISPON, nullptr, 0);
  }
  if (display_on_result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to enable Waveshare P4 7B panel: %s", esp_err_to_name(display_on_result));
    return;
  }
  g_display_ready = true;
  ESP_LOGI(kTag, "Waveshare P4 7B display initialized at %dx%d RGB565", kWidth, kHeight);
}

void show_black_frame() {
  render_frame(0, nullptr, true);
  g_force_redraw = true;
}

void turn_on_backlight() {
  if (g_backlight_on) {
    return;
  }
  const esp_err_t result = bsp_display_backlight_on();
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Failed to enable P4 LCD backlight: %s", esp_err_to_name(result));
    return;
  }
  g_backlight_on = true;
}

void render_boot_frame(int frame, const char *build_id) {
  const int signature = frame_signature(frame);
  if (!g_force_redraw && signature == g_last_signature) {
    return;
  }
  if (render_frame(frame, build_id, false)) {
    g_last_signature = signature;
    g_force_redraw = false;
  }
}

void request_display_assets_reload() {
  g_force_redraw = true;
  g_logged_bg_missing = false;
  g_logged_bg_bad_size = false;
  g_logged_bg_read_error = false;
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
