#include "board/display.h"

#include <atomic>
#include <cstdint>
#include <cstring>

#include "app_state.h"
#include "board/pins.h"
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
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace {
constexpr char kTag[] = "hexe_display_ws185";
constexpr int kWidth = 360;
constexpr int kHeight = 360;
constexpr int kFlushRows = 16;
constexpr uint16_t kBlack = 0x0000;
constexpr spi_host_device_t kDisplaySpiHost = SPI2_HOST;

esp_lcd_panel_handle_t g_panel = nullptr;
uint16_t *g_framebuffer = nullptr;
uint16_t *g_flush_buffer = nullptr;
SemaphoreHandle_t g_flush_done = nullptr;
bool g_display_ready = false;
bool g_backlight_on = false;
std::atomic<bool> g_force_redraw{true};

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}

constexpr uint16_t swap565(uint16_t value) {
  return static_cast<uint16_t>((value >> 8) | (value << 8));
}

uint16_t phase_color(hexe::AppPhase phase) {
  switch (phase) {
    case hexe::AppPhase::kBooting:
      return swap565(0x39E7);
    case hexe::AppPhase::kWiFiConnecting:
    case hexe::AppPhase::kBackendConnecting:
      return swap565(0x001F);
    case hexe::AppPhase::kIdle:
      return swap565(0x07E0);
    case hexe::AppPhase::kListening:
      return swap565(0x07FF);
    case hexe::AppPhase::kThinking:
      return swap565(0xFFE0);
    case hexe::AppPhase::kReplying:
      return swap565(0xFD20);
    case hexe::AppPhase::kUpdating:
      return swap565(0xF81F);
    case hexe::AppPhase::kMuted:
      return swap565(0x8410);
    case hexe::AppPhase::kTimerFinished:
      return swap565(0xF800);
    case hexe::AppPhase::kError:
      return swap565(0xF800);
  }
  return swap565(0xFFFF);
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

void flush_framebuffer() {
  if (g_panel == nullptr || g_framebuffer == nullptr || g_flush_buffer == nullptr) {
    return;
  }
  for (int y = 0; y < kHeight; y += kFlushRows) {
    const int rows = (y + kFlushRows) <= kHeight ? kFlushRows : (kHeight - y);
    const size_t bytes = static_cast<size_t>(kWidth) * static_cast<size_t>(rows) * sizeof(uint16_t);
    std::memcpy(g_flush_buffer, g_framebuffer + (y * kWidth), bytes);
    while (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, 0) == pdTRUE) {
    }
    ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(g_panel, 0, y, kWidth, y + rows, g_flush_buffer));
    if (g_flush_done != nullptr && xSemaphoreTake(g_flush_done, pdMS_TO_TICKS(1000)) != pdTRUE) {
      ESP_LOGW(kTag, "Timed out waiting for LCD flush completion");
    }
  }
}

void draw_status_frame() {
  const auto &state = hexe::state();
  const uint16_t background = state.muted ? swap565(0x2104) : swap565(0x0841);
  const uint16_t accent = phase_color(state.phase);
  fill_frame(background);
  draw_ring(kWidth / 2, kHeight / 2, 154, 12, accent);
  draw_ring(kWidth / 2, kHeight / 2, 118, 6, swap565(0x7BEF));

  const int volume = std::clamp(state.output_volume_percent, 0, 100);
  fill_rect(110, 250, 140, 8, swap565(0x3186));
  fill_rect(110, 250, (140 * volume) / 100, 8, state.muted ? swap565(0xF800) : accent);

  if (state.audio_streaming || state.vad_speaking || state.tts_playback_active) {
    draw_disc(kWidth / 2, kHeight / 2, 38, accent);
  } else {
    draw_ring(kWidth / 2, kHeight / 2, 44, 8, accent);
  }

  if (state.ota_active) {
    const int progress = std::clamp(state.ota_progress_percent, 0, 100);
    fill_rect(90, 284, 180, 8, swap565(0x3186));
    fill_rect(90, 284, (180 * progress) / 100, 8, swap565(0xFFFF));
  }
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
  g_flush_buffer = static_cast<uint16_t *>(heap_caps_malloc(kWidth * kFlushRows * sizeof(uint16_t), MALLOC_CAP_DMA | MALLOC_CAP_8BIT));
  if (g_framebuffer == nullptr || g_flush_buffer == nullptr || g_flush_done == nullptr) {
    ESP_LOGE(kTag, "Failed to allocate Waveshare display buffers");
    return;
  }

  spi_bus_config_t bus_config = ST77916_PANEL_BUS_QSPI_CONFIG(
      pins::kWs185DisplayClk,
      pins::kWs185DisplayData0,
      pins::kWs185DisplayData1,
      pins::kWs185DisplayData2,
      pins::kWs185DisplayData3,
      kWidth * kFlushRows * sizeof(uint16_t));
  esp_err_t result = spi_bus_initialize(kDisplaySpiHost, &bus_config, SPI_DMA_CH_AUTO);
  if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
    ESP_LOGE(kTag, "Failed to initialize ST77916 SPI bus: %s", esp_err_to_name(result));
    return;
  }

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  esp_lcd_panel_io_spi_config_t io_config = ST77916_PANEL_IO_QSPI_CONFIG(pins::kWs185DisplayCs, on_color_transfer_done, &g_flush_done);
  result = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kDisplaySpiHost), &io_config, &io_handle);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create ST77916 panel IO: %s", esp_err_to_name(result));
    return;
  }

  st77916_vendor_config_t vendor_config = {};
  vendor_config.flags.use_qspi_interface = 1;
  esp_lcd_panel_dev_config_t panel_config = {};
  panel_config.reset_gpio_num = GPIO_NUM_NC;
  panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
  panel_config.bits_per_pixel = 16;
  panel_config.vendor_config = &vendor_config;

  waveshare_185_reset_display();
  result = esp_lcd_new_panel_st77916(io_handle, &panel_config, &g_panel);
  if (result != ESP_OK) {
    ESP_LOGE(kTag, "Failed to create ST77916 panel: %s", esp_err_to_name(result));
    return;
  }
  ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
  ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(g_panel, true));

  g_display_ready = true;
  show_black_frame();
  turn_on_backlight();
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
  (void)frame;
  (void)build_id;
  if (!g_display_ready) {
    return;
  }
  draw_status_frame();
  flush_framebuffer();
  g_force_redraw = false;
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

}  // namespace hexe::board
