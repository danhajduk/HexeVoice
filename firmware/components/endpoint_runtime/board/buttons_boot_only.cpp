#include "board/buttons.h"

#include "board/pins.h"
#include "driver/gpio.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_buttons_boot";

constexpr gpio_num_t gpio_pin(int pin) {
  return static_cast<gpio_num_t>(pin);
}
}  // namespace

namespace hexe::board {

void init_buttons() {
  gpio_config_t input_config = {};
  input_config.pin_bit_mask = 1ULL << pins::kBootButton;
  input_config.mode = GPIO_MODE_INPUT;
  input_config.pull_up_en = GPIO_PULLUP_ENABLE;
  input_config.pull_down_en = GPIO_PULLDOWN_DISABLE;
  input_config.intr_type = GPIO_INTR_DISABLE;
  gpio_config(&input_config);
  ESP_LOGW(kTag, "Only BOOT GPIO%d is configured; UI button actions are not implemented yet", pins::kBootButton);
}

void update_buttons() {
  (void)gpio_get_level(gpio_pin(pins::kBootButton));
}

}  // namespace hexe::board
