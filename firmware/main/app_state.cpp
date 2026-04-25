#include "app_state.h"

namespace hexe {

AppState &state() {
  static AppState app_state;
  return app_state;
}

void advance_loading_frame() {
  auto &app_state = state();
  app_state.loading_frame = (app_state.loading_frame + 1) % 120;
}

}  // namespace hexe
