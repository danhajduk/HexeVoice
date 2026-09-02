set(HEXE_FIRMWARE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(HEXE_ENDPOINT_CONFIG "${HEXE_FIRMWARE_ROOT}/config/endpoint.yaml")
if(NOT EXISTS "${HEXE_ENDPOINT_CONFIG}")
  set(HEXE_ENDPOINT_CONFIG "${HEXE_FIRMWARE_ROOT}/config/endpoint.example.yaml")
endif()
set(HEXE_ENDPOINT_CONFIG_GENERATOR "${HEXE_FIRMWARE_ROOT}/tools/generate_endpoint_config.py")
set(HEXE_BOARD_PROFILE_ROOT "${HEXE_FIRMWARE_ROOT}/boards")
set(HEXE_BOARD_PROFILE_GENERATOR "${HEXE_FIRMWARE_ROOT}/tools/generate_board_profile_config.py")
set(HEXE_BOARD_PROFILE_VALIDATOR "${HEXE_FIRMWARE_ROOT}/tools/validate_board_profiles.py")
file(GLOB HEXE_BOARD_PROFILE_CONFIGS "${HEXE_BOARD_PROFILE_ROOT}/*/board.yaml")
set_property(
  DIRECTORY
  APPEND
  PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${HEXE_ENDPOINT_CONFIG}"
    "${HEXE_ENDPOINT_CONFIG_GENERATOR}"
    "${HEXE_BOARD_PROFILE_GENERATOR}"
    "${HEXE_BOARD_PROFILE_VALIDATOR}"
    ${HEXE_BOARD_PROFILE_CONFIGS}
)

if(DEFINED ENV{HEXE_BOARD_PROFILE} AND NOT "$ENV{HEXE_BOARD_PROFILE}" STREQUAL "")
  set(HEXE_BOARD_PROFILE "$ENV{HEXE_BOARD_PROFILE}" CACHE STRING "Hexe firmware board profile" FORCE)
elseif(NOT DEFINED HEXE_BOARD_PROFILE OR HEXE_BOARD_PROFILE STREQUAL "")
  set(HEXE_BOARD_PROFILE "esp_box_3")
endif()

set(HEXE_GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
execute_process(
  COMMAND python3
    "${HEXE_BOARD_PROFILE_GENERATOR}"
    --profile-root "${HEXE_BOARD_PROFILE_ROOT}"
    --board-profile "${HEXE_BOARD_PROFILE}"
    --output "${HEXE_GENERATED_DIR}/board_profile_config.cmake"
    --header-output "${HEXE_GENERATED_DIR}/board_profile_pins.h"
  RESULT_VARIABLE HEXE_BOARD_PROFILE_CONFIG_RESULT
)
if(NOT HEXE_BOARD_PROFILE_CONFIG_RESULT EQUAL 0)
  message(FATAL_ERROR "Failed to generate board profile config for ${HEXE_BOARD_PROFILE}")
endif()
include("${HEXE_GENERATED_DIR}/board_profile_config.cmake")

if(NOT HEXE_BOARD_ADAPTER_BUILDABLE)
  message(FATAL_ERROR "Board profile ${HEXE_BOARD_PROFILE} is defined but its firmware adapters are not buildable yet")
endif()
if(NOT HEXE_BOARD_SRCS)
  message(FATAL_ERROR "Board profile ${HEXE_BOARD_PROFILE} did not define any firmware adapter source files")
endif()

execute_process(
  COMMAND python3
    "${HEXE_ENDPOINT_CONFIG_GENERATOR}"
    --input "${HEXE_ENDPOINT_CONFIG}"
    --output "${HEXE_GENERATED_DIR}/endpoint_config.h"
    --board-profile "${HEXE_BOARD_PROFILE}"
  RESULT_VARIABLE HEXE_ENDPOINT_CONFIG_RESULT
)
if(NOT HEXE_ENDPOINT_CONFIG_RESULT EQUAL 0)
  message(FATAL_ERROR "Failed to generate endpoint_config.h from ${HEXE_ENDPOINT_CONFIG}")
endif()

message(STATUS "Hexe board profile: ${HEXE_BOARD_PROFILE}")
message(STATUS "Hexe board profile file: ${HEXE_BOARD_PROFILE_PATH}")
message(STATUS "Hexe board partition schema: ${HEXE_BOARD_PARTITION_SCHEMA}")

idf_component_register(
  SRCS
    "app_state.cpp"
    ${HEXE_BOARD_SRCS}
    "board/wifi.cpp"
    "system/clock.cpp"
    "system/ota.cpp"
    "system/log_stream.cpp"
    "system/power.cpp"
    "system/settings.cpp"
    "system/telemetry.cpp"
    "ui/animator.cpp"
    "ui/screens.cpp"
    "voice/assistant_client.cpp"
    "voice/backend_client.cpp"
    "voice/micro_wake_engine.cpp"
    "voice/model_bundle.cpp"
    "voice/stt_stream.cpp"
    "voice/wake_word.cpp"
  INCLUDE_DIRS
    "."
    "${HEXE_GENERATED_DIR}"
  EMBED_FILES
    "voice/models/audio_preprocessor_int8.tflite"
    "voice/models/alexa.tflite"
    "voice/models/stop.tflite"
  REQUIRES
    app_update
    esp_app_format
    esp_event
    esp_driver_gpio
    esp_driver_i2c
    esp_driver_i2s
    esp_driver_rmt
    esp_https_ota
    esp_http_client
    esp_lcd
    esp_netif
    esp_partition
    esp-tflite-micro
    esp_websocket_client
    esp_wifi
    espressif__esp_codec_dev
    espressif__esp_lcd_st77916
    espressif__esp_lcd_touch
    fatfs
    cjson
    lwip
    mbedtls
    nvs_flash
    sdmmc
)

target_compile_definitions(
  ${COMPONENT_LIB}
  PRIVATE
    ${HEXE_BOARD_DEFINITIONS}
    HEXE_MICRO_WAKE_WORD_ENGINE_LINKED=1
    HEXE_MICRO_WAKE_WORD_TFLM_ENABLED=1
    HEXE_MICRO_WAKE_WORD_FEATURE_FRONTEND_ENABLED=1
)
