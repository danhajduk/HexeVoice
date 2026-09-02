#include "sdkconfig.h"

#if defined(CONFIG_BT_ENABLED) && defined(CONFIG_BT_NIMBLE_ENABLED) && defined(CONFIG_BT_NIMBLE_ROLE_PERIPHERAL) && \
    defined(CONFIG_BT_NIMBLE_GATT_SERVER)

#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "host/ble_gap.h"
#include "host/ble_gatt.h"
#include "host/ble_hs.h"
#include "host/ble_uuid.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

const char *hexe_ble_provisioning_device_identity_json(void);
const char *hexe_ble_provisioning_pairing_nonce_json(void);
const char *hexe_ble_provisioning_status_json(void);
const char *hexe_ble_provisioning_ack_error_json(void);
int hexe_ble_provisioning_handle_encrypted_credentials(const char *json, unsigned int length);

static const char *TAG = "hexe_ble_gatt";

static uint8_t own_addr_type;
static int advertising_requested;
static int connected;
static uint16_t advertising_refresh_sequence;
static uint8_t advertising_timestamp_data[8];
static TaskHandle_t advertising_refresh_task_handle;

static uint16_t device_identity_handle;
static uint16_t pairing_nonce_handle;
static uint16_t status_handle;
static uint16_t encrypted_credentials_handle;
static uint16_t ack_error_handle;

static const ble_uuid128_t service_uuid =
    BLE_UUID128_INIT(0x00, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);
static const ble_uuid128_t device_identity_uuid =
    BLE_UUID128_INIT(0x01, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);
static const ble_uuid128_t pairing_nonce_uuid =
    BLE_UUID128_INIT(0x02, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);
static const ble_uuid128_t status_uuid =
    BLE_UUID128_INIT(0x03, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);
static const ble_uuid128_t encrypted_credentials_uuid =
    BLE_UUID128_INIT(0x04, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);
static const ble_uuid128_t ack_error_uuid =
    BLE_UUID128_INIT(0x05, 0x00, 0x10, 0x7a, 0x0f, 0x7c, 0x46, 0x9a, 0x8b, 0x4d, 0x04, 0x5f, 0x00, 0x00, 0x9c, 0x7f);

enum hexe_ble_characteristic {
  HEXE_BLE_CHR_DEVICE_IDENTITY = 1,
  HEXE_BLE_CHR_PAIRING_NONCE = 2,
  HEXE_BLE_CHR_STATUS = 3,
  HEXE_BLE_CHR_ENCRYPTED_CREDENTIALS = 4,
  HEXE_BLE_CHR_ACK_ERROR = 5,
};

static int append_json(struct os_mbuf *om, const char *json) {
  int rc = os_mbuf_append(om, json, strlen(json));
  return rc == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

static int access_cb(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt, void *arg) {
  (void) conn_handle;
  (void) attr_handle;
  enum hexe_ble_characteristic characteristic = (enum hexe_ble_characteristic)(uintptr_t)arg;
  if (ctxt->op == BLE_GATT_ACCESS_OP_READ_CHR) {
    switch (characteristic) {
      case HEXE_BLE_CHR_DEVICE_IDENTITY:
        return append_json(ctxt->om, hexe_ble_provisioning_device_identity_json());
      case HEXE_BLE_CHR_PAIRING_NONCE:
        return append_json(ctxt->om, hexe_ble_provisioning_pairing_nonce_json());
      case HEXE_BLE_CHR_STATUS:
        return append_json(ctxt->om, hexe_ble_provisioning_status_json());
      case HEXE_BLE_CHR_ACK_ERROR:
        return append_json(ctxt->om, hexe_ble_provisioning_ack_error_json());
      default:
        return BLE_ATT_ERR_READ_NOT_PERMITTED;
    }
  }
  if (ctxt->op == BLE_GATT_ACCESS_OP_WRITE_CHR && characteristic == HEXE_BLE_CHR_ENCRYPTED_CREDENTIALS) {
    uint16_t length = OS_MBUF_PKTLEN(ctxt->om);
    char buffer[4097];
    if (length == 0 || length >= sizeof(buffer)) {
      return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }
    int rc = ble_hs_mbuf_to_flat(ctxt->om, buffer, sizeof(buffer) - 1, &length);
    if (rc != 0) {
      return BLE_ATT_ERR_UNLIKELY;
    }
    buffer[length] = '\0';
    if (hexe_ble_provisioning_handle_encrypted_credentials(buffer, length) == 0) {
      ble_gatts_chr_updated(status_handle);
      ble_gatts_chr_updated(ack_error_handle);
      return 0;
    }
    ble_gatts_chr_updated(status_handle);
    ble_gatts_chr_updated(ack_error_handle);
    return BLE_ATT_ERR_UNLIKELY;
  }
  return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
}

static const struct ble_gatt_chr_def characteristics[] = {
    {&device_identity_uuid.u, access_cb, (void *)(uintptr_t)HEXE_BLE_CHR_DEVICE_IDENTITY, NULL, BLE_GATT_CHR_F_READ, 0, &device_identity_handle, NULL},
    {&pairing_nonce_uuid.u, access_cb, (void *)(uintptr_t)HEXE_BLE_CHR_PAIRING_NONCE, NULL, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY, 0, &pairing_nonce_handle, NULL},
    {&status_uuid.u, access_cb, (void *)(uintptr_t)HEXE_BLE_CHR_STATUS, NULL, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY, 0, &status_handle, NULL},
    {&encrypted_credentials_uuid.u, access_cb, (void *)(uintptr_t)HEXE_BLE_CHR_ENCRYPTED_CREDENTIALS, NULL, BLE_GATT_CHR_F_WRITE, 0, &encrypted_credentials_handle, NULL},
    {&ack_error_uuid.u, access_cb, (void *)(uintptr_t)HEXE_BLE_CHR_ACK_ERROR, NULL, BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY, 0, &ack_error_handle, NULL},
    {0},
};

static const struct ble_gatt_svc_def services[] = {
    {BLE_GATT_SVC_TYPE_PRIMARY, &service_uuid.u, NULL, characteristics},
    {0},
};

static int gap_event(struct ble_gap_event *event, void *arg);

static void write_le16(uint8_t *target, uint16_t value) {
  target[0] = (uint8_t)(value & 0xff);
  target[1] = (uint8_t)((value >> 8) & 0xff);
}

static void write_le32(uint8_t *target, uint32_t value) {
  target[0] = (uint8_t)(value & 0xff);
  target[1] = (uint8_t)((value >> 8) & 0xff);
  target[2] = (uint8_t)((value >> 16) & 0xff);
  target[3] = (uint8_t)((value >> 24) & 0xff);
}

static void update_advertising_timestamp_data(void) {
  uint32_t uptime_s = (uint32_t)(esp_timer_get_time() / 1000000LL);
  uint16_t sequence = ++advertising_refresh_sequence;
  write_le16(&advertising_timestamp_data[0], 0xffff);
  write_le32(&advertising_timestamp_data[2], uptime_s);
  write_le16(&advertising_timestamp_data[6], sequence);
}

static int set_scan_response_fields(void) {
  update_advertising_timestamp_data();

  struct ble_hs_adv_fields rsp_fields;
  memset(&rsp_fields, 0, sizeof(rsp_fields));
  rsp_fields.name = (uint8_t *)ble_svc_gap_device_name();
  rsp_fields.name_len = strlen((const char *)rsp_fields.name);
  rsp_fields.name_is_complete = 1;
  rsp_fields.mfg_data = advertising_timestamp_data;
  rsp_fields.mfg_data_len = sizeof(advertising_timestamp_data);
  return ble_gap_adv_rsp_set_fields(&rsp_fields);
}

static void advertise(void) {
  if (!advertising_requested || connected || ble_gap_adv_active()) {
    return;
  }
  struct ble_hs_adv_fields fields;
  memset(&fields, 0, sizeof(fields));
  fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
  fields.uuids128 = &service_uuid;
  fields.num_uuids128 = 1;
  fields.uuids128_is_complete = 1;
  int rc = ble_gap_adv_set_fields(&fields);
  if (rc != 0) {
    ESP_LOGW(TAG, "BLE onboarding advertising fields failed: %d", rc);
    return;
  }

  rc = set_scan_response_fields();
  if (rc != 0) {
    ESP_LOGW(TAG, "BLE onboarding scan response fields failed: %d", rc);
    return;
  }

  struct ble_gap_adv_params adv_params;
  memset(&adv_params, 0, sizeof(adv_params));
  adv_params.conn_mode = BLE_GAP_CONN_MODE_UND;
  adv_params.disc_mode = BLE_GAP_DISC_MODE_GEN;
  rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &adv_params, gap_event, NULL);
  if (rc != 0) {
    ESP_LOGW(TAG, "BLE onboarding advertising start failed: %d", rc);
  }
}

static int gap_event(struct ble_gap_event *event, void *arg) {
  (void)arg;
  switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
      if (event->connect.status != 0) {
        advertise();
      } else {
        connected = 1;
      }
      return 0;
    case BLE_GAP_EVENT_DISCONNECT:
      connected = 0;
      advertise();
      return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
      advertise();
      return 0;
    default:
      return 0;
  }
}

static void advertising_refresh_task(void *param) {
  (void)param;
  while (1) {
    vTaskDelay(pdMS_TO_TICKS(5000));
    if (!advertising_requested || connected) {
      continue;
    }
    if (!ble_gap_adv_active()) {
      advertise();
    }
  }
}

static void on_sync(void) {
  int rc = ble_hs_id_infer_auto(0, &own_addr_type);
  if (rc != 0) {
    ESP_LOGW(TAG, "BLE onboarding address selection failed: %d", rc);
    return;
  }
  advertise();
}

static void host_task(void *param) {
  (void)param;
  nimble_port_run();
  nimble_port_freertos_deinit();
}

int hexe_ble_provisioning_gatt_init(const char *device_name) {
  int rc = nimble_port_init();
  if (rc != 0) {
    return rc;
  }
  ble_svc_gap_init();
  ble_svc_gatt_init();
  ble_hs_cfg.sync_cb = on_sync;
  rc = ble_gatts_count_cfg(services);
  if (rc != 0) {
    return rc;
  }
  rc = ble_gatts_add_svcs(services);
  if (rc != 0) {
    return rc;
  }
  if (device_name != NULL && device_name[0] != '\0') {
    rc = ble_svc_gap_device_name_set(device_name);
    if (rc != 0) {
      return rc;
    }
  }
  nimble_port_freertos_init(host_task);
  if (advertising_refresh_task_handle == NULL) {
    xTaskCreate(advertising_refresh_task, "ble_adv_refresh", 3072, NULL, 5, &advertising_refresh_task_handle);
  }
  return 0;
}

int hexe_ble_provisioning_gatt_set_advertising(int enabled) {
  advertising_requested = enabled ? 1 : 0;
  if (!advertising_requested && ble_gap_adv_active()) {
    return ble_gap_adv_stop();
  }
  if (advertising_requested) {
    advertise();
  }
  return 0;
}

#else

int hexe_ble_provisioning_gatt_init(const char *device_name) {
  (void)device_name;
  return -1;
}

int hexe_ble_provisioning_gatt_set_advertising(int enabled) {
  (void)enabled;
  return -1;
}

#endif
