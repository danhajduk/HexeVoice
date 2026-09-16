#include "recovery_display.h"

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <sys/stat.h>

#include "board_profile_pins.h"
#include "endpoint_config.h"
#include "recovery_ble_provisioning.h"
#include "recovery_control.h"
#include "esp_log.h"

#if HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B
#include "bsp/display.h"
#include "bsp/esp32_p4_wifi6_touch_lcd_7b.h"
#include "esp_app_desc.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_commands.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

extern "C" const char *hexe_ble_provisioning_local_address();
#endif

#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_lcd_st77916.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#endif

namespace {

constexpr char kTag[] = "hexe_recovery_display";

#if HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B
constexpr int kWidth = BSP_LCD_H_RES;
constexpr int kHeight = BSP_LCD_V_RES;
constexpr int kFlushRows = 120;
constexpr int kBytesPerPixel = 3;
constexpr size_t kBackgroundBytes =
    static_cast<size_t>(kWidth) * static_cast<size_t>(kHeight) * kBytesPerPixel;
constexpr char kRecoveryBackgroundPath[] = BSP_SD_MOUNT_POINT "/hexe/pictures/recovery_bg.rgb888";
constexpr char kFallbackBackgroundPath[] = BSP_SD_MOUNT_POINT "/hexe/pictures/bg.rgb888";

constexpr uint32_t rgb565_to_rgb888(uint16_t color) {
  const uint32_t red = ((color >> 11) & 0x1F) * 255 / 31;
  const uint32_t green = ((color >> 5) & 0x3F) * 255 / 63;
  const uint32_t blue = (color & 0x1F) * 255 / 31;
  return (red << 16) | (green << 8) | blue;
}

constexpr uint32_t kBlack = rgb565_to_rgb888(0x0000);
constexpr uint32_t kCanvas = rgb565_to_rgb888(0x08A4);
constexpr uint32_t kCanvasAlt = rgb565_to_rgb888(0x10E6);
constexpr uint32_t kInk = rgb565_to_rgb888(0xFFFF);
constexpr uint32_t kMuted = rgb565_to_rgb888(0xBDF7);
constexpr uint32_t kCyan = rgb565_to_rgb888(0x05FF);
constexpr uint32_t kGreen = rgb565_to_rgb888(0x37E6);
constexpr uint32_t kYellow = rgb565_to_rgb888(0xFEE0);
constexpr uint32_t kOrange = rgb565_to_rgb888(0xFCA0);
constexpr uint32_t kRed = rgb565_to_rgb888(0xF926);
constexpr uint32_t kMagenta = rgb565_to_rgb888(0xD29F);
constexpr uint32_t kBlue = rgb565_to_rgb888(0x03BF);

enum class P4Screen {
  kWaiting,
  kPairing,
  kValidating,
  kApplying,
  kComplete,
  kFailed,
  kOta,
};

esp_lcd_panel_handle_t g_panel = nullptr;
esp_lcd_panel_io_handle_t g_panel_io = nullptr;
uint8_t *g_flush_buffer = nullptr;
uint8_t *g_background_pixels = nullptr;
SemaphoreHandle_t g_refresh_done = nullptr;
bool g_display_ready = false;
bool g_wait_for_refresh = false;
bool g_backlight_on = false;
bool g_force_redraw = true;
bool g_sd_checked = false;
int g_strip_y = 0;
int g_strip_rows = 0;
uint32_t g_last_signature = 0;
uint32_t g_frame = 0;
const char *g_background_source = "procedural";

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

bool state_is_one_of(const char *state, const char *a, const char *b = nullptr, const char *c = nullptr, const char *d = nullptr) {
  if (state == nullptr) {
    return false;
  }
  return std::strcmp(state, a) == 0 || (b != nullptr && std::strcmp(state, b) == 0) ||
         (c != nullptr && std::strcmp(state, c) == 0) || (d != nullptr && std::strcmp(state, d) == 0);
}

P4Screen desired_p4_screen() {
  if (hexe::recovery::recovery_firmware_install_active()) {
    return P4Screen::kOta;
  }
  const char *state = hexe::recovery::recovery_ble_state();
  if (state_is_one_of(state, "failed", "pairing_failed")) {
    return P4Screen::kFailed;
  }
  if (state_is_one_of(state, "completed")) {
    return P4Screen::kComplete;
  }
  if (state_is_one_of(state, "applying")) {
    return P4Screen::kApplying;
  }
  if (state_is_one_of(state, "validating")) {
    return P4Screen::kValidating;
  }
  if (state_is_one_of(state, "pairing_advert_seen", "pairing_connected", "pairing_offer_received", "pairing_identity_sent")) {
    return P4Screen::kPairing;
  }
  return P4Screen::kWaiting;
}

const char *screen_title(P4Screen screen) {
  switch (screen) {
    case P4Screen::kPairing:
      return "Pairing with Core";
    case P4Screen::kValidating:
      return "Checking credentials";
    case P4Screen::kApplying:
      return "Saving credentials";
    case P4Screen::kComplete:
      return "Onboarding saved";
    case P4Screen::kFailed:
      return "Onboarding failed";
    case P4Screen::kOta:
      return "Firmware update";
    case P4Screen::kWaiting:
    default:
      return "Waiting for onboarding";
  }
}

const char *screen_detail(P4Screen screen) {
  switch (screen) {
    case P4Screen::kPairing:
      return "Keep this device powered";
    case P4Screen::kValidating:
      return "Verifying encrypted payload";
    case P4Screen::kApplying:
      return "Writing local recovery settings";
    case P4Screen::kComplete:
      return "Credentials stored";
    case P4Screen::kFailed:
      return "Use app or AP rescue to retry";
    case P4Screen::kOta:
      return "Installing endpoint firmware";
    case P4Screen::kWaiting:
    default:
      return "BLE ready   AP 192.168.4.1";
  }
}

uint32_t screen_accent(P4Screen screen) {
  switch (screen) {
    case P4Screen::kPairing:
      return kCyan;
    case P4Screen::kValidating:
      return kYellow;
    case P4Screen::kApplying:
      return kOrange;
    case P4Screen::kComplete:
      return kGreen;
    case P4Screen::kFailed:
      return kRed;
    case P4Screen::kOta:
      return kMagenta;
    case P4Screen::kWaiting:
    default:
      return kBlue;
  }
}

bool screen_animates(P4Screen screen) {
  return screen == P4Screen::kWaiting || screen == P4Screen::kPairing || screen == P4Screen::kValidating ||
         screen == P4Screen::kApplying || screen == P4Screen::kOta;
}

uint32_t hash_text(uint32_t signature, const char *text) {
  if (text == nullptr) {
    return signature * 131;
  }
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    signature = (signature * 131) + static_cast<unsigned char>(*cursor);
  }
  return signature;
}

uint32_t display_signature(P4Screen screen, uint32_t frame) {
  uint32_t signature = 2166136261U;
  signature = (signature * 131) + static_cast<uint32_t>(screen);
  signature = hash_text(signature, hexe::recovery::recovery_ble_state());
  signature = hash_text(signature, hexe::recovery::recovery_ble_reason());
  if (screen == P4Screen::kOta) {
    signature = (signature * 131) + std::clamp(hexe::recovery::recovery_firmware_install_progress_percent(), 0, 100);
  }
  if (screen_animates(screen)) {
    signature = (signature * 131) + (frame % 12);
  }
  return signature;
}

void set_pixel(int x, int y, uint32_t color) {
  if (g_flush_buffer == nullptr || x < 0 || y < g_strip_y || x >= kWidth || y >= g_strip_y + g_strip_rows) {
    return;
  }
  uint8_t *pixel = g_flush_buffer + (((y - g_strip_y) * kWidth + x) * kBytesPerPixel);
  pixel[0] = static_cast<uint8_t>(color & 0xFF);
  pixel[1] = static_cast<uint8_t>((color >> 8) & 0xFF);
  pixel[2] = static_cast<uint8_t>((color >> 16) & 0xFF);
}

void fill_rect(int x, int y, int width, int height, uint32_t color) {
  const int x0 = std::clamp(x, 0, kWidth);
  const int y0 = std::clamp(y, g_strip_y, g_strip_y + g_strip_rows);
  const int x1 = std::clamp(x + width, 0, kWidth);
  const int y1 = std::clamp(y + height, g_strip_y, g_strip_y + g_strip_rows);
  for (int row = y0; row < y1; ++row) {
    for (int col = x0; col < x1; ++col) {
      set_pixel(col, row, color);
    }
  }
}

void fill_procedural_background() {
  for (int row = 0; row < g_strip_rows; ++row) {
    const int y = g_strip_y + row;
    const bool alt_band = ((y / 40) % 2) == 0;
    const uint32_t base = alt_band ? kCanvas : kCanvasAlt;
    for (int x = 0; x < kWidth; ++x) {
      set_pixel(x, y, base);
    }
  }
}

void fill_background_strip() {
  if (g_background_pixels == nullptr) {
    fill_procedural_background();
    return;
  }
  std::memcpy(g_flush_buffer,
              g_background_pixels + (static_cast<size_t>(g_strip_y) * static_cast<size_t>(kWidth) * kBytesPerPixel),
              static_cast<size_t>(kWidth) * static_cast<size_t>(g_strip_rows) * kBytesPerPixel);
}

void draw_disc(int center_x, int center_y, int radius, uint32_t color) {
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

void draw_ring(int center_x, int center_y, int radius, int thickness, uint32_t color) {
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

void draw_char(int x, int y, char ch, int scale_percent, uint32_t color) {
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

void draw_text(int x, int y, const char *text, int scale_percent, uint32_t color) {
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

void draw_centered_text(int y, const char *text, int scale_percent, uint32_t color) {
  draw_text((kWidth - text_width(text, scale_percent)) / 2, y, text, scale_percent, color);
}

void sanitize_reason(char *target, size_t size, const char *reason) {
  if (target == nullptr || size == 0) {
    return;
  }
  if (reason == nullptr || reason[0] == '\0') {
    std::snprintf(target, size, "state %s", hexe::recovery::recovery_ble_state());
    return;
  }
  size_t out = 0;
  for (const char *cursor = reason; *cursor != '\0' && out + 1 < size; ++cursor) {
    char ch = *cursor;
    if (ch == '_' || ch == '/') {
      ch = ' ';
    }
    target[out++] = ch;
  }
  target[out] = '\0';
}

void format_base_mac(char *target, size_t size) {
  if (target == nullptr || size == 0) {
    return;
  }
  uint8_t mac[6] = {};
  if (esp_efuse_mac_get_default(mac) != ESP_OK) {
    std::snprintf(target, size, "unknown");
    return;
  }
  std::snprintf(target,
                size,
                "%02X:%02X:%02X:%02X:%02X:%02X",
                mac[0],
                mac[1],
                mac[2],
                mac[3],
                mac[4],
                mac[5]);
}

bool try_load_sd_background(const char *path) {
  struct stat info = {};
  if (stat(path, &info) != 0) {
    ESP_LOGI(kTag, "P4 recovery background not found at %s: %s", path, std::strerror(errno));
    return false;
  }
  if (static_cast<size_t>(info.st_size) != kBackgroundBytes) {
    ESP_LOGW(kTag,
             "Ignoring P4 recovery background %s: expected %u bytes for %dx%d RGB888, got %ld",
             path,
             static_cast<unsigned>(kBackgroundBytes),
             kWidth,
             kHeight,
             static_cast<long>(info.st_size));
    return false;
  }

  if (g_background_pixels == nullptr) {
    g_background_pixels = static_cast<uint8_t *>(heap_caps_malloc(kBackgroundBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  }
  if (g_background_pixels == nullptr) {
    ESP_LOGW(kTag, "No PSRAM available for P4 recovery background cache; using procedural background");
    return false;
  }

  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Could not open P4 recovery background %s: %s", path, std::strerror(errno));
    return false;
  }
  const size_t read_bytes = std::fread(g_background_pixels, 1, kBackgroundBytes, file);
  std::fclose(file);
  if (read_bytes != kBackgroundBytes) {
    ESP_LOGW(kTag,
             "Could not read P4 recovery background %s: expected %u bytes, got %u",
             path,
             static_cast<unsigned>(kBackgroundBytes),
             static_cast<unsigned>(read_bytes));
    heap_caps_free(g_background_pixels);
    g_background_pixels = nullptr;
    return false;
  }
  for (size_t offset = 0; offset < kBackgroundBytes; offset += kBytesPerPixel) {
    std::swap(g_background_pixels[offset], g_background_pixels[offset + 2]);
  }

  g_background_source = std::strcmp(path, kRecoveryBackgroundPath) == 0 ? "SD recovery_bg.rgb888" : "SD bg.rgb888";
  ESP_LOGI(kTag, "Loaded P4 recovery background from %s", path);
  return true;
}

void init_p4_sd_background() {
  if (g_sd_checked) {
    return;
  }
  g_sd_checked = true;
  const esp_err_t mount_result = bsp_sdcard_mount();
  if (mount_result != ESP_OK && mount_result != ESP_ERR_INVALID_STATE) {
    ESP_LOGI(kTag, "microSD unavailable for recovery background; using procedural background: %s", esp_err_to_name(mount_result));
    return;
  }
  if (!try_load_sd_background(kRecoveryBackgroundPath)) {
    try_load_sd_background(kFallbackBackgroundPath);
  }
}

void draw_progress_bar(int x, int y, int width, int height, int progress, uint32_t accent) {
  fill_rect(x, y, width, height, rgb565_to_rgb888(0x2965));
  fill_rect(x, y, (width * std::clamp(progress, 0, 100)) / 100, height, accent);
}

void draw_activity_dots(uint32_t frame, uint32_t accent) {
  constexpr int start_x = 439;
  constexpr int center_y = 506;
  for (int i = 0; i < 6; ++i) {
    const bool lit = static_cast<int>(frame % 6) == i;
    draw_disc(start_x + (i * 30), center_y, lit ? 9 : 5, lit ? accent : rgb565_to_rgb888(0x39E7));
  }
}

void draw_p4_frame(P4Screen screen, uint32_t frame) {
  const uint32_t accent = screen_accent(screen);
  const bool ota = screen == P4Screen::kOta;
  const int progress = ota ? std::clamp(hexe::recovery::recovery_firmware_install_progress_percent(), 0, 100) : 0;
  char reason[72] = {};
  sanitize_reason(reason, sizeof(reason), ota ? hexe::recovery::recovery_firmware_install_state()
                                             : hexe::recovery::recovery_ble_reason());
  char state_text[72] = {};
  sanitize_reason(state_text, sizeof(state_text), ota ? "firmware_install" : hexe::recovery::recovery_ble_state());
  char status_line[96] = {};
  std::snprintf(status_line,
                sizeof(status_line),
                "BLE %s   HTTP %s",
                hexe::recovery::recovery_ble_advertising() ? "advertising" : "ready",
                hexe::recovery::recovery_http_api_active() ? hexe::recovery::recovery_http_mode() : "off");
  char name_line[96] = {};
  char id_line[96] = {};
  char base_mac[24] = {};
  char ble_mac[24] = {};
  format_base_mac(base_mac, sizeof(base_mac));
  const char *local_ble_address = hexe_ble_provisioning_local_address();
  std::snprintf(ble_mac, sizeof(ble_mac), "%s", local_ble_address == nullptr || local_ble_address[0] == '\0' ? "starting" : local_ble_address);
  std::snprintf(name_line, sizeof(name_line), "Name %.28s", hexe::config::kEndpointId);
  std::snprintf(id_line, sizeof(id_line), "Device id %.24s", hexe::config::kEndpointId);
  char base_line[48] = {};
  char ble_line[48] = {};
  std::snprintf(base_line, sizeof(base_line), "Base MAC %s", base_mac);
  std::snprintf(ble_line, sizeof(ble_line), "BLE MAC %s", ble_mac);
  const esp_app_desc_t *app = esp_app_get_description();
  char version[96] = {};
  std::snprintf(version, sizeof(version), "P4 7B recovery   %s", app != nullptr ? app->version : "unknown");

  for (int y = 0; y < kHeight; y += kFlushRows) {
    g_strip_y = y;
    g_strip_rows = std::min(kFlushRows, kHeight - y);
    fill_background_strip();
    fill_rect(0, 0, kWidth, 78, kBlack);
    fill_rect(0, kHeight - 68, kWidth, 68, kBlack);
    fill_rect(0, 78, kWidth, 5, accent);
    fill_rect(0, kHeight - 73, kWidth, 5, accent);
    fill_rect(0, 84, kWidth, 128, kCanvasAlt);

    draw_text(46, 26, "HEXE", 430, kInk);
    draw_text(214, 38, "RECOVERY", 230, accent);
    draw_text(674, 38, status_line, 150, kMuted);
    draw_centered_text(128, screen_title(screen), 430, kInk);
    draw_centered_text(232, screen_detail(screen), 230, accent);
    draw_centered_text(288, state_text, 190, kInk);
    draw_centered_text(322, reason, 180, kMuted);
    draw_text(76, 372, name_line, 170, kInk);
    draw_text(76, 402, id_line, 160, kMuted);
    draw_text(76, 432, base_line, 160, kMuted);
    draw_text(548, 432, ble_line, 160, kMuted);

    if (ota) {
      draw_progress_bar(272, 486, 480, 22, progress, accent);
      char progress_text[24] = {};
      std::snprintf(progress_text, sizeof(progress_text), "%d percent", progress);
      draw_centered_text(518, progress_text, 160, kMuted);
    } else if (screen_animates(screen)) {
      draw_ring(kWidth / 2, 484, 24, 6, accent);
      draw_activity_dots(frame, accent);
    } else if (screen == P4Screen::kComplete) {
      draw_disc(kWidth / 2, 494, 30, accent);
      draw_text((kWidth / 2) - 16, 474, "OK", 210, kBlack);
    } else {
      draw_ring(kWidth / 2, 494, 32, 7, accent);
    }
    draw_text(48, 552, version, 175, kMuted);
    draw_text(420, 552, g_background_source, 130, kMuted);
    draw_text(744, 552, "no secrets on screen", 160, kMuted);

    while (g_wait_for_refresh && g_refresh_done != nullptr && xSemaphoreTake(g_refresh_done, 0) == pdTRUE) {
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, y, kWidth, y + g_strip_rows, g_flush_buffer));
    if (g_wait_for_refresh && g_refresh_done != nullptr && xSemaphoreTake(g_refresh_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
      ESP_LOGW(kTag, "Timed out waiting for P4 LCD refresh");
    }
  }
}
#endif

#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
constexpr int kWidth = 360;
constexpr int kHeight = 360;
// Waveshare's reference firmware caps ST77916 QSPI transfers at 2048 bytes.
// Two 360px RGB565 rows are 1440 bytes, keeping every color burst under that
// limit while still avoiding a full-frame buffer in the recovery image.
constexpr int kFlushRows = 2;
constexpr size_t kAssetBytes = kWidth * kHeight * sizeof(uint16_t);
constexpr spi_host_device_t kDisplaySpiHost = SPI2_HOST;
constexpr int kDisplayReadIdClockHz = 3 * 1000 * 1000;
constexpr int kDisplayPixelClockHz = 10 * 1000 * 1000;
constexpr int kLcdOpcodeReadCommand = 0x0B;
constexpr uint8_t kTcaRegisterOutput = 0x01;
constexpr uint8_t kTcaRegisterConfig = 0x03;
constexpr uint8_t kDisplayResetBit = 1;
constexpr int kI2cTimeoutMs = 1000;

extern const uint8_t _binary_min_fw_waiting_to_pair_rgb565_start[] asm("_binary_min_fw_waiting_to_pair_rgb565_start");
extern const uint8_t _binary_min_fw_waiting_to_pair_rgb565_end[] asm("_binary_min_fw_waiting_to_pair_rgb565_end");
extern const uint8_t _binary_min_fw_pairing_rgb565_start[] asm("_binary_min_fw_pairing_rgb565_start");
extern const uint8_t _binary_min_fw_pairing_rgb565_end[] asm("_binary_min_fw_pairing_rgb565_end");
extern const uint8_t _binary_ota_progress_rgb565_start[] asm("_binary_ota_progress_rgb565_start");
extern const uint8_t _binary_ota_progress_rgb565_end[] asm("_binary_ota_progress_rgb565_end");

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

enum class Plate {
  kWaiting,
  kPairing,
  kOta,
};

struct Asset {
  const uint8_t *start;
  const uint8_t *end;
};

esp_lcd_panel_handle_t g_panel = nullptr;
uint16_t *g_flush_buffer = nullptr;
SemaphoreHandle_t g_flush_done = nullptr;
i2c_master_bus_handle_t g_i2c_bus = nullptr;
i2c_master_dev_handle_t g_io_expander = nullptr;
uint8_t g_io_expander_output = 0xFF;
bool g_display_ready = false;
Plate g_current_plate = Plate::kWaiting;
int g_last_progress = -1;
bool g_force_redraw = true;

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

constexpr uint16_t swap565(uint16_t value) {
  return static_cast<uint16_t>((value >> 8) | (value << 8));
}

bool on_color_transfer_done(esp_lcd_panel_io_handle_t panel_io, esp_lcd_panel_io_event_data_t *edata, void *user_ctx);

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
  ESP_LOGI(kTag,
           "ST77916 register 0x04 data: %02x %02x %02x %02x",
           register_data[0],
           register_data[1],
           register_data[2],
           register_data[3]);
  return true;
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

void apply_display_rotation() {
  constexpr int rotation = hexe::board::display_config::kRotationDeg;
  static_assert(rotation == 0 || rotation == 90 || rotation == 180 || rotation == 270, "unsupported display rotation");
  const bool swap_xy = rotation == 90 || rotation == 270;
  const bool mirror_x = rotation == 90 || rotation == 180;
  const bool mirror_y = rotation == 180 || rotation == 270;
  ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(g_panel, swap_xy));
  ESP_ERROR_CHECK(esp_lcd_panel_mirror(g_panel, mirror_x, mirror_y));
  ESP_LOGI(kTag, "Recovery display rotation configured: %d deg", rotation);
}

bool tca_write(uint8_t reg, uint8_t value) {
  if (g_io_expander == nullptr) {
    return false;
  }
  const uint8_t data[] = {reg, value};
  const esp_err_t result = i2c_master_transmit(g_io_expander, data, sizeof(data), kI2cTimeoutMs);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "TCA9554 write failed reg=0x%02x value=0x%02x err=%s", reg, value, esp_err_to_name(result));
    return false;
  }
  return true;
}

bool init_i2c_bus() {
  if (g_i2c_bus != nullptr) {
    return true;
  }
  i2c_master_bus_config_t bus_config = {};
  bus_config.i2c_port = static_cast<i2c_port_num_t>(hexe::board::pins::kWs185I2cPort);
  bus_config.sda_io_num = gpio_pin(hexe::board::pins::kWs185I2cSda);
  bus_config.scl_io_num = gpio_pin(hexe::board::pins::kWs185I2cScl);
  bus_config.clk_source = I2C_CLK_SRC_DEFAULT;
  bus_config.glitch_ignore_cnt = 7;
  bus_config.flags.enable_internal_pullup = true;
  const esp_err_t result = i2c_new_master_bus(&bus_config, &g_i2c_bus);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(kTag, "Failed to initialize Waveshare I2C bus: %s", esp_err_to_name(result));
    g_i2c_bus = nullptr;
    return false;
  }
  return true;
}

bool init_io_expander() {
  if (g_io_expander != nullptr) {
    return true;
  }
  if (!init_i2c_bus()) {
    return false;
  }
  i2c_device_config_t device_config = {};
  device_config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  device_config.device_address = hexe::board::pins::kWs185IoExpanderAddress;
  device_config.scl_speed_hz = hexe::board::pins::kWs185I2cClockHz;
  esp_err_t result = i2c_master_bus_add_device(g_i2c_bus, &device_config, &g_io_expander);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(kTag, "Failed to add Waveshare TCA9554: %s", esp_err_to_name(result));
    g_io_expander = nullptr;
    return false;
  }
  const uint8_t display_mask = static_cast<uint8_t>(1U << kDisplayResetBit);
  g_io_expander_output |= display_mask;
  return tca_write(kTcaRegisterOutput, g_io_expander_output) &&
         tca_write(kTcaRegisterConfig, static_cast<uint8_t>(~display_mask));
}

bool pulse_display_reset() {
  if (!init_io_expander()) {
    return false;
  }
  g_io_expander_output = static_cast<uint8_t>(g_io_expander_output & ~(1U << kDisplayResetBit));
  if (!tca_write(kTcaRegisterOutput, g_io_expander_output)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(20));
  g_io_expander_output = static_cast<uint8_t>(g_io_expander_output | (1U << kDisplayResetBit));
  if (!tca_write(kTcaRegisterOutput, g_io_expander_output)) {
    return false;
  }
  vTaskDelay(pdMS_TO_TICKS(120));
  return true;
}

Asset asset_for_plate(Plate plate) {
  switch (plate) {
    case Plate::kPairing:
      return {_binary_min_fw_pairing_rgb565_start, _binary_min_fw_pairing_rgb565_end};
    case Plate::kOta:
      return {_binary_ota_progress_rgb565_start, _binary_ota_progress_rgb565_end};
    case Plate::kWaiting:
    default:
      return {_binary_min_fw_waiting_to_pair_rgb565_start, _binary_min_fw_waiting_to_pair_rgb565_end};
  }
}

bool pairing_active() {
  const char *state = hexe::recovery::recovery_ble_state();
  if (state == nullptr) {
    return false;
  }
  return std::strcmp(state, "pairing_advert_seen") == 0 ||
         std::strcmp(state, "pairing_connected") == 0 ||
         std::strcmp(state, "pairing_offer_received") == 0 ||
         std::strcmp(state, "pairing_identity_sent") == 0 ||
         std::strcmp(state, "pairing_failed") == 0 ||
         std::strcmp(state, "completed") == 0;
}

Plate desired_plate() {
  if (hexe::recovery::recovery_firmware_install_active()) {
    return Plate::kOta;
  }
  if (pairing_active()) {
    return Plate::kPairing;
  }
  return Plate::kWaiting;
}

void overlay_progress_row(int absolute_y, uint16_t *row, int progress) {
  constexpr int kBarX = 88;
  constexpr int kBarY = 263;
  constexpr int kBarW = 184;
  constexpr int kBarH = 8;
  if (absolute_y < kBarY || absolute_y >= (kBarY + kBarH)) {
    return;
  }
  const int fill_width = (kBarW * std::clamp(progress, 0, 100)) / 100;
  const uint16_t fill = swap565(0x07FF);
  const uint16_t empty = swap565(0x18C3);
  for (int x = 0; x < kBarW; ++x) {
    row[kBarX + x] = x < fill_width ? fill : empty;
  }
}

void render_plate(Plate plate, int progress) {
  if (!g_display_ready || g_flush_buffer == nullptr) {
    return;
  }
  const Asset asset = asset_for_plate(plate);
  const size_t bytes = static_cast<size_t>(asset.end - asset.start);
  if (bytes != kAssetBytes) {
    ESP_LOGW(kTag, "Skipping recovery display asset with unexpected size=%u", static_cast<unsigned>(bytes));
    return;
  }

  for (int y = 0; y < kHeight; y += kFlushRows) {
    const int rows = std::min(kFlushRows, kHeight - y);
    for (int row = 0; row < rows; ++row) {
      const auto *source = reinterpret_cast<const uint16_t *>(asset.start + ((y + row) * kWidth * sizeof(uint16_t)));
      auto *target = g_flush_buffer + (row * kWidth);
      for (int x = 0; x < kWidth; ++x) {
        target[x] = swap565(source[x]);
      }
      if (plate == Plate::kOta) {
        overlay_progress_row(y + row, g_flush_buffer + (row * kWidth), progress);
      }
    }
    while (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, 0) == pdTRUE) {
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, y, kWidth, y + rows, g_flush_buffer));
    if (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
      ESP_LOGW(kTag, "Timed out waiting for LCD flush completion");
    }
  }
}
#endif

}  // namespace

namespace hexe::recovery {

void init_recovery_display() {
#if HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B
  if (g_display_ready) {
    return;
  }

  g_refresh_done = xSemaphoreCreateBinary();
  const size_t flush_buffer_bytes = static_cast<size_t>(kWidth) * kFlushRows * kBytesPerPixel;
  g_flush_buffer = static_cast<uint8_t *>(heap_caps_malloc(flush_buffer_bytes, MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_flush_buffer == nullptr) {
    g_flush_buffer = static_cast<uint8_t *>(
        heap_caps_malloc(flush_buffer_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  }
  if (g_flush_buffer == nullptr || g_refresh_done == nullptr) {
    ESP_LOGW(kTag, "Recovery display disabled: P4 buffer allocation failed");
    return;
  }

  const esp_err_t result = bsp_display_new(nullptr, &g_panel, &g_panel_io);
  if (result != ESP_OK || g_panel == nullptr) {
    ESP_LOGW(kTag, "Recovery display disabled: P4 panel init failed: %s", esp_err_to_name(result));
    return;
  }

  esp_lcd_dpi_panel_event_callbacks_t callbacks = {};
  callbacks.on_color_trans_done = on_color_done;
  const esp_err_t callback_result = esp_lcd_dpi_panel_register_event_callbacks(g_panel, &callbacks, g_refresh_done);
  if (callback_result == ESP_OK) {
    g_wait_for_refresh = true;
  } else {
    ESP_LOGW(kTag, "P4 recovery LCD refresh callback unavailable: %s", esp_err_to_name(callback_result));
  }

  esp_err_t display_on_result = esp_lcd_panel_disp_on_off(g_panel, true);
  if (display_on_result == ESP_ERR_NOT_SUPPORTED && g_panel_io != nullptr) {
    ESP_LOGW(kTag, "Panel display-on callback unavailable; sending DCS display-on command");
    display_on_result = esp_lcd_panel_io_tx_param(g_panel_io, LCD_CMD_DISPON, nullptr, 0);
  }
  if (display_on_result != ESP_OK) {
    ESP_LOGW(kTag, "Recovery display disabled: P4 panel enable failed: %s", esp_err_to_name(display_on_result));
    return;
  }

  init_p4_sd_background();
  g_display_ready = true;
  g_force_redraw = true;
  update_recovery_display();
  const esp_err_t backlight_result = bsp_display_backlight_on();
  if (backlight_result == ESP_OK) {
    g_backlight_on = true;
  } else {
    ESP_LOGW(kTag, "Failed to enable P4 recovery LCD backlight: %s", esp_err_to_name(backlight_result));
  }
  ESP_LOGI(kTag, "Recovery display initialized for Waveshare P4 7B RGB888");
#elif HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
  if (g_display_ready) {
    return;
  }

  gpio_config_t backlight_config = {};
  backlight_config.pin_bit_mask = 1ULL << hexe::board::pins::kWs185DisplayBacklight;
  backlight_config.mode = GPIO_MODE_OUTPUT;
  gpio_config(&backlight_config);
  gpio_set_level(gpio_pin(hexe::board::pins::kWs185DisplayBacklight), 0);

  g_flush_done = xSemaphoreCreateBinary();
  g_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kFlushRows * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_flush_buffer == nullptr || g_flush_done == nullptr) {
    ESP_LOGW(kTag, "Recovery display disabled: buffer allocation failed");
    return;
  }

  spi_bus_config_t bus_config = {};
  bus_config.data0_io_num = hexe::board::pins::kWs185DisplayData0;
  bus_config.data1_io_num = hexe::board::pins::kWs185DisplayData1;
  bus_config.sclk_io_num = hexe::board::pins::kWs185DisplayClk;
  bus_config.data2_io_num = hexe::board::pins::kWs185DisplayData2;
  bus_config.data3_io_num = hexe::board::pins::kWs185DisplayData3;
  bus_config.data4_io_num = -1;
  bus_config.data5_io_num = -1;
  bus_config.data6_io_num = -1;
  bus_config.data7_io_num = -1;
  bus_config.max_transfer_sz = kWidth * kFlushRows * sizeof(uint16_t);
  esp_err_t result = spi_bus_initialize(kDisplaySpiHost, &bus_config, SPI_DMA_CH_AUTO);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGW(kTag, "Recovery display disabled: SPI init failed: %s", esp_err_to_name(result));
    return;
  }

  if (!pulse_display_reset()) {
    ESP_LOGW(kTag, "Recovery display reset pulse failed; attempting panel init anyway");
  }
  uint8_t panel_id[4] = {};
  const bool panel_id_read = read_panel_id(panel_id);

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  esp_lcd_panel_io_spi_config_t io_config = make_panel_io_config(kDisplayPixelClockHz, true);
  result = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kDisplaySpiHost), &io_config, &io_handle);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Recovery display disabled: panel IO failed: %s", esp_err_to_name(result));
    return;
  }

  st77916_vendor_config_t vendor_config = {};
  vendor_config.init_cmds = kWavesharePanelInit;
  vendor_config.init_cmds_size = sizeof(kWavesharePanelInit) / sizeof(kWavesharePanelInit[0]);
  vendor_config.flags.use_qspi_interface = 1;
  ESP_LOGI(kTag,
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
    ESP_LOGW(kTag, "Recovery display disabled: panel create failed: %s", esp_err_to_name(result));
    return;
  }
  ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
  apply_display_rotation();
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));
  g_display_ready = true;
  g_force_redraw = true;
  update_recovery_display();
  gpio_set_level(gpio_pin(hexe::board::pins::kWs185DisplayBacklight), 1);
  ESP_LOGI(kTag, "Recovery display initialized for Waveshare 1.85");
#else
  ESP_LOGI(kTag, "Recovery display UI is not enabled for this board profile");
#endif
}

void update_recovery_display() {
#if HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B
  if (!g_display_ready) {
    return;
  }
  const P4Screen screen = desired_p4_screen();
  const uint32_t signature = display_signature(screen, g_frame);
  ++g_frame;
  if (!g_force_redraw && signature == g_last_signature) {
    return;
  }
  draw_p4_frame(screen, g_frame);
  g_last_signature = signature;
  g_force_redraw = false;
#elif HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
  if (!g_display_ready) {
    return;
  }
  const Plate plate = desired_plate();
  const int progress = plate == Plate::kOta ? hexe::recovery::recovery_firmware_install_progress_percent() : -1;
  if (!g_force_redraw && plate == g_current_plate && progress == g_last_progress) {
    return;
  }
  render_plate(plate, progress);
  g_current_plate = plate;
  g_last_progress = progress;
  g_force_redraw = false;
#endif
}

bool recovery_display_ready() {
#if HEXE_BOARD_PROFILE_WAVESHARE_P4_WIFI6_TOUCH_LCD_7B || HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
  return g_display_ready;
#else
  return false;
#endif
}

}  // namespace hexe::recovery
