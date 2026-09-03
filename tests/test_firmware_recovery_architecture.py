from pathlib import Path


RECOVERY_DOC = Path("docs/firmware-recovery-architecture.md")
ROOT_CMAKE = Path("firmware/CMakeLists.txt")
BUILD_SCRIPT = Path("firmware/build.sh")
REBUILD_SCRIPT = Path("scripts/rebuild-firmware.sh")
RECOVERY_README = Path("firmware/apps/recovery/README.md")
RECOVERY_MAIN_CMAKE = Path("firmware/apps/recovery/main/CMakeLists.txt")
RECOVERY_MAIN = Path("firmware/apps/recovery/main/app_main.cpp")
RECOVERY_RUNTIME_CMAKE = Path("firmware/components/recovery_runtime/CMakeLists.txt")
RECOVERY_CONTROL = Path("firmware/components/recovery_runtime/recovery_control.cpp")
RECOVERY_CONTROL_HEADER = Path("firmware/components/recovery_runtime/recovery_control.h")
RECOVERY_BLE = Path("firmware/components/recovery_runtime/recovery_ble_provisioning.cpp")
RECOVERY_BLE_HEADER = Path("firmware/components/recovery_runtime/recovery_ble_provisioning.h")
RECOVERY_STATUS = Path("firmware/components/recovery_runtime/recovery_status.cpp")
RECOVERY_STATUS_HEADER = Path("firmware/components/recovery_runtime/recovery_status.h")
PARTITION_ROADMAP = Path("docs/firmware-partition-ota-roadmap.md")
S3_8M_PARTITIONS = Path("firmware/partitions/s3_8m_v1.csv")
S3_8M_RECOVERY_PARTITIONS = Path("firmware/partitions/s3_8m_recovery_v1.csv")
S3_16M_RECOVERY_PARTITIONS = Path("firmware/partitions/s3_16m_recovery_v1.csv")
P4_32M_PARTITIONS = Path("firmware/partitions/p4_32m_v1.csv")


def test_recovery_architecture_defines_app_boundary_and_build_lane():
    doc = RECOVERY_DOC.read_text()
    root_cmake = ROOT_CMAKE.read_text()

    assert "Status: Task 273 architecture contract; Task 274 skeleton implemented" in doc
    assert "HEXE_FIRMWARE_APP=recovery" in doc
    assert "firmware/apps/recovery/main/" in doc
    assert "application_type=recovery" in doc
    assert "Recovery must not link `endpoint_runtime`." in doc
    assert "HEXE_FIRMWARE_APP" in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT endpoint_runtime" in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT recovery_runtime" in root_cmake
    assert 'apps/${HEXE_FIRMWARE_APP}/main' in root_cmake
    assert "HEXE_FIRMWARE_RUNTIME_DIR" in root_cmake
    assert "COMPONENT_DIRS" in root_cmake
    assert '$ENV{IDF_PATH}/components' in root_cmake
    assert "HEXE_FIRMWARE_ADDITIONAL_COMPONENT_DIRS" in root_cmake
    assert "set(COMPONENTS main ${HEXE_FIRMWARE_RUNTIME_COMPONENT} ${HEXE_FIRMWARE_ADDITIONAL_COMPONENTS})" in root_cmake


def test_recovery_architecture_defines_entry_conditions_and_interfaces():
    doc = RECOVERY_DOC.read_text()

    for entry_reason in (
        "physical_gesture",
        "main_requested",
        "no_valid_main_slot",
        "boot_failure_threshold",
        "factory_or_serial",
    ):
        assert entry_reason in doc

    assert "Recovery must work without Hexe Core and without an SD card." in doc
    assert "temporary Wi-Fi AP" in doc
    assert "serial console diagnostics" in doc
    assert "minimal display/touch status" in doc


def test_recovery_architecture_locks_recovery_diagnostics_api():
    doc = RECOVERY_DOC.read_text()

    for path in (
        "/api/recovery/status",
        "/api/recovery/partitions",
        "/api/recovery/diagnostics",
        "/api/recovery/ble/status",
        "/api/recovery/wifi",
        "/api/recovery/endpoint",
        "/api/recovery/firmware/install",
        "/api/recovery/boot/select",
        "/api/recovery/config/reset",
    ):
        assert path in doc

    assert '"schema_version": "hexe-recovery-status-v1"' in doc
    assert '"recovery_api_version": "hexe-recovery-api-v1"' in doc
    assert '"core_required": false' in doc
    assert '"sd_required": false' in doc


def test_recovery_architecture_links_from_existing_firmware_docs():
    recovery_readme = RECOVERY_README.read_text()
    partition_roadmap = PARTITION_ROADMAP.read_text()

    assert "docs/firmware-recovery-architecture.md" in recovery_readme
    assert "docs/firmware-recovery-architecture.md" in partition_roadmap


def test_recovery_skeleton_has_bootable_app_and_runtime_component():
    main_cmake = RECOVERY_MAIN_CMAKE.read_text()
    main_source = RECOVERY_MAIN.read_text()
    runtime_cmake = RECOVERY_RUNTIME_CMAKE.read_text()
    status_header = RECOVERY_STATUS_HEADER.read_text()

    assert '"app_main.cpp"' in main_cmake
    assert "recovery_runtime" in main_cmake
    assert "endpoint_runtime" not in main_cmake
    assert 'extern "C" void app_main(void)' in main_source
    assert "init_recovery_runtime();" in main_source
    assert "log_recovery_status();" in main_source
    assert '"recovery_status.cpp"' in runtime_cmake
    assert '"recovery_control.cpp"' in runtime_cmake
    assert "generate_board_profile_config.py" in runtime_cmake
    assert "generate_endpoint_config.py" in runtime_cmake
    assert "board_profile_pins.h" in runtime_cmake
    assert "endpoint_config.h" in runtime_cmake
    assert "HEXE_BOARD_RECOVERY_APP" in runtime_cmake
    assert 'HEXE_BOARD_IDF_TARGET STREQUAL "esp32s3"' in runtime_cmake
    assert "HEXE_FIRMWARE_RUNTIME_COMPONENT endpoint_runtime" not in runtime_cmake
    assert "esp-tflite-micro" not in runtime_cmake
    assert "render_status_json()" in status_header
    assert "init_recovery_controls()" in RECOVERY_MAIN.read_text()


def test_recovery_status_skeleton_reports_safe_serial_json():
    status_source = RECOVERY_STATUS.read_text()

    assert 'kSchemaVersion[] = "hexe-recovery-status-v1"' in status_source
    assert 'kRecoveryApiVersion[] = "hexe-recovery-api-v1"' in status_source
    assert 'kApplicationType[] = "recovery"' in status_source
    assert '\\"core_required\\":false' in status_source
    assert '\\"sd_required\\":false' in status_source
    assert '\\"models_required\\":false' in status_source
    assert '\\"endpoint_runtime_linked\\":false' in status_source
    assert "recovery_network_mode()" in status_source
    assert "recovery_ip_address()" in status_source
    assert '\\"interfaces\\":{\\"serial_console\\":true,\\"http_api\\":%s' in status_source
    assert '\\"ble\\":%s' in status_source
    assert '\\"ble_mode\\":\\"%s\\"' in status_source
    assert '\\"actions\\":{\\"wifi_provisioning\\":%s' in status_source
    assert '\\"ble_local_recovery_provisioning\\":%s' in status_source
    assert '\\"ble_core_governed_provisioning\\":false' in status_source
    assert "recovery_wifi_recovery_enabled()" in status_source
    assert "esp_ota_get_running_partition" in status_source
    assert "esp_flash_get_size" in status_source
    assert "esp_psram_is_initialized" in status_source
    assert "nvs_flash_init" in status_source


def test_recovery_control_plane_exposes_local_http_rescue_api():
    control_source = RECOVERY_CONTROL.read_text()
    control_header = RECOVERY_CONTROL_HEADER.read_text()
    runtime_cmake = RECOVERY_RUNTIME_CMAKE.read_text()

    for path in (
        "/api/recovery/status",
        "/api/recovery/partitions",
        "/api/recovery/diagnostics",
        "/api/recovery/ble/status",
        "/api/recovery/wifi",
        "/api/recovery/endpoint",
        "/api/recovery/firmware/install",
        "/api/recovery/boot/select",
        "/api/recovery/config/reset",
    ):
        assert path in control_source

    assert "esp_http_server" in runtime_cmake
    assert "bt" in runtime_cmake
    assert "esp_wifi" in runtime_cmake
    assert "esp_netif" in runtime_cmake
    assert "espressif__cjson" in runtime_cmake
    assert "mbedtls" in runtime_cmake
    assert "httpd_start" in control_source
    assert "HTTPD_DEFAULT_CONFIG" in control_source
    assert "HexeRecovery-" in control_source
    assert "WIFI_MODE_APSTA" in control_source
    assert "init_recovery_controls()" in control_header
    assert "recovery_wifi_recovery_enabled()" in control_header
    assert 'std::strcmp(hexe::board::pins::kBoardProfile, "ha_voice_pe") != 0' in control_source
    assert "Recovery Wi-Fi/HTTP disabled for BLE-only board profile" in control_source
    assert "init_recovery_ble_provisioning()" in control_source
    assert "render_recovery_ble_status_json()" in control_source


def test_recovery_ble_provisioning_supports_local_fallback_without_endpoint_runtime():
    runtime_cmake = RECOVERY_RUNTIME_CMAKE.read_text()
    ble_source = RECOVERY_BLE.read_text()
    ble_header = RECOVERY_BLE_HEADER.read_text()
    status_source = RECOVERY_STATUS.read_text()

    assert '"../endpoint_runtime/system/ble_provisioning_gatt.c"' in runtime_cmake
    assert '"recovery_ble_provisioning.cpp"' in runtime_cmake
    assert "endpoint_runtime" not in runtime_cmake.replace("../endpoint_runtime/system/ble_provisioning_gatt.c", "")
    assert "init_recovery_ble_provisioning()" in ble_header
    assert "render_recovery_ble_status_json()" in ble_header
    assert '"ble.provision_wifi"' in ble_source
    assert '"hardware.bluetooth.ble.provision_wifi"' in ble_source
    assert '"hexe.voice_node.wifi_backend.v1"' in ble_source
    assert '"local_recovery"' in ble_source
    assert '"core_governed_requires_endpoint_app"' in ble_source
    assert "hexe_ble_provisioning_gatt_init" in ble_source
    assert "hexe_ble_pairing_central_set_scanning(1)" in ble_source
    assert '"central_scanning"' in ble_source
    assert '"host_pairing"' in ble_source
    assert "hexe_ble_pairing_host_advert_seen" in ble_source
    assert "hexe_ble_pairing_offer_received" in ble_source
    assert "hexe_ble_pairing_identity_write_result" in ble_source
    assert '"device_id"' in ble_source
    assert '"board_profile"' in ble_source
    assert '"onboarding_session_id"' in ble_source
    assert '"identity_sent"' in ble_source
    identity_json = ble_source[
        ble_source.index("hexe_ble_provisioning_device_identity_json") : ble_source.index(
            "hexe_ble_provisioning_pairing_nonce_json"
        )
    ]
    pairing_nonce_json = ble_source[
        ble_source.index("hexe_ble_provisioning_pairing_nonce_json") : ble_source.index(
            "hexe_ble_provisioning_status_json"
        )
    ]
    assert '"pairing_nonce"' not in identity_json
    assert '"pairing_nonce"' in pairing_nonce_json
    assert "remember_host_pairing_offer" in ble_source
    assert "hexe_ble_provisioning_handle_encrypted_credentials" in ble_source
    assert "save_local_recovery_payload" in ble_source
    assert "set_nvs_string(handle, kWifiSsidKey" in ble_source
    assert "set_nvs_string(handle, kWifiPasswordKey" in ble_source
    assert "nvs_set_u8(handle, kProvisionedKey, 1)" in ble_source
    assert "ESP_LOG" not in ble_source[ble_source.index("save_local_recovery_payload") : ble_source.index("bool handle_local_recovery_write")]
    assert "recovery_ble_enabled()" in status_source


def test_recovery_provisioning_writes_endpoint_compatible_nvs_keys():
    control_source = RECOVERY_CONTROL.read_text()

    assert 'constexpr char kSettingsNamespace[] = "hexe_settings"' in control_source
    for key in (
        "endpoint_id",
        "display_name",
        "backend_host",
        "http_port",
        "ws_port",
        "use_tls",
        "wifi_ssid",
        "wifi_password",
        "provisioned",
    ):
        assert f'"{key}"' in control_source

    assert 'set_nvs_string(handle, root, "endpoint_id", kEndpointIdKey, true)' in control_source
    assert 'set_nvs_string(handle, root, "backend_host", kBackendHostKey, true)' in control_source
    assert 'set_nvs_string(handle, root, "wifi_ssid", kWifiSsidKey, true)' in control_source
    assert "nvs_set_u8(handle, kProvisionedKey, 1)" in control_source
    assert "valid_port" in control_source
    assert "wifi_ssid is required" in control_source


def test_recovery_signed_endpoint_install_streams_to_inactive_slot():
    control_source = RECOVERY_CONTROL.read_text()

    assert 'X-Hexe-Application-Type' in control_source
    assert 'std::strcmp(headers->application_type, "endpoint")' in control_source
    assert "X-Hexe-Board-Profile" in control_source
    assert "X-Hexe-Partition-Schema" in control_source
    assert "X-Hexe-Image-Sha256" in control_source
    assert "X-Hexe-Manifest-Signature" in control_source
    assert "canonical_install_payload" in control_source
    assert "calculate_install_hmac" in control_source
    assert "verify_install_signature" in control_source
    assert "hexe::config::kEndpointOtaManifestSigningKey" in control_source
    assert "esp_ota_get_next_update_partition" in control_source
    assert "esp_ota_begin(update_partition" in control_source
    assert "esp_ota_write" in control_source
    assert "mbedtls_md_update" in control_source
    assert "esp_ota_set_boot_partition(update_partition)" in control_source
    assert "esp_restart" not in control_source


def test_recovery_partition_and_reset_controls_are_selective():
    control_source = RECOVERY_CONTROL.read_text()

    assert "render_partitions_json()" in control_source
    assert "esp_partition_find(ESP_PARTITION_TYPE_ANY" in control_source
    assert "esp_ota_get_boot_partition" in control_source
    assert "esp_ota_get_running_partition" in control_source
    assert "partition_label is required" in control_source
    assert "Only endpoint OTA app partitions may be selected" in control_source
    assert "reset_provisioning" in control_source
    assert "reset_wifi" in control_source
    assert "reset_settings" in control_source
    assert "reset_calibration" in control_source
    assert "erase_key_if_requested(handle, kWifiSsidKey)" in control_source


def test_recovery_contract_covers_unusable_main_ota_slots():
    doc = RECOVERY_DOC.read_text()
    roadmap = PARTITION_ROADMAP.read_text()
    control_source = RECOVERY_CONTROL.read_text()
    status_source = RECOVERY_STATUS.read_text()

    assert "no_valid_main_slot" in doc
    assert "boot_failure_threshold" in doc
    assert "devices with two unusable OTA app slots" in roadmap
    assert "Recovery must work without Hexe Core and without an SD card." in doc
    assert "temporary Wi-Fi AP" in doc
    assert "POST" in doc and "/api/recovery/firmware/install" in doc
    assert "POST" in doc and "/api/recovery/boot/select" in doc

    for partition_csv in (S3_8M_RECOVERY_PARTITIONS, S3_16M_RECOVERY_PARTITIONS, P4_32M_PARTITIONS):
        source = partition_csv.read_text()
        assert "factory,    app,  factory" in source
        assert "factory,    app,  factory, ,         2M," in source
        assert "ota_0,      app,  ota_0" in source
        assert "ota_1,      app,  ota_1" in source

    assert "endpoint_runtime_linked\\\":false" in status_source
    assert "\\\"core_required\\\":false" in status_source
    assert "\\\"sd_required\\\":false" in status_source
    assert "recovery_temporary_ap_active()" in status_source
    assert "firmware_upload\\\":%s" in status_source
    assert "recovery_wifi_recovery_enabled()" in status_source
    assert "boot_select\\\":%s" in status_source
    assert "esp_ota_get_next_update_partition(nullptr)" in control_source
    assert "esp_ota_abort(ota_handle)" in control_source
    assert "Only endpoint OTA app partitions may be selected" in control_source
    assert "partition->subtype == ESP_PARTITION_SUBTYPE_APP_FACTORY" in control_source


def test_build_script_exports_recovery_metadata_without_endpoint_runtime():
    build_script = BUILD_SCRIPT.read_text()

    assert "runtime_component_for_app()" in build_script
    assert 'endpoint) echo "endpoint_runtime"' in build_script
    assert 'recovery) echo "recovery_runtime"' in build_script
    assert 'minimal) echo "recovery_runtime"' in build_script
    assert 'FIRMWARE_EXPORT_FLAVOR="minimal"' in build_script
    assert "minimal_project_version()" in build_script
    assert 'PROJECT_VERSION="$(minimal_project_version "${PROJECT_VERSION}")"' in build_script
    assert 'echo "${EXPORT_DIR:-${ROOT_DIR}/export-min-$1}"' in build_script
    assert 'echo "hexe_min_${1}.bin"' in build_script
    assert 'FIRMWARE_APPLICATION_TYPE="${FIRMWARE_APP}"' in build_script
    assert 'FIRMWARE_API_VERSION="$(firmware_api_version_for_app "${FIRMWARE_EXPORT_FLAVOR}")"' in build_script
    assert 'GENERATED_COMPONENT_NAME="$(runtime_component_for_app "${FIRMWARE_EXPORT_FLAVOR}")"' in build_script
    assert 'idf_env+=("IDF_COMPONENT_MANAGER=0")' in build_script
    assert 'push mode supports only HEXE_FIRMWARE_APP=endpoint.' in build_script
    assert 'hexe_${FIRMWARE_APP}_${1}.bin' in build_script
    assert "CONFIG_BT_NIMBLE_HOST_TASK_STACK_SIZE=8192" in build_script


def test_rebuild_script_can_select_minimal_factory_exports():
    build_script = BUILD_SCRIPT.read_text()
    rebuild_script = REBUILD_SCRIPT.read_text()
    recovery_readme = RECOVERY_README.read_text()
    firmware_readme = Path("firmware/README.md").read_text()
    doc = RECOVERY_DOC.read_text()

    assert 'min|minimal|factory)' in build_script
    assert "--include-minimal" in rebuild_script
    assert "--minimal-only" in rebuild_script
    assert 'run_build minimal "${profile}"' in rebuild_script
    assert "Minimal firmware profiles:" in rebuild_script
    assert "export-min-<board>" in doc
    assert "min-<build-version>" in doc
    assert "export-min-<board>" in recovery_readme
    assert "export-min-<profile>" in firmware_readme
    assert "min-<build-version>" in firmware_readme
