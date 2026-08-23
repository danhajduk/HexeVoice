#include "ui/animator.h"

#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_animator";
}

namespace hexe::ui {

void init_animator() {
  ESP_LOGI(kTag, "Animator ready for Hexe boot/logo motion");
}

}  // namespace hexe::ui
