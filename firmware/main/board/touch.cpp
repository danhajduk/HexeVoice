#include "board/touch.h"

#include "bsp/touch.h"
#include "esp_err.h"
#include "esp_lcd_touch.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_touch";

esp_lcd_touch_handle_t g_touch = nullptr;
bool g_touch_ready = false;
}

namespace hexe::board {

void init_touch() {
  if (g_touch_ready) {
    return;
  }

  const esp_err_t result = bsp_touch_new(nullptr, &g_touch);
  if (result != ESP_OK || g_touch == nullptr) {
    ESP_LOGW(kTag, "Touchscreen unavailable: %s", esp_err_to_name(result));
    return;
  }

  g_touch_ready = true;
  ESP_LOGI(kTag, "Touchscreen initialized");
}

bool touch_ready() {
  return g_touch_ready;
}

}  // namespace hexe::board
