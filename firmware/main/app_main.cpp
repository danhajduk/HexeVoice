#include "app_state.h"
#include "board/audio.h"
#include "board/buttons.h"
#include "board/display.h"
#include "board/storage.h"
#include "board/wifi.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "system/log_stream.h"
#include "system/ota.h"
#include "system/power.h"
#include "system/settings.h"
#include "system/telemetry.h"
#include "ui/animator.h"
#include "ui/screens.h"
#include "voice/assistant_client.h"
#include "voice/backend_client.h"
#include "voice/stt_stream.h"
#include "voice/tts_player.h"
#include "voice/wake_word.h"

namespace {
constexpr char kTag[] = "hexe_main";
constexpr int kPanelSettleDelayMs = 250;
constexpr int kBacklightSettleDelayMs = 200;
constexpr int kPostDisplayInitDelayMs = 100;
constexpr int kBootAnimationDelayMs = 60;
constexpr int kIdleRenderDelayMs = 25;
}

extern "C" void app_main(void) {
  const esp_app_desc_t *app = esp_app_get_description();
  ESP_LOGI(kTag, "Starting Hexe native firmware scaffold");
  ESP_LOGI(kTag, "Firmware project=%s version=%s", app->project_name, app->version);

  hexe::system::init_settings();
  hexe::board::init_storage();
  hexe::board::init_display();
  vTaskDelay(pdMS_TO_TICKS(kPostDisplayInitDelayMs));
  hexe::board::show_black_frame();
  vTaskDelay(pdMS_TO_TICKS(kPanelSettleDelayMs));
  hexe::board::turn_on_backlight();
  vTaskDelay(pdMS_TO_TICKS(kBacklightSettleDelayMs));
  hexe::ui::init_animator();
  hexe::ui::render_boot_screen();

  hexe::board::init_buttons();
  hexe::board::init_audio();
  hexe::board::init_wifi();
  hexe::system::init_log_stream();

  hexe::voice::init_wake_word();
  hexe::voice::init_backend_client();
  hexe::voice::init_stt_stream();
  hexe::voice::init_tts_player();
  hexe::voice::init_assistant_client();

  hexe::system::init_power();
  hexe::system::init_telemetry();
  hexe::system::init_ota();

  ESP_LOGI(kTag, "Hexe scaffold initialized for build %s", app->version);

  while (true) {
    auto &state = hexe::state();
    if (state.phase == hexe::AppPhase::kBooting) {
      hexe::advance_loading_frame();
    }

    hexe::board::update_audio();
    hexe::board::update_buttons();
    hexe::board::refresh_wifi_status();

    const int frame = state.loading_frame;
    hexe::board::render_boot_frame(frame, app->version);
    const int delay_ms = state.phase == hexe::AppPhase::kBooting ? kBootAnimationDelayMs : kIdleRenderDelayMs;
    vTaskDelay(pdMS_TO_TICKS(delay_ms));
  }
}
