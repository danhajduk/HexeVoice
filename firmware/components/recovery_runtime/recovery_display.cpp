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
