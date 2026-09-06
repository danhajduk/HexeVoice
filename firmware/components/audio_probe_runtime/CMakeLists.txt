set(HEXE_FIRMWARE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../..")
set(HEXE_ENDPOINT_CONFIG "${HEXE_FIRMWARE_ROOT}/config/endpoint.yaml")
if(NOT EXISTS "${HEXE_ENDPOINT_CONFIG}")
  set(HEXE_ENDPOINT_CONFIG "${HEXE_FIRMWARE_ROOT}/config/endpoint.example.yaml")
endif()
set(HEXE_ENDPOINT_CONFIG_GENERATOR "${HEXE_FIRMWARE_ROOT}/tools/generate_endpoint_config.py")
set(HEXE_BOARD_PROFILE_ROOT "${HEXE_FIRMWARE_ROOT}/boards")
set(HEXE_BOARD_PROFILE_GENERATOR "${HEXE_FIRMWARE_ROOT}/tools/generate_board_profile_config.py")

if(DEFINED ENV{HEXE_BOARD_PROFILE} AND NOT "$ENV{HEXE_BOARD_PROFILE}" STREQUAL "")
  set(HEXE_BOARD_PROFILE "$ENV{HEXE_BOARD_PROFILE}" CACHE STRING "Hexe firmware board profile" FORCE)
elseif(NOT DEFINED HEXE_BOARD_PROFILE OR HEXE_BOARD_PROFILE STREQUAL "")
  set(HEXE_BOARD_PROFILE "ha_voice_pe")
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

message(STATUS "Hexe audio probe board profile: ${HEXE_BOARD_PROFILE}")

idf_component_register(
  SRCS
    "audio_probe.cpp"
  INCLUDE_DIRS
    "."
    "${HEXE_GENERATED_DIR}"
  REQUIRES
    esp_event
    esp_driver_gpio
    esp_driver_i2c
    esp_driver_i2s
    esp_netif
    esp_wifi
    freertos
    heap
    log
    lwip
    nvs_flash
)
