#include "ui/screens.h"

#include "app_state.h"
#include "esp_log.h"

namespace {
constexpr char kTag[] = "hexe_screens";
}

namespace hexe::ui {

void render_boot_screen() {
  hexe::state().phase = hexe::AppPhase::kBooting;
  ESP_LOGI(kTag, "Render boot screen placeholder with Hexe logo animation");
}

}  // namespace hexe::ui
