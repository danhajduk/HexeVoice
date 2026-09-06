#include "recovery_display.h"

#include <algorithm>
#include <cstdint>
#include <cstring>

#include "board_profile_pins.h"
#include "recovery_ble_provisioning.h"
#include "recovery_control.h"
#include "esp_log.h"

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

#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
constexpr int kWidth = 360;
constexpr int kHeight = 360;
constexpr int kFlushRows = 16;
constexpr size_t kAssetBytes = kWidth * kHeight * sizeof(uint16_t);
constexpr spi_host_device_t kDisplaySpiHost = SPI2_HOST;
constexpr uint8_t kTcaRegisterOutput = 0x01;
constexpr uint8_t kTcaRegisterConfig = 0x03;
constexpr uint8_t kDisplayResetBit = 2;
constexpr int kI2cTimeoutMs = 1000;

extern const uint8_t _binary_min_fw_waiting_to_pair_rgb565_start[] asm("_binary_min_fw_waiting_to_pair_rgb565_start");
extern const uint8_t _binary_min_fw_waiting_to_pair_rgb565_end[] asm("_binary_min_fw_waiting_to_pair_rgb565_end");
extern const uint8_t _binary_min_fw_pairing_rgb565_start[] asm("_binary_min_fw_pairing_rgb565_start");
extern const uint8_t _binary_min_fw_pairing_rgb565_end[] asm("_binary_min_fw_pairing_rgb565_end");
extern const uint8_t _binary_ota_progress_rgb565_start[] asm("_binary_ota_progress_rgb565_start");
extern const uint8_t _binary_ota_progress_rgb565_end[] asm("_binary_ota_progress_rgb565_end");

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
        overlay_progress_row(y + row, target, progress);
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
#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
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

  esp_lcd_panel_io_handle_t io_handle = nullptr;
  esp_lcd_panel_io_spi_config_t io_config = {};
  io_config.cs_gpio_num = gpio_pin(hexe::board::pins::kWs185DisplayCs);
  io_config.dc_gpio_num = GPIO_NUM_NC;
  io_config.spi_mode = 0;
  io_config.pclk_hz = 40 * 1000 * 1000;
  io_config.trans_queue_depth = 10;
  io_config.on_color_trans_done = on_color_transfer_done;
  io_config.user_ctx = &g_flush_done;
  io_config.lcd_cmd_bits = 32;
  io_config.lcd_param_bits = 8;
  io_config.flags.quad_mode = 1;
  result = esp_lcd_new_panel_io_spi(static_cast<esp_lcd_spi_bus_handle_t>(kDisplaySpiHost), &io_config, &io_handle);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Recovery display disabled: panel IO failed: %s", esp_err_to_name(result));
    return;
  }

  st77916_vendor_config_t vendor_config = {};
  vendor_config.flags.use_qspi_interface = 1;
  esp_lcd_panel_dev_config_t panel_config = {};
  panel_config.reset_gpio_num = GPIO_NUM_NC;
  panel_config.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
  panel_config.bits_per_pixel = 16;
  panel_config.vendor_config = &vendor_config;

  if (!pulse_display_reset()) {
    ESP_LOGW(kTag, "Recovery display reset pulse failed; attempting panel init anyway");
  }
  result = esp_lcd_new_panel_st77916(io_handle, &panel_config, &g_panel);
  if (result != ESP_OK) {
    ESP_LOGW(kTag, "Recovery display disabled: panel create failed: %s", esp_err_to_name(result));
    return;
  }
  ESP_ERROR_CHECK(esp_lcd_panel_init(g_panel));
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
#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
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
#if HEXE_BOARD_PROFILE_WAVESHARE_S3_TOUCH_LCD_1_85C_BOX_V2
  return g_display_ready;
#else
  return false;
#endif
}

}  // namespace hexe::recovery
