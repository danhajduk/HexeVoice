#include "board/display.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <sys/stat.h>

#include "app_state.h"
#include "board/storage.h"
#include "bsp/display.h"
#include "cJSON.h"
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
#include "system/asset_sync.h"
#include "system/clock.h"

namespace {
constexpr char kTag[] = "hexe_display_p4_7b";
constexpr int kWidth = BSP_LCD_H_RES;
constexpr int kHeight = BSP_LCD_V_RES;
constexpr int kFlushRows = 120;
constexpr int kBytesPerPixel = 3;

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
constexpr uint32_t kMuted = rgb565_to_rgb888(0x7BEF);
constexpr uint32_t kCyan = rgb565_to_rgb888(0x05FF);
constexpr uint32_t kGreen = rgb565_to_rgb888(0x37E6);
constexpr uint32_t kYellow = rgb565_to_rgb888(0xFEE0);
constexpr uint32_t kOrange = rgb565_to_rgb888(0xFCA0);
constexpr uint32_t kRed = rgb565_to_rgb888(0xF926);
constexpr uint32_t kMagenta = rgb565_to_rgb888(0xD29F);
constexpr uint32_t kBlue = rgb565_to_rgb888(0x03BF);
constexpr char kProceduralAssetName[] = "procedural-p4-7b-status";
constexpr char kSdTestBackgroundName[] = "bg.rgb888";
constexpr char kStatusLayoutFilename[] = "status_layout.json";
constexpr char kDefaultClockFont[] = "manrope/clock_42.hxf";
constexpr char kDefaultVersionFont[] = "manrope/version_24.hxf";
constexpr char kDefaultIdleClockFont[] = "manrope/clock_42.hxf";
constexpr char kDefaultIdleDateFont[] = "manrope/date_32.hxf";
constexpr size_t kSdTestBackgroundBytes =
    static_cast<size_t>(kWidth) * static_cast<size_t>(kHeight) * kBytesPerPixel;
constexpr int kWifiSpriteSize = 40;
constexpr int kStatusSpriteSize = 40;
constexpr int kSidebarWidth = 88;
constexpr int kSidebarTop = 78;
constexpr int kSidebarHeight = 477;
constexpr int kIdleClockFrameWidth = 500;
constexpr int kIdleClockFrameHeight = 210;
constexpr int kActivitySpriteSize = 180;
constexpr int kSidebarButtonSize = 56;
constexpr size_t kActivitySpriteCount = 4;
constexpr size_t kSidebarButtonCount = 3;
constexpr size_t kStatusLayoutMaxBytes = 8192;
constexpr size_t kMaxStatusAnimations = 4;
constexpr size_t kMaxClockFontBytes = 64 * 1024;
constexpr size_t kFontGlyphCount = 96;

enum class StatusIconId : uint8_t {
  kWifi = 0,
  kNodeConnected,
  kAssetDownloading,
  kMicEnabled,
  kMicDisabled,
  kMicActive,
  kMute,
  kDnd,
  kTimer,
  kAlarm,
  kPlayback,
  kUpdateAvailable,
  kWarning,
  kPrivacy,
  kCloudOffline,
  kCount,
};

enum class StatusAnimationType : uint8_t {
  kBlinkDot,
  kRunningDots,
  kPulse,
  kPulseRing,
  kSlideIn,
  kInvalid,
};

enum class StatusFlag : uint8_t {
  kHeartbeat,
  kLoading,
  kWifiConnected,
  kWifiConnecting,
  kBackendConnected,
  kBackendConnecting,
  kVoiceWsConnected,
  kAssetSyncActive,
  kMediaTransferActive,
  kOtaActive,
  kListening,
  kThinking,
  kReplying,
  kMuted,
  kTimerActive,
  kTimerFinished,
  kError,
  kUiReady,
  kIdleReady,
  kMicrophoneEnabled,
  kMicrophoneDisabled,
  kMicrophoneActive,
  kDnd,
  kAlarmActive,
  kPlaybackActive,
  kUpdateAvailable,
  kWarningActive,
  kPrivacyMode,
  kCloudOffline,
  kInvalid,
};

struct StatusAnimation {
  StatusAnimationType type = StatusAnimationType::kInvalid;
  StatusFlag flag = StatusFlag::kInvalid;
  bool expected = true;
  uint32_t color = kCyan;
  int x_per_mille = 500;
  int y_per_mille = 500;
  int radius_per_mille = 80;
  int spacing_per_mille = 180;
  int offset_x_per_mille = -1000;
  int offset_y_per_mille = 0;
  int period_ms = 1000;
  int count = 3;
  int min_opacity = 128;
  int max_opacity = 255;
};

struct SlideAnimationState {
  bool active = false;
  int64_t started_ms = 0;
};

struct StatusIconLayout {
  bool floating;
  int x;
  StatusAnimation animations[kMaxStatusAnimations] = {};
  size_t animation_count = 0;
};

struct AnimatedSpriteLayout {
  int x;
  int y;
  StatusAnimation animations[kMaxStatusAnimations] = {};
  size_t animation_count = 0;
};

struct AnimatedTextLayout {
  int x = kWidth / 2;
  int y = 250;
  int font_size = 96;
  uint32_t color = kInk;
  char font[96] = {};
  StatusAnimation animations[kMaxStatusAnimations] = {};
  size_t animation_count = 0;
};

struct StatusLayout {
  struct IdleClock {
    bool enabled = true;
    char date_format[32] = "%A, %B %d %Y.";
    struct Frame {
      int x = (kWidth - kIdleClockFrameWidth) / 2;
      int y = 205;
      StatusAnimation animations[kMaxStatusAnimations] = {};
      size_t animation_count = 0;
    } frame;
    AnimatedTextLayout hours = {415, 240, 118, 0xD8FFFA};
    AnimatedTextLayout separator = {512, 247, 98, 0x35F4DB};
    AnimatedTextLayout minutes = {609, 240, 118, 0xD8FFFA};
    AnimatedTextLayout date = {512, 370, 30, 0x55B8FF};
  } idle_clock;
  struct Sidebars {
    StatusAnimation left = {
        StatusAnimationType::kSlideIn, StatusFlag::kUiReady, true, kCyan, 500, 500, 80, 180, -1000, 0, 450};
    StatusAnimation right = {
        StatusAnimationType::kSlideIn, StatusFlag::kUiReady, true, kCyan, 500, 500, 80, 180, 1000, 0, 450};
  } sidebars;
  struct ActivitySprites {
    AnimatedSpriteLayout items[kActivitySpriteCount] = {
        {422, 205}, {422, 205}, {422, 205}, {422, 205}};
  } activity_sprites;
  struct TimerScreen {
    bool enabled = true;
    AnimatedTextLayout primary_countdown = {270, 450, 64, 0xD8FFFA};
    AnimatedTextLayout primary_label = {270, 525, 24, 0x35F4DB};
    struct Upcoming {
      int x = 760;
      int y = 438;
      int gap = 42;
      int count = 3;
      int font_size = 25;
      uint32_t color = 0x55B8FF;
      char font[96] = {};
    } upcoming;
  } timer_screen;
  struct SidebarButtons {
    bool enabled = true;
    int x = 16;
    int y = 112;
    int gap = 18;
  } sidebar_buttons;
  struct Clock {
    int font_size = 42;
    int x_offset = 0;
    int y_offset = 0;
    uint32_t color = kCyan;
    char font[96] = {};
  } clock;
  struct Version {
    int font_size = 18;
    int x = 24;
    int y = 568;
    uint32_t color = kBlue;
    char font[96] = {};
  } version;
  int y = 12;
  int floating_x = 20;
  int floating_gap = 0;
  StatusIconLayout icons[static_cast<size_t>(StatusIconId::kCount)] = {
      {false, 920},
      {false, 880},
      {true, 0},
  };
  StatusIconId floating_order[static_cast<size_t>(StatusIconId::kCount)] = {
      StatusIconId::kAssetDownloading,
  };
  size_t floating_count = 1;
};

struct ClockGlyph {
  char code = '\0';
  int left = 0;
  int top = 0;
  int advance = 0;
  int width = 0;
  int height = 0;
  uint32_t bitmap_offset = 0;
};

struct ClockFont {
  uint8_t *data = nullptr;
  size_t size = 0;
  int pixel_size = 0;
  int ascent = 0;
  ClockGlyph glyphs[kFontGlyphCount] = {};
  size_t glyph_count = 0;
  bool load_attempted = false;
};

struct StatusSprite {
  const char *name;
  int width;
  int height;
  uint8_t *colors = nullptr;
  uint8_t *alpha = nullptr;
  bool load_attempted = false;
};

esp_lcd_panel_handle_t g_panel = nullptr;
esp_lcd_panel_io_handle_t g_panel_io = nullptr;
uint8_t *g_flush_buffer = nullptr;
uint8_t *g_background_pixels = nullptr;
SemaphoreHandle_t g_refresh_done = nullptr;
bool g_display_ready = false;
bool g_backlight_on = false;
bool g_wait_for_refresh = false;
bool g_force_redraw = true;
std::atomic<bool> g_display_assets_reload_requested{false};
int g_strip_y = 0;
int g_strip_rows = 0;
int g_last_signature = -1;
int g_last_asset_read_ms = 0;
int g_last_flush_ms = 0;
int g_last_render_ms = 0;
int64_t g_frame_time_ms = 0;
char g_last_asset_filename[128] = "procedural-p4-7b-status";
bool g_logged_sd_unavailable = false;
bool g_logged_bg_missing = false;
bool g_logged_bg_bad_size = false;
StatusSprite g_wifi_on_sprite{"wifi_on", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_wifi_off_sprite{"wifi_off", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_node_connected_sprite{"node_connected", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_asset_downloading_sprite{"asset_downloading", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_mic_enabled_sprite{"mic_enabled", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_mic_disabled_sprite{"mic_disabled", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_mic_active_sprite{"mic_active", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_mute_sprite{"mute", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_dnd_sprite{"dnd", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_timer_sprite{"timer", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_alarm_sprite{"alarm", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_playback_sprite{"playback", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_update_available_sprite{"update_available", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_warning_sprite{"warning", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_privacy_sprite{"privacy", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_cloud_offline_sprite{"cloud_offline", kStatusSpriteSize, kStatusSpriteSize};
StatusSprite g_sidebar_sprite{"sidebar", kSidebarWidth, kSidebarHeight};
StatusSprite g_sidebar_right_sprite{"sidebar_right", kSidebarWidth, kSidebarHeight};
StatusSprite g_idle_clock_frame_sprite{"idle_clock_frame", kIdleClockFrameWidth, kIdleClockFrameHeight};
StatusSprite g_activity_listening_sprite{"activity_listening", kActivitySpriteSize, kActivitySpriteSize};
StatusSprite g_activity_thinking_sprite{"activity_thinking", kActivitySpriteSize, kActivitySpriteSize};
StatusSprite g_activity_replay_sprite{"activity_replay", kActivitySpriteSize, kActivitySpriteSize};
StatusSprite g_activity_timer_sprite{"activity_timer", kActivitySpriteSize, kActivitySpriteSize};
StatusSprite g_button_timer_sprite{"button_timer", kSidebarButtonSize, kSidebarButtonSize};
StatusSprite g_button_weather_sprite{"button_weather", kSidebarButtonSize, kSidebarButtonSize};
StatusSprite g_button_config_sprite{"button_config", kSidebarButtonSize, kSidebarButtonSize};
SlideAnimationState g_slide_animation_states[static_cast<size_t>(StatusIconId::kCount)][kMaxStatusAnimations] = {};
SlideAnimationState g_sidebar_left_animation_state;
SlideAnimationState g_sidebar_right_animation_state;
SlideAnimationState g_idle_clock_animation_states[5][kMaxStatusAnimations] = {};
SlideAnimationState g_activity_animation_states[kActivitySpriteCount][kMaxStatusAnimations] = {};
StatusLayout g_status_layout;
bool g_status_layout_loaded = false;
ClockFont g_clock_font;
ClockFont g_version_font;
ClockFont g_idle_hours_font;
ClockFont g_idle_separator_font;
ClockFont g_idle_minutes_font;
ClockFont g_idle_date_font;
ClockFont g_timer_countdown_font;
ClockFont g_timer_label_font;
ClockFont g_timer_upcoming_font;

bool status_animations_active(const hexe::AppState &state);
int json_layout_coordinate(cJSON *object, const char *key, int fallback, int maximum);
void draw_status_animation(
    const StatusAnimation &animation,
    const StatusSprite &sprite,
    int sprite_x,
    int sprite_y,
    int64_t now_ms);

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

uint32_t phase_color(hexe::AppPhase phase) {
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
  signature = (signature * 131) + (hexe::system::asset_sync_active() ? 1 : 0);
  signature = (signature * 131) + static_cast<int>(state.display_timer_count);
  if (state.timer_active) {
    signature = (signature * 131) + static_cast<int>((esp_timer_get_time() / 1000000) % 100000);
  }
  std::tm local = {};
  if (hexe::system::clock_synced() && hexe::system::current_local_time(&local)) {
    signature = (signature * 131) + (local.tm_hour * 60) + local.tm_min + 1;
  }
  if (status_animations_active(state)) {
    signature = (signature * 131) + static_cast<int>((esp_timer_get_time() / 50000) % 100000);
  }
  if (state.phase == hexe::AppPhase::kBooting || state.phase == hexe::AppPhase::kListening ||
      state.phase == hexe::AppPhase::kThinking || state.phase == hexe::AppPhase::kReplying) {
    signature = (signature * 131) + (frame % 64);
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

void blend_pixel(int x, int y, uint32_t color, uint8_t alpha) {
  if (alpha == 0 || g_flush_buffer == nullptr || x < 0 || y < g_strip_y || x >= kWidth ||
      y >= g_strip_y + g_strip_rows) {
    return;
  }
  uint8_t *pixel = g_flush_buffer + (((y - g_strip_y) * kWidth + x) * kBytesPerPixel);
  const int inverse = 255 - alpha;
  pixel[0] = static_cast<uint8_t>((((color)&0xFF) * alpha + pixel[0] * inverse) / 255);
  pixel[1] = static_cast<uint8_t>((((color >> 8) & 0xFF) * alpha + pixel[1] * inverse) / 255);
  pixel[2] = static_cast<uint8_t>((((color >> 16) & 0xFF) * alpha + pixel[2] * inverse) / 255);
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

void draw_hline(int x, int y, int width, uint32_t color) {
  fill_rect(x, y, width, 1, color);
}

void draw_vline(int x, int y, int height, uint32_t color) {
  fill_rect(x, y, 1, height, color);
}

void draw_rect_outline(int x, int y, int width, int height, uint32_t color) {
  draw_hline(x, y, width, color);
  draw_hline(x, y + height - 1, width, color);
  draw_vline(x, y, height, color);
  draw_vline(x + width - 1, y, height, color);
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

bool read_exact_file(const char *path, uint8_t *target, size_t expected_bytes) {
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    return false;
  }
  const size_t read_bytes = std::fread(target, 1, expected_bytes, file);
  std::fclose(file);
  return read_bytes == expected_bytes;
}

bool load_status_sprite(StatusSprite *sprite) {
  if (sprite == nullptr || sprite->name == nullptr) {
    return false;
  }
  if (sprite->colors != nullptr && sprite->alpha != nullptr) {
    return true;
  }
  if (sprite->load_attempted || !hexe::board::sd_card_mounted()) {
    return false;
  }
  sprite->load_attempted = true;

  char color_path[160] = {};
  char alpha_path[160] = {};
  const char *sprites_path = hexe::board::sd_card_sprites_path();
  const int color_written =
      std::snprintf(color_path, sizeof(color_path), "%s/%s.rgb888", sprites_path, sprite->name);
  const int alpha_written = std::snprintf(alpha_path, sizeof(alpha_path), "%s/%s.alpha8", sprites_path, sprite->name);
  if (color_written <= 0 || color_written >= static_cast<int>(sizeof(color_path)) || alpha_written <= 0 ||
      alpha_written >= static_cast<int>(sizeof(alpha_path))) {
    return false;
  }

  const size_t pixel_count = static_cast<size_t>(sprite->width) * sprite->height;
  const size_t color_bytes = pixel_count * kBytesPerPixel;
  sprite->colors =
      static_cast<uint8_t *>(heap_caps_malloc(color_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  sprite->alpha = static_cast<uint8_t *>(heap_caps_malloc(pixel_count, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (sprite->colors == nullptr || sprite->alpha == nullptr ||
      !read_exact_file(color_path, sprite->colors, color_bytes) ||
      !read_exact_file(alpha_path, sprite->alpha, pixel_count)) {
    heap_caps_free(sprite->colors);
    heap_caps_free(sprite->alpha);
    sprite->colors = nullptr;
    sprite->alpha = nullptr;
    return false;
  }
  for (size_t offset = 0; offset < color_bytes; offset += kBytesPerPixel) {
    std::swap(sprite->colors[offset], sprite->colors[offset + 2]);
  }
  return true;
}

void draw_status_sprite(StatusSprite *sprite, int x, int y, uint8_t opacity = 255) {
  if (!load_status_sprite(sprite)) {
    return;
  }
  for (int source_y = 0; source_y < sprite->height; ++source_y) {
    const int target_y = y + source_y;
    if (target_y < g_strip_y || target_y >= g_strip_y + g_strip_rows || target_y < 0 || target_y >= kHeight) {
      continue;
    }
    for (int source_x = 0; source_x < sprite->width; ++source_x) {
      const int target_x = x + source_x;
      if (target_x < 0 || target_x >= kWidth) {
        continue;
      }
      const size_t source_pixel = static_cast<size_t>(source_y) * sprite->width + source_x;
      const uint8_t alpha = static_cast<uint8_t>((static_cast<unsigned>(sprite->alpha[source_pixel]) * opacity) / 255);
      if (alpha == 0) {
        continue;
      }
      uint8_t *target = g_flush_buffer +
          ((static_cast<size_t>(target_y - g_strip_y) * kWidth + target_x) * kBytesPerPixel);
      const uint8_t *source = sprite->colors + (source_pixel * kBytesPerPixel);
      for (int channel = 0; channel < kBytesPerPixel; ++channel) {
        target[channel] = static_cast<uint8_t>(
            ((static_cast<unsigned>(source[channel]) * alpha) +
             (static_cast<unsigned>(target[channel]) * (255 - alpha)) + 127) /
            255);
      }
    }
  }
}

StatusIconId status_icon_id(const char *name) {
  if (name != nullptr && std::strcmp(name, "wifi") == 0) {
    return StatusIconId::kWifi;
  }
  if (name != nullptr && std::strcmp(name, "node_connected") == 0) {
    return StatusIconId::kNodeConnected;
  }
  if (name != nullptr && std::strcmp(name, "asset_downloading") == 0) {
    return StatusIconId::kAssetDownloading;
  }
  static constexpr struct { const char *name; StatusIconId id; } kIcons[] = {
      {"mic_enabled", StatusIconId::kMicEnabled}, {"mic_disabled", StatusIconId::kMicDisabled},
      {"mic_active", StatusIconId::kMicActive}, {"mute", StatusIconId::kMute},
      {"dnd", StatusIconId::kDnd}, {"timer", StatusIconId::kTimer},
      {"alarm", StatusIconId::kAlarm}, {"playback", StatusIconId::kPlayback},
      {"update_available", StatusIconId::kUpdateAvailable}, {"warning", StatusIconId::kWarning},
      {"privacy", StatusIconId::kPrivacy}, {"cloud_offline", StatusIconId::kCloudOffline},
  };
  for (const auto &icon : kIcons) {
    if (name != nullptr && std::strcmp(name, icon.name) == 0) return icon.id;
  }
  return StatusIconId::kCount;
}

StatusAnimationType status_animation_type(const char *name) {
  if (name != nullptr && std::strcmp(name, "blink_dot") == 0) {
    return StatusAnimationType::kBlinkDot;
  }
  if (name != nullptr && std::strcmp(name, "running_dots") == 0) {
    return StatusAnimationType::kRunningDots;
  }
  if (name != nullptr && std::strcmp(name, "pulse") == 0) {
    return StatusAnimationType::kPulse;
  }
  if (name != nullptr && std::strcmp(name, "pulse_ring") == 0) {
    return StatusAnimationType::kPulseRing;
  }
  if (name != nullptr && std::strcmp(name, "slide_in") == 0) {
    return StatusAnimationType::kSlideIn;
  }
  return StatusAnimationType::kInvalid;
}

StatusFlag status_flag(const char *name) {
  if (name == nullptr) {
    return StatusFlag::kInvalid;
  }
  struct FlagName {
    const char *name;
    StatusFlag flag;
  };
  static constexpr FlagName kFlags[] = {
      {"heartbeat", StatusFlag::kHeartbeat},
      {"loading", StatusFlag::kLoading},
      {"wifi_connected", StatusFlag::kWifiConnected},
      {"wifi_connecting", StatusFlag::kWifiConnecting},
      {"backend_connected", StatusFlag::kBackendConnected},
      {"backend_connecting", StatusFlag::kBackendConnecting},
      {"voice_ws_connected", StatusFlag::kVoiceWsConnected},
      {"asset_sync_active", StatusFlag::kAssetSyncActive},
      {"media_transfer_active", StatusFlag::kMediaTransferActive},
      {"ota_active", StatusFlag::kOtaActive},
      {"listening", StatusFlag::kListening},
      {"thinking", StatusFlag::kThinking},
      {"replying", StatusFlag::kReplying},
      {"muted", StatusFlag::kMuted},
      {"timer_active", StatusFlag::kTimerActive},
      {"timer_finished", StatusFlag::kTimerFinished},
      {"error", StatusFlag::kError},
      {"ui_ready", StatusFlag::kUiReady},
      {"idle_ready", StatusFlag::kIdleReady},
      {"microphone_enabled", StatusFlag::kMicrophoneEnabled},
      {"microphone_disabled", StatusFlag::kMicrophoneDisabled},
      {"microphone_active", StatusFlag::kMicrophoneActive},
      {"dnd", StatusFlag::kDnd},
      {"alarm_active", StatusFlag::kAlarmActive},
      {"playback_active", StatusFlag::kPlaybackActive},
      {"update_available", StatusFlag::kUpdateAvailable},
      {"warning_active", StatusFlag::kWarningActive},
      {"privacy_mode", StatusFlag::kPrivacyMode},
      {"cloud_offline", StatusFlag::kCloudOffline},
  };
  for (const auto &entry : kFlags) {
    if (std::strcmp(name, entry.name) == 0) {
      return entry.flag;
    }
  }
  return StatusFlag::kInvalid;
}

int json_integer(cJSON *object, const char *key, int fallback, int minimum, int maximum) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  return cJSON_IsNumber(value) ? std::clamp(value->valueint, minimum, maximum) : fallback;
}

int json_per_mille(cJSON *object, const char *key, int fallback) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  if (!cJSON_IsNumber(value)) {
    return fallback;
  }
  return std::clamp(static_cast<int>(value->valuedouble * 1000.0 + 0.5), 0, 1000);
}

int json_signed_per_mille(cJSON *object, const char *key, int fallback) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  if (!cJSON_IsNumber(value)) {
    return fallback;
  }
  const double scaled = value->valuedouble * 1000.0;
  return std::clamp(static_cast<int>(scaled + (scaled < 0 ? -0.5 : 0.5)), -1000, 1000);
}

uint32_t json_color(cJSON *object, const char *key, uint32_t fallback) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  if (!cJSON_IsString(value) || value->valuestring == nullptr || std::strlen(value->valuestring) != 7 ||
      value->valuestring[0] != '#') {
    return fallback;
  }
  char *end = nullptr;
  const unsigned long color = std::strtoul(value->valuestring + 1, &end, 16);
  return end != nullptr && *end == '\0' ? static_cast<uint32_t>(color) : fallback;
}

StatusAnimation parse_status_animation(cJSON *item) {
  StatusAnimation animation;
  cJSON *type = cJSON_IsObject(item) ? cJSON_GetObjectItem(item, "type") : nullptr;
  animation.type = cJSON_IsString(type) ? status_animation_type(type->valuestring) : StatusAnimationType::kInvalid;
  cJSON *when = cJSON_IsObject(item) ? cJSON_GetObjectItem(item, "when") : nullptr;
  cJSON *flag = cJSON_IsObject(when) ? cJSON_GetObjectItem(when, "flag") : nullptr;
  animation.flag = cJSON_IsString(flag) ? status_flag(flag->valuestring) : StatusFlag::kInvalid;
  cJSON *equals = cJSON_IsObject(when) ? cJSON_GetObjectItem(when, "equals") : nullptr;
  animation.expected = cJSON_IsBool(equals) ? cJSON_IsTrue(equals) : true;
  animation.color = json_color(item, "color", animation.color);
  cJSON *position = cJSON_GetObjectItem(item, "position");
  animation.x_per_mille = json_per_mille(position, "x", animation.x_per_mille);
  animation.y_per_mille = json_per_mille(position, "y", animation.y_per_mille);
  animation.radius_per_mille = json_per_mille(item, "radius", animation.radius_per_mille);
  animation.spacing_per_mille = json_per_mille(item, "spacing", animation.spacing_per_mille);
  cJSON *offset = cJSON_GetObjectItem(item, "offset");
  animation.offset_x_per_mille = json_signed_per_mille(offset, "x", animation.offset_x_per_mille);
  animation.offset_y_per_mille = json_signed_per_mille(offset, "y", animation.offset_y_per_mille);
  animation.period_ms = json_integer(item, "period_ms", animation.period_ms, 100, 60000);
  animation.count = json_integer(item, "count", animation.count, 1, 8);
  animation.min_opacity = json_integer(item, "min_opacity", animation.min_opacity, 0, 255);
  animation.max_opacity = json_integer(item, "max_opacity", animation.max_opacity, 0, 255);
  if (animation.min_opacity > animation.max_opacity) {
    std::swap(animation.min_opacity, animation.max_opacity);
  }
  return animation;
}

void parse_animation_list(cJSON *owner, StatusAnimation *animations, size_t *animation_count) {
  *animation_count = 0;
  cJSON *items = cJSON_IsObject(owner) ? cJSON_GetObjectItem(owner, "animations") : nullptr;
  cJSON *item = nullptr;
  cJSON_ArrayForEach(item, items) {
    if (*animation_count >= kMaxStatusAnimations) {
      break;
    }
    StatusAnimation animation = parse_status_animation(item);
    if (animation.type != StatusAnimationType::kInvalid && animation.flag != StatusFlag::kInvalid) {
      animations[(*animation_count)++] = animation;
    }
  }
}

void parse_animated_text(cJSON *item, AnimatedTextLayout *layout, const char *default_font) {
  layout->x = json_layout_coordinate(item, "x", layout->x, kWidth - 1);
  layout->y = json_layout_coordinate(item, "y", layout->y, kHeight - 1);
  layout->font_size = json_integer(item, "font_size", layout->font_size, 8, 180);
  layout->color = json_color(item, "color", layout->color);
  cJSON *font = cJSON_IsObject(item) ? cJSON_GetObjectItem(item, "font") : nullptr;
  const char *font_name = cJSON_IsString(font) ? font->valuestring : default_font;
  std::snprintf(layout->font, sizeof(layout->font), "%s", font_name);
  parse_animation_list(item, layout->animations, &layout->animation_count);
}

int activity_sprite_index(const char *name) {
  static constexpr const char *kNames[kActivitySpriteCount] = {
      "listening", "thinking", "replay", "timer"};
  for (size_t index = 0; index < kActivitySpriteCount; ++index) {
    if (name != nullptr && std::strcmp(name, kNames[index]) == 0) {
      return static_cast<int>(index);
    }
  }
  return -1;
}

int json_layout_coordinate(cJSON *object, const char *key, int fallback, int maximum) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  return cJSON_IsNumber(value) ? std::clamp(value->valueint, 0, maximum) : fallback;
}

int json_signed_offset(cJSON *object, const char *key, int fallback, int limit) {
  cJSON *value = cJSON_IsObject(object) ? cJSON_GetObjectItem(object, key) : nullptr;
  return cJSON_IsNumber(value) ? std::clamp(value->valueint, -limit, limit) : fallback;
}

uint16_t read_u16_le(const uint8_t *data) {
  return static_cast<uint16_t>(data[0]) | (static_cast<uint16_t>(data[1]) << 8);
}

int16_t read_i16_le(const uint8_t *data) {
  return static_cast<int16_t>(read_u16_le(data));
}

uint32_t read_u32_le(const uint8_t *data) {
  return static_cast<uint32_t>(data[0]) | (static_cast<uint32_t>(data[1]) << 8) |
      (static_cast<uint32_t>(data[2]) << 16) | (static_cast<uint32_t>(data[3]) << 24);
}

bool load_bitmap_font(ClockFont *font, const char *filename, const char *label) {
  if (font == nullptr || filename == nullptr) {
    return false;
  }
  if (font->data != nullptr) {
    return true;
  }
  if (font->load_attempted || !hexe::board::sd_card_mounted()) {
    return false;
  }
  font->load_attempted = true;
  char path[192] = {};
  const int written = std::snprintf(
      path,
      sizeof(path),
      "%s/%s",
      hexe::board::sd_card_fonts_path(),
      filename);
  if (written <= 0 || written >= static_cast<int>(sizeof(path))) {
    return false;
  }
  struct stat info = {};
  if (stat(path, &info) != 0 || info.st_size < 10 || info.st_size > static_cast<off_t>(kMaxClockFontBytes)) {
    ESP_LOGW(kTag, "%s font unavailable; using built-in fallback", label);
    return false;
  }
  auto *data = static_cast<uint8_t *>(heap_caps_malloc(info.st_size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (data == nullptr || !read_exact_file(path, data, info.st_size)) {
    heap_caps_free(data);
    return false;
  }
  const size_t glyph_count = read_u16_le(data + 8);
  const int pixel_size = read_u16_le(data + 4);
  const int ascent = read_i16_le(data + 6);
  constexpr size_t kHeaderBytes = 10;
  constexpr size_t kRecordBytes = 15;
  if (std::memcmp(data, "HXF1", 4) != 0 || pixel_size <= 0 || ascent <= 0 ||
      glyph_count == 0 || glyph_count > kFontGlyphCount ||
      kHeaderBytes + (glyph_count * kRecordBytes) > static_cast<size_t>(info.st_size)) {
    heap_caps_free(data);
    ESP_LOGW(kTag, "%s font is invalid; using built-in fallback", label);
    return false;
  }
  for (size_t index = 0; index < glyph_count; ++index) {
    const uint8_t *record = data + kHeaderBytes + (index * kRecordBytes);
    auto &glyph = font->glyphs[index];
    glyph.code = static_cast<char>(record[0]);
    glyph.left = read_i16_le(record + 1);
    glyph.top = read_i16_le(record + 3);
    glyph.advance = read_i16_le(record + 5);
    glyph.width = read_u16_le(record + 7);
    glyph.height = read_u16_le(record + 9);
    glyph.bitmap_offset = read_u32_le(record + 11);
    const size_t bitmap_bytes = static_cast<size_t>(glyph.width) * glyph.height;
    if (glyph.width == 0 || glyph.height == 0 || glyph.bitmap_offset > static_cast<size_t>(info.st_size) ||
        bitmap_bytes > static_cast<size_t>(info.st_size) - glyph.bitmap_offset) {
      heap_caps_free(data);
      return false;
    }
  }
  font->data = data;
  font->size = info.st_size;
  font->pixel_size = pixel_size;
  font->ascent = ascent;
  font->glyph_count = glyph_count;
  ESP_LOGI(kTag, "Loaded %s font %s", label, path);
  return true;
}

const ClockGlyph *font_glyph(const ClockFont &font, char code) {
  for (size_t index = 0; index < font.glyph_count; ++index) {
    if (font.glyphs[index].code == code) {
      return &font.glyphs[index];
    }
  }
  return nullptr;
}

int scale_font_metric(const ClockFont &font, int font_size, int value) {
  const int numerator = value * font_size;
  const int adjustment = numerator < 0 ? -(font.pixel_size / 2) : (font.pixel_size / 2);
  return (numerator + adjustment) / font.pixel_size;
}

int bitmap_text_width(const ClockFont &font, const char *text, int font_size) {
  int width = 0;
  for (const char *cursor = text; cursor != nullptr && *cursor != '\0'; ++cursor) {
    const ClockGlyph *glyph = font_glyph(font, *cursor);
    if (glyph == nullptr) {
      return 0;
    }
    width += scale_font_metric(font, font_size, glyph->advance);
  }
  return width;
}

bool draw_bitmap_text(
    const ClockFont &font, const char *text, int x, int y, int font_size, uint32_t color) {
  if (font.data == nullptr || text == nullptr) {
    return false;
  }
  int cursor_x = x;
  const int baseline_y = y + scale_font_metric(font, font_size, font.ascent);
  for (const char *cursor = text; *cursor != '\0'; ++cursor) {
    const ClockGlyph *glyph = font_glyph(font, *cursor);
    if (glyph == nullptr) {
      return false;
    }
    const int glyph_x = cursor_x + scale_font_metric(font, font_size, glyph->left);
    const int glyph_y = baseline_y - scale_font_metric(font, font_size, glyph->top);
    const int scaled_width = std::max(1, scale_font_metric(font, font_size, glyph->width));
    const int scaled_height = std::max(1, scale_font_metric(font, font_size, glyph->height));
    const uint8_t *bitmap = font.data + glyph->bitmap_offset;
    for (int row = 0; row < scaled_height; ++row) {
      const int source_row = std::min(glyph->height - 1, (row * glyph->height) / scaled_height);
      for (int column = 0; column < scaled_width; ++column) {
        const int source_column = std::min(glyph->width - 1, (column * glyph->width) / scaled_width);
        blend_pixel(
            glyph_x + column,
            glyph_y + row,
            color,
            bitmap[(source_row * glyph->width) + source_column]);
      }
    }
    cursor_x += scale_font_metric(font, font_size, glyph->advance);
  }
  return true;
}

bool valid_layout_filename(const char *filename) {
  if (filename == nullptr || filename[0] == '\0' || std::strstr(filename, "..") != nullptr ||
      std::strchr(filename, '/') != nullptr || std::strchr(filename, '\\') != nullptr) {
    return false;
  }
  const size_t length = std::strlen(filename);
  return length > 5 && std::strcmp(filename + length - 5, ".json") == 0;
}

cJSON *read_status_layout_file(const char *filename) {
  if (!valid_layout_filename(filename)) {
    ESP_LOGW(kTag, "Rejected invalid layout filename");
    return nullptr;
  }
  char path[192] = {};
  const int written = std::snprintf(
      path, sizeof(path), "%s/%s", hexe::board::sd_card_sprites_path(), filename);
  if (written <= 0 || written >= static_cast<int>(sizeof(path))) {
    return nullptr;
  }
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Layout file not found: %s", filename);
    return nullptr;
  }
  auto *payload = static_cast<char *>(
      heap_caps_calloc(kStatusLayoutMaxBytes + 1, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (payload == nullptr) {
    std::fclose(file);
    ESP_LOGW(kTag, "Could not allocate layout buffer for %s", filename);
    return nullptr;
  }
  const size_t payload_size = std::fread(payload, 1, kStatusLayoutMaxBytes, file);
  const bool too_large = std::fgetc(file) != EOF;
  std::fclose(file);
  if (payload_size == 0 || too_large) {
    heap_caps_free(payload);
    ESP_LOGW(kTag, "Layout file is empty or too large: %s", filename);
    return nullptr;
  }

  cJSON *root = cJSON_ParseWithLength(payload, payload_size);
  heap_caps_free(payload);
  if (!cJSON_IsObject(root)) {
    cJSON_Delete(root);
    ESP_LOGW(kTag, "Layout file is invalid: %s", filename);
    return nullptr;
  }
  return root;
}

void merge_status_layout_file(cJSON *root, const char *filename) {
  cJSON *section_root = read_status_layout_file(filename);
  if (section_root == nullptr) {
    return;
  }
  cJSON *section = nullptr;
  cJSON_ArrayForEach(section, section_root) {
    if (section->string == nullptr || std::strcmp(section->string, "schema_version") == 0) {
      continue;
    }
    cJSON *copy = cJSON_Duplicate(section, true);
    if (copy == nullptr) {
      continue;
    }
    if (cJSON_HasObjectItem(root, section->string)) {
      cJSON_ReplaceItemInObjectCaseSensitive(root, section->string, copy);
    } else {
      cJSON_AddItemToObject(root, section->string, copy);
    }
  }
  cJSON_Delete(section_root);
  ESP_LOGI(kTag, "Loaded layout section %s", filename);
}

void load_status_layout() {
  if (g_status_layout_loaded) {
    return;
  }
  g_status_layout_loaded = true;
  g_status_layout = StatusLayout{};

  cJSON *root = read_status_layout_file(kStatusLayoutFilename);
  if (root == nullptr) {
    ESP_LOGW(kTag, "Status layout not found; using defaults");
    return;
  }
  cJSON *files = cJSON_GetObjectItem(root, "files");
  cJSON *filename = nullptr;
  cJSON_ArrayForEach(filename, files) {
    if (cJSON_IsString(filename)) {
      merge_status_layout_file(root, filename->valuestring);
    }
  }
  cJSON *icons_config = cJSON_GetObjectItem(root, "icons");
  cJSON *floating = cJSON_IsObject(icons_config) ? cJSON_GetObjectItem(icons_config, "floating")
                                                  : cJSON_GetObjectItem(root, "floating");
  cJSON *sidebars = cJSON_GetObjectItem(root, "sidebars");
  cJSON *activity_sprites = cJSON_GetObjectItem(root, "activity_sprites");
  cJSON *timer_screen = cJSON_GetObjectItem(root, "timer_screen");
  cJSON *sidebar_buttons = cJSON_GetObjectItem(root, "sidebar_buttons");
  cJSON *idle_clock = cJSON_GetObjectItem(root, "idle_clock");
  cJSON *clock = cJSON_GetObjectItem(root, "clock");
  cJSON *version = cJSON_GetObjectItem(root, "version");
  cJSON *idle_enabled = cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "enabled") : nullptr;
  if (cJSON_IsBool(idle_enabled)) {
    g_status_layout.idle_clock.enabled = cJSON_IsTrue(idle_enabled);
  }
  cJSON *date_format = cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "date_format") : nullptr;
  if (cJSON_IsString(date_format) && date_format->valuestring != nullptr) {
    std::snprintf(
        g_status_layout.idle_clock.date_format,
        sizeof(g_status_layout.idle_clock.date_format),
        "%s",
        date_format->valuestring);
  }
  cJSON *idle_frame = cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "sprite") : nullptr;
  g_status_layout.idle_clock.frame.x =
      json_layout_coordinate(idle_frame, "x", g_status_layout.idle_clock.frame.x, kWidth - kIdleClockFrameWidth);
  g_status_layout.idle_clock.frame.y =
      json_layout_coordinate(idle_frame, "y", g_status_layout.idle_clock.frame.y, kHeight - kIdleClockFrameHeight);
  parse_animation_list(
      idle_frame,
      g_status_layout.idle_clock.frame.animations,
      &g_status_layout.idle_clock.frame.animation_count);
  parse_animated_text(
      cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "hours") : nullptr,
      &g_status_layout.idle_clock.hours,
      kDefaultIdleClockFont);
  parse_animated_text(
      cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "separator") : nullptr,
      &g_status_layout.idle_clock.separator,
      kDefaultIdleClockFont);
  parse_animated_text(
      cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "minutes") : nullptr,
      &g_status_layout.idle_clock.minutes,
      kDefaultIdleClockFont);
  parse_animated_text(
      cJSON_IsObject(idle_clock) ? cJSON_GetObjectItem(idle_clock, "date") : nullptr,
      &g_status_layout.idle_clock.date,
      kDefaultIdleDateFont);
  cJSON *timer_enabled = cJSON_IsObject(timer_screen) ? cJSON_GetObjectItem(timer_screen, "enabled") : nullptr;
  if (cJSON_IsBool(timer_enabled)) {
    g_status_layout.timer_screen.enabled = cJSON_IsTrue(timer_enabled);
  }
  parse_animated_text(
      cJSON_IsObject(timer_screen) ? cJSON_GetObjectItem(timer_screen, "primary_countdown") : nullptr,
      &g_status_layout.timer_screen.primary_countdown,
      kDefaultIdleClockFont);
  parse_animated_text(
      cJSON_IsObject(timer_screen) ? cJSON_GetObjectItem(timer_screen, "primary_label") : nullptr,
      &g_status_layout.timer_screen.primary_label,
      kDefaultIdleDateFont);
  cJSON *upcoming = cJSON_IsObject(timer_screen) ? cJSON_GetObjectItem(timer_screen, "upcoming") : nullptr;
  g_status_layout.timer_screen.upcoming.x =
      json_layout_coordinate(upcoming, "x", g_status_layout.timer_screen.upcoming.x, kWidth - 1);
  g_status_layout.timer_screen.upcoming.y =
      json_layout_coordinate(upcoming, "y", g_status_layout.timer_screen.upcoming.y, kHeight - 1);
  g_status_layout.timer_screen.upcoming.gap =
      json_integer(upcoming, "gap", g_status_layout.timer_screen.upcoming.gap, 20, 100);
  g_status_layout.timer_screen.upcoming.count =
      json_integer(upcoming, "count", g_status_layout.timer_screen.upcoming.count, 1, 3);
  g_status_layout.timer_screen.upcoming.font_size =
      json_integer(upcoming, "font_size", g_status_layout.timer_screen.upcoming.font_size, 12, 64);
  g_status_layout.timer_screen.upcoming.color =
      json_color(upcoming, "color", g_status_layout.timer_screen.upcoming.color);
  cJSON *upcoming_font = cJSON_IsObject(upcoming) ? cJSON_GetObjectItem(upcoming, "font") : nullptr;
  std::snprintf(
      g_status_layout.timer_screen.upcoming.font,
      sizeof(g_status_layout.timer_screen.upcoming.font),
      "%s",
      cJSON_IsString(upcoming_font) ? upcoming_font->valuestring : kDefaultIdleDateFont);
  cJSON *left_sidebar = cJSON_IsObject(sidebars) ? cJSON_GetObjectItem(sidebars, "left") : nullptr;
  cJSON *right_sidebar = cJSON_IsObject(sidebars) ? cJSON_GetObjectItem(sidebars, "right") : nullptr;
  if (cJSON_IsObject(left_sidebar)) {
    StatusAnimation animation = parse_status_animation(left_sidebar);
    if (animation.type == StatusAnimationType::kSlideIn && animation.flag != StatusFlag::kInvalid) {
      g_status_layout.sidebars.left = animation;
    }
  }
  if (cJSON_IsObject(right_sidebar)) {
    StatusAnimation animation = parse_status_animation(right_sidebar);
    if (animation.type == StatusAnimationType::kSlideIn && animation.flag != StatusFlag::kInvalid) {
      g_status_layout.sidebars.right = animation;
    }
  }
  cJSON *activity_items = cJSON_IsObject(activity_sprites) ? cJSON_GetObjectItem(activity_sprites, "items") : nullptr;
  cJSON *activity_item = nullptr;
  cJSON_ArrayForEach(activity_item, activity_items) {
    cJSON *id = cJSON_GetObjectItem(activity_item, "id");
    const int index = cJSON_IsString(id) ? activity_sprite_index(id->valuestring) : -1;
    if (index < 0) {
      continue;
    }
    auto &layout = g_status_layout.activity_sprites.items[index];
    layout.x = json_layout_coordinate(activity_item, "x", layout.x, kWidth - kActivitySpriteSize);
    layout.y = json_layout_coordinate(activity_item, "y", layout.y, kHeight - kActivitySpriteSize);
    parse_animation_list(activity_item, layout.animations, &layout.animation_count);
  }
  cJSON *buttons_enabled = cJSON_IsObject(sidebar_buttons) ? cJSON_GetObjectItem(sidebar_buttons, "enabled") : nullptr;
  if (cJSON_IsBool(buttons_enabled)) {
    g_status_layout.sidebar_buttons.enabled = cJSON_IsTrue(buttons_enabled);
  }
  g_status_layout.sidebar_buttons.x = json_layout_coordinate(
      sidebar_buttons, "x", g_status_layout.sidebar_buttons.x, kSidebarWidth - kSidebarButtonSize);
  g_status_layout.sidebar_buttons.y = json_layout_coordinate(
      sidebar_buttons, "y", g_status_layout.sidebar_buttons.y, kHeight - kSidebarButtonSize);
  g_status_layout.sidebar_buttons.gap = json_layout_coordinate(
      sidebar_buttons, "gap", g_status_layout.sidebar_buttons.gap, kSidebarHeight);
  g_status_layout.clock.font_size =
      json_integer(clock, "font_size", g_status_layout.clock.font_size, 12, 96);
  g_status_layout.clock.x_offset =
      json_signed_offset(clock, "x_offset", g_status_layout.clock.x_offset, kWidth);
  g_status_layout.clock.y_offset =
      json_signed_offset(clock, "y_offset", g_status_layout.clock.y_offset, kHeight);
  g_status_layout.clock.color = json_color(clock, "color", g_status_layout.clock.color);
  cJSON *clock_font = cJSON_IsObject(clock) ? cJSON_GetObjectItem(clock, "font") : nullptr;
  const char *font_name = cJSON_IsString(clock_font) ? clock_font->valuestring : kDefaultClockFont;
  std::snprintf(g_status_layout.clock.font, sizeof(g_status_layout.clock.font), "%s", font_name);
  g_status_layout.version.font_size =
      json_integer(version, "font_size", g_status_layout.version.font_size, 8, 64);
  g_status_layout.version.x =
      json_layout_coordinate(version, "x", g_status_layout.version.x, kWidth - 1);
  g_status_layout.version.y =
      json_layout_coordinate(version, "y", g_status_layout.version.y, kHeight - 1);
  g_status_layout.version.color = json_color(version, "color", g_status_layout.version.color);
  cJSON *version_font = cJSON_IsObject(version) ? cJSON_GetObjectItem(version, "font") : nullptr;
  const char *version_font_name =
      cJSON_IsString(version_font) ? version_font->valuestring : kDefaultVersionFont;
  std::snprintf(
      g_status_layout.version.font, sizeof(g_status_layout.version.font), "%s", version_font_name);
  g_status_layout.y = json_layout_coordinate(
      cJSON_IsObject(icons_config) ? icons_config : root, "y", g_status_layout.y, kHeight - 1);
  g_status_layout.floating_x = json_layout_coordinate(floating, "x", g_status_layout.floating_x, kWidth - 1);
  g_status_layout.floating_gap = json_layout_coordinate(floating, "gap", g_status_layout.floating_gap, kWidth);

  cJSON *icons = cJSON_IsObject(icons_config) ? cJSON_GetObjectItem(icons_config, "items") : icons_config;
  if (cJSON_IsArray(icons)) {
    g_status_layout.floating_count = 0;
    cJSON *icon = nullptr;
    cJSON_ArrayForEach(icon, icons) {
      cJSON *id_value = cJSON_GetObjectItem(icon, "id");
      cJSON *placement = cJSON_GetObjectItem(icon, "placement");
      if (!cJSON_IsString(id_value) || !cJSON_IsString(placement)) {
        continue;
      }
      const StatusIconId id = status_icon_id(id_value->valuestring);
      if (id == StatusIconId::kCount) {
        continue;
      }
      auto &layout = g_status_layout.icons[static_cast<size_t>(id)];
      layout.animation_count = 0;
      layout.floating = std::strcmp(placement->valuestring, "floating") == 0;
      if (layout.floating) {
        if (g_status_layout.floating_count < static_cast<size_t>(StatusIconId::kCount)) {
          g_status_layout.floating_order[g_status_layout.floating_count++] = id;
        }
      } else {
        layout.x = json_layout_coordinate(icon, "x", layout.x, kWidth - 1);
      }
      cJSON *animations = cJSON_GetObjectItem(icon, "animations");
      cJSON *animation_item = nullptr;
      cJSON_ArrayForEach(animation_item, animations) {
        if (layout.animation_count >= kMaxStatusAnimations) {
          break;
        }
        StatusAnimation animation = parse_status_animation(animation_item);
        if (animation.type != StatusAnimationType::kInvalid && animation.flag != StatusFlag::kInvalid) {
          layout.animations[layout.animation_count++] = animation;
        }
      }
    }
  }
  cJSON_Delete(root);
  ESP_LOGI(kTag, "Loaded status layout");
}

bool status_icon_active(StatusIconId id, const hexe::AppState &state) {
  switch (id) {
    case StatusIconId::kWifi:
      return true;
    case StatusIconId::kNodeConnected:
      return state.backend_connected;
    case StatusIconId::kAssetDownloading:
      return hexe::system::asset_sync_active();
    case StatusIconId::kMicEnabled: return state.microphone_enabled && state.phase != hexe::AppPhase::kListening;
    case StatusIconId::kMicDisabled: return !state.microphone_enabled;
    case StatusIconId::kMicActive: return state.microphone_enabled && state.phase == hexe::AppPhase::kListening;
    case StatusIconId::kMute: return state.muted;
    case StatusIconId::kDnd: return state.dnd_enabled;
    case StatusIconId::kTimer: return state.timer_active;
    case StatusIconId::kAlarm: return state.alarm_active;
    case StatusIconId::kPlayback: return state.tts_playback_active;
    case StatusIconId::kUpdateAvailable: return state.update_available;
    case StatusIconId::kWarning: return state.warning_active || state.phase == hexe::AppPhase::kError;
    case StatusIconId::kPrivacy: return state.privacy_mode;
    case StatusIconId::kCloudOffline: return state.cloud_offline;
    case StatusIconId::kCount:
      return false;
  }
  return false;
}

StatusSprite *status_icon_sprite(StatusIconId id, const hexe::AppState &state) {
  switch (id) {
    case StatusIconId::kWifi:
      return state.wifi_connected ? &g_wifi_on_sprite : &g_wifi_off_sprite;
    case StatusIconId::kNodeConnected:
      return &g_node_connected_sprite;
    case StatusIconId::kAssetDownloading:
      return &g_asset_downloading_sprite;
    case StatusIconId::kMicEnabled: return &g_mic_enabled_sprite;
    case StatusIconId::kMicDisabled: return &g_mic_disabled_sprite;
    case StatusIconId::kMicActive: return &g_mic_active_sprite;
    case StatusIconId::kMute: return &g_mute_sprite;
    case StatusIconId::kDnd: return &g_dnd_sprite;
    case StatusIconId::kTimer: return &g_timer_sprite;
    case StatusIconId::kAlarm: return &g_alarm_sprite;
    case StatusIconId::kPlayback: return &g_playback_sprite;
    case StatusIconId::kUpdateAvailable: return &g_update_available_sprite;
    case StatusIconId::kWarning: return &g_warning_sprite;
    case StatusIconId::kPrivacy: return &g_privacy_sprite;
    case StatusIconId::kCloudOffline: return &g_cloud_offline_sprite;
    case StatusIconId::kCount:
      return nullptr;
  }
  return nullptr;
}

bool status_flag_value(StatusFlag flag, const hexe::AppState &state) {
  switch (flag) {
    case StatusFlag::kHeartbeat:
      return true;
    case StatusFlag::kLoading:
      return state.phase == hexe::AppPhase::kBooting || state.phase == hexe::AppPhase::kWiFiConnecting ||
          state.phase == hexe::AppPhase::kBackendConnecting || state.ota_active || hexe::system::asset_sync_active();
    case StatusFlag::kWifiConnected:
      return state.wifi_connected;
    case StatusFlag::kWifiConnecting:
      return state.phase == hexe::AppPhase::kWiFiConnecting;
    case StatusFlag::kBackendConnected:
      return state.backend_connected;
    case StatusFlag::kBackendConnecting:
      return state.phase == hexe::AppPhase::kBackendConnecting;
    case StatusFlag::kVoiceWsConnected:
      return state.voice_ws_connected;
    case StatusFlag::kAssetSyncActive:
      return hexe::system::asset_sync_active();
    case StatusFlag::kMediaTransferActive:
      return state.media_transfer_active;
    case StatusFlag::kOtaActive:
      return state.ota_active;
    case StatusFlag::kListening:
      return state.phase == hexe::AppPhase::kListening;
    case StatusFlag::kThinking:
      return state.phase == hexe::AppPhase::kThinking;
    case StatusFlag::kReplying:
      return state.phase == hexe::AppPhase::kReplying;
    case StatusFlag::kMuted:
      return state.muted || state.phase == hexe::AppPhase::kMuted;
    case StatusFlag::kTimerActive:
      return state.timer_active;
    case StatusFlag::kTimerFinished:
      return state.timer_state == hexe::TimerLifecycleState::kFinished;
    case StatusFlag::kError:
      return state.phase == hexe::AppPhase::kError;
    case StatusFlag::kUiReady:
      return state.wifi_connected && state.backend_connected && !state.ota_active &&
          state.phase != hexe::AppPhase::kUpdating && state.phase != hexe::AppPhase::kError;
    case StatusFlag::kIdleReady:
      return state.wifi_connected && state.backend_connected && !state.ota_active &&
          state.phase == hexe::AppPhase::kIdle && hexe::system::clock_synced();
    case StatusFlag::kMicrophoneEnabled: return state.microphone_enabled;
    case StatusFlag::kMicrophoneDisabled: return !state.microphone_enabled;
    case StatusFlag::kMicrophoneActive: return state.microphone_enabled && state.phase == hexe::AppPhase::kListening;
    case StatusFlag::kDnd: return state.dnd_enabled;
    case StatusFlag::kAlarmActive: return state.alarm_active;
    case StatusFlag::kPlaybackActive: return state.tts_playback_active;
    case StatusFlag::kUpdateAvailable: return state.update_available;
    case StatusFlag::kWarningActive: return state.warning_active;
    case StatusFlag::kPrivacyMode: return state.privacy_mode;
    case StatusFlag::kCloudOffline: return state.cloud_offline;
    case StatusFlag::kInvalid:
      return false;
  }
  return false;
}

bool status_animation_active(const StatusAnimation &animation, const hexe::AppState &state) {
  return status_flag_value(animation.flag, state) == animation.expected;
}

bool status_animations_active(const hexe::AppState &state) {
  if (!g_status_layout_loaded) {
    return false;
  }
  for (const auto &layout : g_status_layout.icons) {
    for (size_t index = 0; index < layout.animation_count; ++index) {
      if (status_animation_active(layout.animations[index], state)) {
        return true;
      }
    }
  }
  for (const auto &layout : g_status_layout.activity_sprites.items) {
    for (size_t index = 0; index < layout.animation_count; ++index) {
      if (status_animation_active(layout.animations[index], state)) {
        return true;
      }
    }
  }
  return false;
}

uint32_t scale_color(uint32_t color, int intensity_per_mille) {
  const int intensity = std::clamp(intensity_per_mille, 0, 1000);
  const uint32_t red = ((color >> 16) & 0xFF) * intensity / 1000;
  const uint32_t green = ((color >> 8) & 0xFF) * intensity / 1000;
  const uint32_t blue = (color & 0xFF) * intensity / 1000;
  return (red << 16) | (green << 8) | blue;
}

int animation_phase_per_mille(const StatusAnimation &animation, int64_t now_ms) {
  return static_cast<int>((now_ms % animation.period_ms) * 1000 / animation.period_ms);
}

int animation_triangle_per_mille(const StatusAnimation &animation, int64_t now_ms) {
  const int phase = animation_phase_per_mille(animation, now_ms) * 2;
  return phase <= 1000 ? phase : 2000 - phase;
}

int relative_pixels(int per_mille, int size) {
  return (per_mille * size + 500) / 1000;
}

int signed_relative_pixels(int per_mille, int size) {
  const int scaled = per_mille * size;
  return (scaled + (scaled < 0 ? -500 : 500)) / 1000;
}

void slide_animation_offset(
    const StatusAnimation &animation,
    const hexe::AppState &state,
    int sprite_width,
    int sprite_height,
    int64_t now_ms,
    SlideAnimationState *animation_state,
    int *offset_x,
    int *offset_y) {
  if (!status_animation_active(animation, state)) {
    *animation_state = {};
    return;
  }
  if (!animation_state->active) {
    animation_state->active = true;
    animation_state->started_ms = now_ms;
  }
  const int64_t elapsed_ms = std::clamp<int64_t>(now_ms - animation_state->started_ms, 0, animation.period_ms);
  const int progress = static_cast<int>(elapsed_ms * 1000 / animation.period_ms);
  const int remaining = 1000 - progress;
  const int eased_remaining = static_cast<int>(static_cast<int64_t>(remaining) * remaining * remaining / 1000000);
  *offset_x += signed_relative_pixels(animation.offset_x_per_mille, sprite_width) * eased_remaining / 1000;
  *offset_y += signed_relative_pixels(animation.offset_y_per_mille, sprite_height) * eased_remaining / 1000;
}

void status_sprite_slide_offset(
    StatusIconId id,
    const StatusIconLayout &layout,
    const hexe::AppState &state,
    int sprite_width,
    int sprite_height,
    int64_t now_ms,
    int *offset_x,
    int *offset_y) {
  *offset_x = 0;
  *offset_y = 0;
  const size_t icon_index = static_cast<size_t>(id);
  for (size_t index = 0; index < layout.animation_count; ++index) {
    const auto &animation = layout.animations[index];
    if (animation.type != StatusAnimationType::kSlideIn) {
      continue;
    }
    auto &animation_state = g_slide_animation_states[icon_index][index];
    slide_animation_offset(
        animation, state, sprite_width, sprite_height, now_ms, &animation_state, offset_x, offset_y);
  }
}

void draw_sidebars(const hexe::AppState &state, int64_t now_ms) {
  int left_x = 0;
  int left_y = 0;
  slide_animation_offset(
      g_status_layout.sidebars.left,
      state,
      kSidebarWidth,
      kSidebarHeight,
      now_ms,
      &g_sidebar_left_animation_state,
      &left_x,
      &left_y);
  int right_x = 0;
  int right_y = 0;
  slide_animation_offset(
      g_status_layout.sidebars.right,
      state,
      kSidebarWidth,
      kSidebarHeight,
      now_ms,
      &g_sidebar_right_animation_state,
      &right_x,
      &right_y);
  if (!status_animation_active(g_status_layout.sidebars.left, state) ||
      !status_animation_active(g_status_layout.sidebars.right, state)) {
    return;
  }
  draw_status_sprite(&g_sidebar_sprite, left_x, kSidebarTop + left_y);
  draw_status_sprite(&g_sidebar_right_sprite, kWidth - kSidebarWidth + right_x, kSidebarTop + right_y);
  if (g_status_layout.sidebar_buttons.enabled) {
    StatusSprite *buttons[kSidebarButtonCount] = {
        &g_button_timer_sprite, &g_button_weather_sprite, &g_button_config_sprite};
    int button_y = g_status_layout.sidebar_buttons.y + left_y;
    for (StatusSprite *button : buttons) {
      draw_status_sprite(button, g_status_layout.sidebar_buttons.x + left_x, button_y);
      button_y += kSidebarButtonSize + g_status_layout.sidebar_buttons.gap;
    }
  }
}

uint8_t animated_element_transform(
    const StatusAnimation *animations,
    size_t animation_count,
    const hexe::AppState &state,
    int width,
    int height,
    int64_t now_ms,
    size_t element_index,
    int *offset_x,
    int *offset_y) {
  *offset_x = 0;
  *offset_y = 0;
  int opacity = 255;
  for (size_t index = 0; index < animation_count; ++index) {
    const auto &animation = animations[index];
    if (animation.type == StatusAnimationType::kSlideIn) {
      slide_animation_offset(
          animation,
          state,
          width,
          height,
          now_ms,
          &g_idle_clock_animation_states[element_index][index],
          offset_x,
          offset_y);
    } else if (animation.type == StatusAnimationType::kPulse && status_animation_active(animation, state)) {
      const int phase = animation_triangle_per_mille(animation, now_ms);
      const int animated =
          animation.min_opacity + ((animation.max_opacity - animation.min_opacity) * phase / 1000);
      opacity = std::min(opacity, animated);
    }
  }
  return static_cast<uint8_t>(opacity);
}

int active_activity_sprite(const hexe::AppState &state) {
  if (state.phase == hexe::AppPhase::kListening) return 0;
  if (state.phase == hexe::AppPhase::kThinking) return 1;
  if (state.phase == hexe::AppPhase::kReplying || state.tts_playback_active) return 2;
  if (state.timer_active) return 3;
  return -1;
}

void draw_activity_sprite(const hexe::AppState &state, int64_t now_ms) {
  const int active_index = active_activity_sprite(state);
  StatusSprite *sprites[kActivitySpriteCount] = {
      &g_activity_listening_sprite,
      &g_activity_thinking_sprite,
      &g_activity_replay_sprite,
      &g_activity_timer_sprite,
  };
  for (size_t index = 0; index < kActivitySpriteCount; ++index) {
    if (static_cast<int>(index) != active_index) {
      for (auto &animation_state : g_activity_animation_states[index]) animation_state = {};
    }
  }
  if (active_index < 0) {
    return;
  }
  const auto &layout = g_status_layout.activity_sprites.items[active_index];
  StatusSprite *sprite = sprites[active_index];
  int offset_x = 0;
  int offset_y = 0;
  int opacity = 255;
  for (size_t index = 0; index < layout.animation_count; ++index) {
    const auto &animation = layout.animations[index];
    if (animation.type == StatusAnimationType::kSlideIn) {
      slide_animation_offset(
          animation,
          state,
          sprite->width,
          sprite->height,
          now_ms,
          &g_activity_animation_states[active_index][index],
          &offset_x,
          &offset_y);
    } else if (animation.type == StatusAnimationType::kPulse && status_animation_active(animation, state)) {
      const int phase = animation_triangle_per_mille(animation, now_ms);
      const int animated =
          animation.min_opacity + ((animation.max_opacity - animation.min_opacity) * phase / 1000);
      opacity = std::min(opacity, animated);
    }
  }
  const int x = layout.x + offset_x;
  const int y = layout.y + offset_y;
  draw_status_sprite(sprite, x, y, static_cast<uint8_t>(opacity));
  for (size_t index = 0; index < layout.animation_count; ++index) {
    const auto &animation = layout.animations[index];
    if (animation.type != StatusAnimationType::kPulse && animation.type != StatusAnimationType::kSlideIn &&
        status_animation_active(animation, state)) {
      draw_status_animation(animation, *sprite, x, y, now_ms);
    }
  }
}

void draw_idle_clock_text(
    const AnimatedTextLayout &layout,
    ClockFont *font,
    const char *text,
    const char *label,
    const hexe::AppState &state,
    int64_t now_ms,
    size_t element_index) {
  if (!load_bitmap_font(font, layout.font, label)) {
    const int fallback_scale = (layout.font_size * 100) / 7;
    draw_text(layout.x - (text_width(text, fallback_scale) / 2), layout.y, text, fallback_scale, layout.color);
    return;
  }
  const int width = bitmap_text_width(*font, text, layout.font_size);
  if (width <= 0) {
    return;
  }
  int offset_x = 0;
  int offset_y = 0;
  const uint8_t opacity = animated_element_transform(
      layout.animations,
      layout.animation_count,
      state,
      width,
      layout.font_size,
      now_ms,
      element_index,
      &offset_x,
      &offset_y);
  draw_bitmap_text(
      *font,
      text,
      layout.x - (width / 2) + offset_x,
      layout.y + offset_y,
      layout.font_size,
      scale_color(layout.color, opacity * 1000 / 255));
}

void draw_idle_clock(const hexe::AppState &state, int64_t now_ms) {
  if (!g_status_layout.idle_clock.enabled || !status_flag_value(StatusFlag::kIdleReady, state)) {
    for (auto &element : g_idle_clock_animation_states) {
      for (auto &animation_state : element) {
        animation_state = {};
      }
    }
    return;
  }

  const auto &clock = g_status_layout.idle_clock;
  int frame_offset_x = 0;
  int frame_offset_y = 0;
  const uint8_t frame_opacity = animated_element_transform(
      clock.frame.animations,
      clock.frame.animation_count,
      state,
      kIdleClockFrameWidth,
      kIdleClockFrameHeight,
      now_ms,
      0,
      &frame_offset_x,
      &frame_offset_y);
  draw_status_sprite(
      &g_idle_clock_frame_sprite,
      clock.frame.x + frame_offset_x,
      clock.frame.y + frame_offset_y,
      frame_opacity);

  std::tm local = {};
  if (!hexe::system::current_local_time(&local)) {
    return;
  }
  int hour = local.tm_hour % 12;
  if (hour == 0) {
    hour = 12;
  }
  char hours[4] = {};
  char minutes[4] = {};
  char date[40] = {};
  std::snprintf(hours, sizeof(hours), "%02d", hour);
  std::snprintf(minutes, sizeof(minutes), "%02d", local.tm_min);
  if (std::strftime(date, sizeof(date), clock.date_format, &local) == 0) {
    date[0] = '\0';
  }
  draw_idle_clock_text(clock.hours, &g_idle_hours_font, hours, "idle hours", state, now_ms, 1);
  draw_idle_clock_text(clock.separator, &g_idle_separator_font, ":", "idle separator", state, now_ms, 2);
  draw_idle_clock_text(clock.minutes, &g_idle_minutes_font, minutes, "idle minutes", state, now_ms, 3);
  AnimatedTextLayout centered_date = clock.date;
  centered_date.x = kWidth / 2;
  draw_idle_clock_text(centered_date, &g_idle_date_font, date, "idle date", state, now_ms, 4);
}

int64_t display_timer_remaining_ms(const hexe::DisplayTimer &timer) {
  if (timer.state == hexe::TimerLifecycleState::kPaused) {
    return std::max<int64_t>(0, timer.remaining_ms);
  }
  int64_t now_unix_ms = 0;
  if (timer.due_unix_ms > 0 && hexe::system::current_utc_unix_ms(&now_unix_ms)) {
    return std::max<int64_t>(0, timer.due_unix_ms - now_unix_ms);
  }
  return std::max<int64_t>(0, timer.remaining_ms);
}

void format_timer_countdown(int64_t remaining_ms, char *output, size_t output_size) {
  const int total_seconds = static_cast<int>((remaining_ms + 999) / 1000);
  const int hours = total_seconds / 3600;
  const int minutes = (total_seconds % 3600) / 60;
  const int seconds = total_seconds % 60;
  if (hours > 0) {
    std::snprintf(output, output_size, "%d:%02d:%02d", hours, minutes, seconds);
  } else {
    std::snprintf(output, output_size, "%02d:%02d", minutes, seconds);
  }
}

void draw_timer_text(
    const AnimatedTextLayout &layout,
    ClockFont *font,
    const char *text,
    const char *label) {
  if (!load_bitmap_font(font, layout.font, label)) {
    const int fallback_scale = (layout.font_size * 100) / 7;
    draw_text(layout.x - (text_width(text, fallback_scale) / 2), layout.y, text, fallback_scale, layout.color);
    return;
  }
  const int width = bitmap_text_width(*font, text, layout.font_size);
  if (width > 0) {
    draw_bitmap_text(*font, text, layout.x - (width / 2), layout.y, layout.font_size, layout.color);
  }
}

void draw_timer_screen(const hexe::AppState &state) {
  if (!g_status_layout.timer_screen.enabled || !state.timer_active || state.display_timer_count == 0 ||
      state.phase != hexe::AppPhase::kIdle) {
    return;
  }
  const auto &screen = g_status_layout.timer_screen;
  const auto &primary = state.display_timers[0];
  char countdown[16] = {};
  format_timer_countdown(display_timer_remaining_ms(primary), countdown, sizeof(countdown));
  draw_timer_text(screen.primary_countdown, &g_timer_countdown_font, countdown, "timer countdown");

  char primary_label[32] = {};
  std::snprintf(primary_label, sizeof(primary_label), "%.24s", primary.label[0] != '\0' ? primary.label : "Timer");
  draw_timer_text(screen.primary_label, &g_timer_label_font, primary_label, "timer label");

  const size_t upcoming_count = std::min<size_t>(
      state.display_timer_count > 0 ? state.display_timer_count - 1 : 0,
      static_cast<size_t>(screen.upcoming.count));
  for (size_t index = 0; index < upcoming_count; ++index) {
    const auto &timer = state.display_timers[index + 1];
    char remaining[16] = {};
    char row[64] = {};
    format_timer_countdown(display_timer_remaining_ms(timer), remaining, sizeof(remaining));
    std::snprintf(row, sizeof(row), "%.15s  %s", timer.label[0] != '\0' ? timer.label : "Timer", remaining);
    AnimatedTextLayout row_layout = {};
    row_layout.x = screen.upcoming.x;
    row_layout.y = screen.upcoming.y + static_cast<int>(index) * screen.upcoming.gap;
    row_layout.font_size = screen.upcoming.font_size;
    row_layout.color = screen.upcoming.color;
    std::snprintf(row_layout.font, sizeof(row_layout.font), "%s", screen.upcoming.font);
    draw_timer_text(row_layout, &g_timer_upcoming_font, row, "upcoming timers");
  }
}

uint8_t status_sprite_opacity(const StatusIconLayout &layout, const hexe::AppState &state, int64_t now_ms) {
  int opacity = 255;
  for (size_t index = 0; index < layout.animation_count; ++index) {
    const auto &animation = layout.animations[index];
    if (animation.type != StatusAnimationType::kPulse || !status_animation_active(animation, state)) {
      continue;
    }
    const int phase = animation_triangle_per_mille(animation, now_ms);
    const int animated = animation.min_opacity + ((animation.max_opacity - animation.min_opacity) * phase / 1000);
    opacity = std::min(opacity, animated);
  }
  return static_cast<uint8_t>(opacity);
}

void draw_status_animation(
    const StatusAnimation &animation,
    const StatusSprite &sprite,
    int sprite_x,
    int sprite_y,
    int64_t now_ms) {
  const int size = std::min(sprite.width, sprite.height);
  const int center_x = sprite_x + relative_pixels(animation.x_per_mille, sprite.width);
  const int center_y = sprite_y + relative_pixels(animation.y_per_mille, sprite.height);
  const int radius = std::max(1, relative_pixels(animation.radius_per_mille, size));
  const int phase = animation_phase_per_mille(animation, now_ms);

  switch (animation.type) {
    case StatusAnimationType::kBlinkDot:
      if (phase < 500) {
        draw_disc(center_x, center_y, radius, animation.color);
      }
      break;
    case StatusAnimationType::kRunningDots: {
      const int spacing = std::max(radius * 2 + 1, relative_pixels(animation.spacing_per_mille, size));
      const int first_x = center_x - ((animation.count - 1) * spacing / 2);
      const int active = std::min(animation.count - 1, phase * animation.count / 1000);
      for (int index = 0; index < animation.count; ++index) {
        draw_disc(
            first_x + (index * spacing),
            center_y,
            radius,
            index == active ? animation.color : scale_color(animation.color, 250));
      }
      break;
    }
    case StatusAnimationType::kPulse:
      break;
    case StatusAnimationType::kPulseRing: {
      const int animated_radius = radius + (radius * phase / 1000);
      const int thickness = std::max(1, radius / 3);
      draw_ring(center_x, center_y, animated_radius, thickness, scale_color(animation.color, 1000 - (phase * 700 / 1000)));
      break;
    }
    case StatusAnimationType::kSlideIn:
      break;
    case StatusAnimationType::kInvalid:
      break;
  }
}

void draw_status_icon(
    StatusIconId id,
    const StatusIconLayout &layout,
    int x,
    int y,
    const hexe::AppState &state,
    int64_t now_ms) {
  StatusSprite *sprite = status_icon_sprite(id, state);
  if (sprite == nullptr) {
    return;
  }
  int slide_x = 0;
  int slide_y = 0;
  status_sprite_slide_offset(id, layout, state, sprite->width, sprite->height, now_ms, &slide_x, &slide_y);
  x += slide_x;
  y += slide_y;
  draw_status_sprite(sprite, x, y, status_sprite_opacity(layout, state, now_ms));
  for (size_t index = 0; index < layout.animation_count; ++index) {
    const auto &animation = layout.animations[index];
    if (animation.type != StatusAnimationType::kPulse && animation.type != StatusAnimationType::kSlideIn &&
        status_animation_active(animation, state)) {
      draw_status_animation(animation, *sprite, x, y, now_ms);
    }
  }
}

void draw_header_status_icons() {
  const auto &state = hexe::state();
  load_status_layout();
  const int64_t now_ms = g_frame_time_ms;

  for (size_t index = 0; index < static_cast<size_t>(StatusIconId::kCount); ++index) {
    const auto id = static_cast<StatusIconId>(index);
    const auto &layout = g_status_layout.icons[index];
    if (!layout.floating && status_icon_active(id, state)) {
      draw_status_icon(id, layout, layout.x, g_status_layout.y, state, now_ms);
    }
  }

  int floating_x = g_status_layout.floating_x;
  for (size_t index = 0; index < g_status_layout.floating_count; ++index) {
    const StatusIconId id = g_status_layout.floating_order[index];
    if (!status_icon_active(id, state)) {
      continue;
    }
    StatusSprite *sprite = status_icon_sprite(id, state);
    const auto &layout = g_status_layout.icons[static_cast<size_t>(id)];
    draw_status_icon(id, layout, floating_x, g_status_layout.y, state, now_ms);
    floating_x += sprite->width + g_status_layout.floating_gap;
  }
}

void draw_header_clock() {
  if (g_status_layout.idle_clock.enabled && status_flag_value(StatusFlag::kIdleReady, hexe::state())) {
    return;
  }
  if (!hexe::system::clock_synced()) {
    return;
  }
  std::tm local = {};
  if (!hexe::system::current_local_time(&local)) {
    return;
  }
  int hour = local.tm_hour % 12;
  if (hour == 0) {
    hour = 12;
  }
  char clock_text[16] = {};
  std::snprintf(clock_text, sizeof(clock_text), "%02d:%02d", hour, local.tm_min);
  load_status_layout();

  if (!load_bitmap_font(
          &g_clock_font,
          g_status_layout.clock.font[0] != '\0' ? g_status_layout.clock.font : kDefaultClockFont,
          "clock")) {
    draw_centered_text(
        10 + g_status_layout.clock.y_offset,
        clock_text,
        (g_status_layout.clock.font_size * 600) / 42,
        g_status_layout.clock.color);
    return;
  }

  const ClockGlyph *glyphs[5] = {};
  for (size_t index = 0; index < 5; ++index) {
    glyphs[index] = font_glyph(g_clock_font, clock_text[index]);
    if (glyphs[index] == nullptr) {
      return;
    }
  }
  const int colon_center =
      scale_font_metric(
          g_clock_font,
          g_status_layout.clock.font_size,
          glyphs[0]->advance + glyphs[1]->advance) +
      (scale_font_metric(g_clock_font, g_status_layout.clock.font_size, glyphs[2]->advance) / 2);
  int cursor_x = (kWidth / 2) + g_status_layout.clock.x_offset - colon_center;
  const int baseline_y = 48 + g_status_layout.clock.y_offset;
  const int top_y = baseline_y -
      scale_font_metric(g_clock_font, g_status_layout.clock.font_size, g_clock_font.ascent);
  draw_bitmap_text(
      g_clock_font,
      clock_text,
      cursor_x,
      top_y,
      g_status_layout.clock.font_size,
      g_status_layout.clock.color);
}

void draw_version_text(const char *build_id) {
  if (build_id == nullptr || build_id[0] == '\0') {
    return;
  }
  load_status_layout();
  const char *separator = std::strchr(build_id, '-');
  const char *suffix = separator == nullptr ? build_id : separator + 1;
  const char *suffix_end = std::strchr(suffix, '-');
  const size_t length = suffix_end == nullptr ? std::strlen(suffix) : static_cast<size_t>(suffix_end - suffix);
  char version[32] = {};
  std::snprintf(version, sizeof(version), "%.*s", static_cast<int>(length), suffix);

  if (!load_bitmap_font(
          &g_version_font,
          g_status_layout.version.font[0] != '\0' ? g_status_layout.version.font : kDefaultVersionFont,
          "version") ||
      !draw_bitmap_text(
          g_version_font,
          version,
          g_status_layout.version.x,
          g_status_layout.version.y,
          g_status_layout.version.font_size,
          g_status_layout.version.color)) {
    draw_text(
        g_status_layout.version.x,
        g_status_layout.version.y,
        version,
        (g_status_layout.version.font_size * 100) / 7,
        g_status_layout.version.color);
  }
}

void release_status_sprite(StatusSprite *sprite) {
  if (sprite == nullptr) {
    return;
  }
  heap_caps_free(sprite->colors);
  heap_caps_free(sprite->alpha);
  sprite->colors = nullptr;
  sprite->alpha = nullptr;
  sprite->load_attempted = false;
}

void release_bitmap_font(ClockFont *font);

void reload_display_assets() {
  if (g_background_pixels != nullptr) {
    heap_caps_free(g_background_pixels);
    g_background_pixels = nullptr;
  }
  g_logged_bg_missing = false;
  g_logged_bg_bad_size = false;
  g_status_layout_loaded = false;
  release_bitmap_font(&g_clock_font);
  release_bitmap_font(&g_version_font);
  release_status_sprite(&g_wifi_on_sprite);
  release_status_sprite(&g_wifi_off_sprite);
  release_status_sprite(&g_node_connected_sprite);
  release_status_sprite(&g_asset_downloading_sprite);
  release_status_sprite(&g_mic_enabled_sprite);
  release_status_sprite(&g_mic_disabled_sprite);
  release_status_sprite(&g_mic_active_sprite);
  release_status_sprite(&g_mute_sprite);
  release_status_sprite(&g_dnd_sprite);
  release_status_sprite(&g_timer_sprite);
  release_status_sprite(&g_alarm_sprite);
  release_status_sprite(&g_playback_sprite);
  release_status_sprite(&g_update_available_sprite);
  release_status_sprite(&g_warning_sprite);
  release_status_sprite(&g_privacy_sprite);
  release_status_sprite(&g_cloud_offline_sprite);
  release_status_sprite(&g_sidebar_sprite);
  release_status_sprite(&g_sidebar_right_sprite);
  release_status_sprite(&g_idle_clock_frame_sprite);
  release_status_sprite(&g_activity_listening_sprite);
  release_status_sprite(&g_activity_thinking_sprite);
  release_status_sprite(&g_activity_replay_sprite);
  release_status_sprite(&g_activity_timer_sprite);
  release_status_sprite(&g_button_timer_sprite);
  release_status_sprite(&g_button_weather_sprite);
  release_status_sprite(&g_button_config_sprite);
  release_bitmap_font(&g_idle_hours_font);
  release_bitmap_font(&g_idle_separator_font);
  release_bitmap_font(&g_idle_minutes_font);
  release_bitmap_font(&g_idle_date_font);
  ESP_LOGI(kTag, "Reloading display assets on render task");
}

void release_bitmap_font(ClockFont *font) {
  if (font == nullptr) {
    return;
  }
  heap_caps_free(font->data);
  *font = ClockFont{};
}

bool build_sd_test_background_path(char *path, size_t path_size) {
  if (path == nullptr || path_size == 0) {
    return false;
  }
  const int written =
      std::snprintf(path, path_size, "%s/%s", hexe::board::sd_card_pictures_path(), kSdTestBackgroundName);
  return written > 0 && written < static_cast<int>(path_size);
}

bool load_sd_test_background(char *path, size_t path_size) {
  if (g_background_pixels != nullptr) {
    return build_sd_test_background_path(path, path_size);
  }
  if (!hexe::board::sd_card_mounted()) {
    if (!g_logged_sd_unavailable) {
      ESP_LOGI(kTag, "No microSD mounted; using procedural P4 display background");
      g_logged_sd_unavailable = true;
    }
    return false;
  }
  if (!build_sd_test_background_path(path, path_size)) {
    ESP_LOGW(kTag, "Could not build P4 SD test background path");
    return false;
  }

  struct stat info = {};
  if (stat(path, &info) != 0) {
    if (!g_logged_bg_missing) {
      ESP_LOGI(kTag, "P4 SD test background not found at %s: %s", path, std::strerror(errno));
      g_logged_bg_missing = true;
    }
    return false;
  }
  if (static_cast<size_t>(info.st_size) != kSdTestBackgroundBytes) {
    if (!g_logged_bg_bad_size) {
      ESP_LOGW(
          kTag,
          "Ignoring P4 SD test background %s: expected %u bytes for %dx%d RGB888, got %ld",
          path,
          static_cast<unsigned>(kSdTestBackgroundBytes),
          kWidth,
          kHeight,
          static_cast<long>(info.st_size));
      g_logged_bg_bad_size = true;
    }
    return false;
  }

  g_background_pixels = static_cast<uint8_t *>(
      heap_caps_malloc(kSdTestBackgroundBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (g_background_pixels == nullptr) {
    ESP_LOGW(kTag, "No PSRAM available for P4 background cache; using procedural background");
    return false;
  }
  FILE *file = std::fopen(path, "rb");
  if (file == nullptr) {
    ESP_LOGW(kTag, "Could not open P4 SD test background %s: %s", path, std::strerror(errno));
    heap_caps_free(g_background_pixels);
    g_background_pixels = nullptr;
    return false;
  }
  const size_t read_bytes = std::fread(g_background_pixels, 1, kSdTestBackgroundBytes, file);
  std::fclose(file);
  if (read_bytes != kSdTestBackgroundBytes) {
    ESP_LOGW(
        kTag,
        "Could not cache P4 SD test background %s: expected %u bytes, got %u",
        path,
        static_cast<unsigned>(kSdTestBackgroundBytes),
        static_cast<unsigned>(read_bytes));
    heap_caps_free(g_background_pixels);
    g_background_pixels = nullptr;
    return false;
  }
  for (size_t offset = 0; offset < kSdTestBackgroundBytes; offset += kBytesPerPixel) {
    std::swap(g_background_pixels[offset], g_background_pixels[offset + 2]);
  }
  ESP_LOGI(kTag, "Cached P4 SD test background from %s", path);
  return true;
}

bool copy_sd_background_strip() {
  if (g_background_pixels == nullptr || g_flush_buffer == nullptr) {
    return false;
  }
  const size_t expected_bytes =
      static_cast<size_t>(kWidth) * static_cast<size_t>(g_strip_rows) * kBytesPerPixel;
  const size_t offset = static_cast<size_t>(g_strip_y) * kWidth * kBytesPerPixel;
  std::memcpy(g_flush_buffer, g_background_pixels + offset, expected_bytes);
  return true;
}

void clear_strip() {
  for (int row = 0; row < g_strip_rows; ++row) {
    const int y = g_strip_y + row;
    const bool alt_band = ((y / 36) % 2) == 0;
    const uint32_t base = alt_band ? kCanvas : kCanvasAlt;
    for (int x = 0; x < kWidth; ++x) {
      set_pixel(x, y, base);
    }
  }
}

bool draw_status_frame(int frame, const char *build_id, bool background_loaded) {
  const auto &state = hexe::state();
  const uint32_t accent = phase_color(state.phase);
  const bool drew_background = background_loaded && copy_sd_background_strip();
  if (!drew_background) {
    clear_strip();
  } else {
    load_status_layout();
    draw_sidebars(state, g_frame_time_ms);
    draw_idle_clock(state, g_frame_time_ms);
    draw_activity_sprite(state, g_frame_time_ms);
    draw_timer_screen(state);
    draw_header_clock();
    draw_header_status_icons();
    draw_version_text(build_id);
    (void)frame;
    return true;
  }

  for (int x = 64; x < kWidth; x += 64) {
    draw_vline(x, 0, kHeight, rgb565_to_rgb888(0x1168));
  }
  for (int y = 48; y < kHeight; y += 48) {
    draw_hline(0, y, kWidth, rgb565_to_rgb888(0x1168));
  }

  fill_rect(0, 0, kWidth, 12, accent);
  fill_rect(0, kHeight - 12, kWidth, 12, accent);
  draw_rect_outline(32, 32, kWidth - 64, kHeight - 64, rgb565_to_rgb888(0x3A49));
  draw_rect_outline(44, 44, kWidth - 88, kHeight - 88, rgb565_to_rgb888(0x19CC));

  draw_ring(kWidth / 2, 284, 154, 8, rgb565_to_rgb888(0x29CF));
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
  draw_centered_text(466, detail, 210, rgb565_to_rgb888(0xBDF7));

  char version[96] = {};
  std::snprintf(version, sizeof(version), "build %s", build_id == nullptr ? "unknown" : build_id);
  draw_text(60, 548, version, 190, rgb565_to_rgb888(0xBDF7));

  if (state.ota_active || state.phase == hexe::AppPhase::kUpdating) {
    const int progress = std::clamp(state.ota_progress_percent, 0, 100);
    fill_rect(704, 548, 260, 16, rgb565_to_rgb888(0x2965));
    fill_rect(704, 548, (260 * progress) / 100, 16, accent);
    draw_rect_outline(704, 548, 260, 16, kInk);
  } else {
    draw_text(720, 548, "single app", 190, rgb565_to_rgb888(0xBDF7));
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
  g_frame_time_ms = started_us / 1000;
  char background_path[128] = {};
  const int64_t asset_read_started_us = black ? 0 : esp_timer_get_time();
  const bool background_loaded = !black && load_sd_test_background(background_path, sizeof(background_path));
  const int asset_read_ms = black ? 0 : static_cast<int>((esp_timer_get_time() - asset_read_started_us) / 1000);
  bool used_background = false;
  for (int y = 0; y < kHeight; y += kFlushRows) {
    g_strip_y = y;
    g_strip_rows = std::min(kFlushRows, kHeight - y);
    if (black) {
      fill_rect(0, y, kWidth, g_strip_rows, kBlack);
    } else {
      used_background = draw_status_frame(frame, build_id, background_loaded) || used_background;
    }
    if (!flush_strip(y, g_strip_rows)) {
      return false;
    }
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
  const size_t flush_buffer_bytes = static_cast<size_t>(kWidth) * kFlushRows * kBytesPerPixel;
  g_flush_buffer = static_cast<uint8_t *>(heap_caps_malloc(flush_buffer_bytes, MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_flush_buffer == nullptr) {
    g_flush_buffer = static_cast<uint8_t *>(
        heap_caps_malloc(flush_buffer_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
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
  ESP_LOGI(kTag, "Waveshare P4 7B display initialized at %dx%d RGB888", kWidth, kHeight);
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
  if (g_display_assets_reload_requested.exchange(false, std::memory_order_acquire)) {
    reload_display_assets();
    g_force_redraw = true;
  }
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
  g_display_assets_reload_requested.store(true, std::memory_order_release);
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
  return "rgb888";
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
