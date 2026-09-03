#include "sdkconfig.h"

#if defined(CONFIG_BT_ENABLED) && defined(CONFIG_BT_NIMBLE_ENABLED) && defined(CONFIG_BT_NIMBLE_ROLE_PERIPHERAL) && \
    defined(CONFIG_BT_NIMBLE_GATT_SERVER) && defined(CONFIG_BT_NIMBLE_ROLE_CENTRAL) && defined(CONFIG_BT_NIMBLE_GATT_CLIENT)

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
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
void hexe_ble_pairing_scan_state_changed(int scanning, const char *reason, int rc);
void hexe_ble_pairing_host_advert_seen(const char *address, int rssi, const char *name, int host_pairing_role_match);
void hexe_ble_pairing_connection_state_changed(int connected, const char *reason, int rc);
int hexe_ble_pairing_offer_received(const char *json, unsigned int length);
void hexe_ble_pairing_identity_write_result(int succeeded, const char *reason, int rc);

static const char *TAG = "hexe_ble_gatt";
enum {
  kPairingConnectTimeoutMs = 30000,
  kMaxPairingOfferBytes = 1024,
};

static uint8_t own_addr_type;
static int advertising_requested;
static int pairing_scan_requested;
static int pairing_scan_active;
static int connected;
static int client_connecting;
static int client_connected;
static int client_offer_received;
static int client_identity_sent;
static uint16_t client_conn_handle;
static uint16_t client_service_start_handle;
static uint16_t client_service_end_handle;
static uint16_t client_device_identity_handle;
static uint16_t client_pairing_nonce_handle;
static ble_addr_t client_peer_addr;
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
static const uint8_t host_pairing_role_marker[] = {0xff, 0xff, 'H', 'X', 'P', 'A', 0x01};

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
static int start_pairing_scan(void);

static void reset_client_handles(void) {
  client_service_start_handle = 0;
  client_service_end_handle = 0;
  client_device_identity_handle = 0;
  client_pairing_nonce_handle = 0;
}

static void reset_client_state(int clear_exchange_state) {
  client_connecting = 0;
  client_connected = 0;
  client_conn_handle = 0;
  reset_client_handles();
  if (clear_exchange_state) {
    client_offer_received = 0;
    client_identity_sent = 0;
  }
}

static void format_addr(const ble_addr_t *addr, char out[18]) {
  if (addr == NULL || out == NULL) {
    return;
  }
  snprintf(
      out,
      18,
      "%02X:%02X:%02X:%02X:%02X:%02X",
      addr->val[5],
      addr->val[4],
      addr->val[3],
      addr->val[2],
      addr->val[1],
      addr->val[0]);
}

static int uuid128_matches_service(const ble_uuid128_t *uuid) {
  return uuid != NULL && ble_uuid_cmp(&uuid->u, &service_uuid.u) == 0;
}

static int fields_include_service_uuid(const struct ble_hs_adv_fields *fields) {
  if (fields == NULL) {
    return 0;
  }
  for (int i = 0; i < fields->num_uuids128; ++i) {
    if (uuid128_matches_service(&fields->uuids128[i])) {
      return 1;
    }
  }
  return 0;
}

static int fields_include_host_pairing_role(const struct ble_hs_adv_fields *fields) {
  if (fields == NULL || fields->mfg_data == NULL || fields->mfg_data_len < sizeof(host_pairing_role_marker)) {
    return 0;
  }
  for (uint8_t offset = 0; offset <= fields->mfg_data_len - sizeof(host_pairing_role_marker); ++offset) {
    if (memcmp(fields->mfg_data + offset, host_pairing_role_marker, sizeof(host_pairing_role_marker)) == 0) {
      return 1;
    }
  }
  return 0;
}

static int start_pairing_connect(const ble_addr_t *addr);
static int pairing_service_discovered_cb(uint16_t conn_handle, const struct ble_gatt_error *error, const struct ble_gatt_svc *svc, void *arg);
static int pairing_chr_discovered_cb(uint16_t conn_handle, const struct ble_gatt_error *error, const struct ble_gatt_chr *chr, void *arg);
static int pairing_offer_read_cb(uint16_t conn_handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg);
static int pairing_identity_write_cb(uint16_t conn_handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg);

static void copy_adv_name(const struct ble_hs_adv_fields *fields, char out[40]) {
  if (out == NULL) {
    return;
  }
  out[0] = '\0';
  if (fields == NULL || fields->name == NULL || fields->name_len == 0) {
    return;
  }
  const uint8_t copy_len = fields->name_len < 39 ? fields->name_len : 39;
  memcpy(out, fields->name, copy_len);
  out[copy_len] = '\0';
}

static void handle_pairing_scan_result(const struct ble_gap_disc_desc *disc) {
  if (disc == NULL) {
    return;
  }
  struct ble_hs_adv_fields fields;
  memset(&fields, 0, sizeof(fields));
  if (ble_hs_adv_parse_fields(&fields, disc->data, disc->length_data) != 0) {
    return;
  }
  if (!fields_include_service_uuid(&fields)) {
    return;
  }
  char address[18] = "";
  char name[40] = "";
  format_addr(&disc->addr, address);
  copy_adv_name(&fields, name);
  hexe_ble_pairing_host_advert_seen(address, disc->rssi, name, fields_include_host_pairing_role(&fields));
  start_pairing_connect(&disc->addr);
}

static int start_pairing_scan(void) {
  if (!pairing_scan_requested || pairing_scan_active || client_connecting || client_connected || ble_gap_disc_active()) {
    return 0;
  }
  struct ble_gap_disc_params params;
  memset(&params, 0, sizeof(params));
  params.passive = 0;
  params.itvl = 0x0010;
  params.window = 0x0010;
  params.filter_duplicates = 1;
  int rc = ble_gap_disc(own_addr_type, BLE_HS_FOREVER, &params, gap_event, NULL);
  if (rc == 0) {
    pairing_scan_active = 1;
    hexe_ble_pairing_scan_state_changed(1, "scanning", 0);
  } else {
    hexe_ble_pairing_scan_state_changed(0, "ble_pairing_scan_start_failed", rc);
  }
  return rc;
}

static int stop_pairing_scan(void) {
  if (ble_gap_disc_active()) {
    int rc = ble_gap_disc_cancel();
    if (rc != 0) {
      hexe_ble_pairing_scan_state_changed(pairing_scan_active, "ble_pairing_scan_stop_failed", rc);
      return rc;
    }
  }
  pairing_scan_active = 0;
  hexe_ble_pairing_scan_state_changed(0, "stopped", 0);
  return 0;
}

static int start_pairing_connect(const ble_addr_t *addr) {
  if (addr == NULL || client_connecting || client_connected || connected) {
    return 0;
  }
  if (ble_gap_disc_active()) {
    int cancel_rc = ble_gap_disc_cancel();
    if (cancel_rc != 0) {
      hexe_ble_pairing_connection_state_changed(0, "scan_cancel_failed", cancel_rc);
      return cancel_rc;
    }
  }
  pairing_scan_active = 0;
  hexe_ble_pairing_scan_state_changed(0, "connecting", 0);
  memcpy(&client_peer_addr, addr, sizeof(client_peer_addr));
  reset_client_state(1);
  client_connecting = 1;
  int rc = ble_gap_connect(own_addr_type, &client_peer_addr, kPairingConnectTimeoutMs, NULL, gap_event, NULL);
  if (rc != 0) {
    client_connecting = 0;
    hexe_ble_pairing_connection_state_changed(0, "connect_start_failed", rc);
    if (pairing_scan_requested) {
      start_pairing_scan();
    }
    return rc;
  }
  hexe_ble_pairing_connection_state_changed(0, "connecting", 0);
  return 0;
}

static int discover_pairing_service(uint16_t conn_handle) {
  int rc = ble_gattc_disc_svc_by_uuid(conn_handle, &service_uuid.u, pairing_service_discovered_cb, NULL);
  if (rc != 0) {
    hexe_ble_pairing_connection_state_changed(0, "service_discovery_start_failed", rc);
    ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  return rc;
}

static int read_pairing_offer(uint16_t conn_handle) {
  if (client_pairing_nonce_handle == 0) {
    hexe_ble_pairing_connection_state_changed(0, "pairing_offer_missing", BLE_ATT_ERR_ATTR_NOT_FOUND);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  int rc = ble_gattc_read(conn_handle, client_pairing_nonce_handle, pairing_offer_read_cb, NULL);
  if (rc != 0) {
    hexe_ble_pairing_connection_state_changed(0, "pairing_offer_read_start_failed", rc);
    ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  return rc;
}

static int write_pairing_identity(uint16_t conn_handle) {
  if (client_device_identity_handle == 0) {
    hexe_ble_pairing_identity_write_result(0, "device_identity_missing", BLE_ATT_ERR_ATTR_NOT_FOUND);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  const char *identity = hexe_ble_provisioning_device_identity_json();
  if (identity == NULL || identity[0] == '\0') {
    hexe_ble_pairing_identity_write_result(0, "device_identity_empty", BLE_ATT_ERR_UNLIKELY);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  size_t identity_len = strlen(identity);
  if (identity_len > UINT16_MAX) {
    hexe_ble_pairing_identity_write_result(0, "device_identity_too_large", BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  int rc = ble_gattc_write_flat(conn_handle, client_device_identity_handle, identity, (uint16_t)identity_len, pairing_identity_write_cb, NULL);
  if (rc != 0) {
    hexe_ble_pairing_identity_write_result(0, "device_identity_write_start_failed", rc);
    ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  return rc;
}

static int pairing_service_discovered_cb(uint16_t conn_handle, const struct ble_gatt_error *error, const struct ble_gatt_svc *svc, void *arg) {
  (void)arg;
  if (error == NULL) {
    return BLE_ATT_ERR_UNLIKELY;
  }
  if (error->status == 0 && svc != NULL) {
    client_service_start_handle = svc->start_handle;
    client_service_end_handle = svc->end_handle;
    int rc = ble_gattc_disc_all_chrs(conn_handle, svc->start_handle, svc->end_handle, pairing_chr_discovered_cb, NULL);
    if (rc != 0) {
      hexe_ble_pairing_connection_state_changed(0, "characteristic_discovery_start_failed", rc);
      ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    }
    return 0;
  }
  if (error->status == BLE_HS_EDONE) {
    if (client_service_start_handle == 0) {
      hexe_ble_pairing_connection_state_changed(0, "service_not_found", BLE_ATT_ERR_ATTR_NOT_FOUND);
      ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    }
    return 0;
  }
  hexe_ble_pairing_connection_state_changed(0, "service_discovery_failed", error->status);
  ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  return 0;
}

static int pairing_chr_discovered_cb(uint16_t conn_handle, const struct ble_gatt_error *error, const struct ble_gatt_chr *chr, void *arg) {
  (void)arg;
  if (error == NULL) {
    return BLE_ATT_ERR_UNLIKELY;
  }
  if (error->status == 0 && chr != NULL) {
    if (ble_uuid_cmp(&chr->uuid.u, &device_identity_uuid.u) == 0) {
      client_device_identity_handle = chr->val_handle;
    } else if (ble_uuid_cmp(&chr->uuid.u, &pairing_nonce_uuid.u) == 0) {
      client_pairing_nonce_handle = chr->val_handle;
    }
    return 0;
  }
  if (error->status == BLE_HS_EDONE) {
    if (client_device_identity_handle == 0 || client_pairing_nonce_handle == 0) {
      hexe_ble_pairing_connection_state_changed(0, "required_characteristics_missing", BLE_ATT_ERR_ATTR_NOT_FOUND);
      ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
      return 0;
    }
    read_pairing_offer(conn_handle);
    return 0;
  }
  hexe_ble_pairing_connection_state_changed(0, "characteristic_discovery_failed", error->status);
  ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  return 0;
}

static int pairing_offer_read_cb(uint16_t conn_handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg) {
  (void)arg;
  if (error == NULL) {
    return BLE_ATT_ERR_UNLIKELY;
  }
  if (error->status != 0 || attr == NULL || attr->om == NULL) {
    hexe_ble_pairing_connection_state_changed(0, "pairing_offer_read_failed", error->status);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  uint16_t length = OS_MBUF_PKTLEN(attr->om);
  char buffer[kMaxPairingOfferBytes + 1];
  if (length == 0 || length > kMaxPairingOfferBytes) {
    hexe_ble_pairing_connection_state_changed(0, "pairing_offer_invalid_length", BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  int rc = ble_hs_mbuf_to_flat(attr->om, buffer, kMaxPairingOfferBytes, &length);
  if (rc != 0) {
    hexe_ble_pairing_connection_state_changed(0, "pairing_offer_flatten_failed", rc);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  buffer[length] = '\0';
  client_offer_received = 1;
  if (hexe_ble_pairing_offer_received(buffer, length) != 0) {
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  return write_pairing_identity(conn_handle);
}

static int pairing_identity_write_cb(uint16_t conn_handle, const struct ble_gatt_error *error, struct ble_gatt_attr *attr, void *arg) {
  (void)attr;
  (void)arg;
  if (error == NULL) {
    return BLE_ATT_ERR_UNLIKELY;
  }
  if (error->status != 0) {
    client_identity_sent = 0;
    hexe_ble_pairing_identity_write_result(0, "device_identity_write_failed", error->status);
    return ble_gap_terminate(conn_handle, BLE_ERR_REM_USER_CONN_TERM);
  }
  client_identity_sent = 1;
  hexe_ble_pairing_identity_write_result(1, "endpoint_identity_written", 0);
  return 0;
}

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
  adv_params.channel_map = BLE_GAP_ADV_DFLT_CHANNEL_MAP;
  rc = ble_gap_adv_start(own_addr_type, NULL, BLE_HS_FOREVER, &adv_params, gap_event, NULL);
  if (rc != 0) {
    ESP_LOGW(TAG, "BLE onboarding advertising start failed: %d", rc);
  }
}

static int gap_event(struct ble_gap_event *event, void *arg) {
  (void)arg;
  switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
      if (client_connecting) {
        client_connecting = 0;
        if (event->connect.status != 0) {
          reset_client_state(1);
          hexe_ble_pairing_connection_state_changed(0, "connect_failed", event->connect.status);
          if (pairing_scan_requested) {
            start_pairing_scan();
          }
          return 0;
        }
        client_connected = 1;
        client_conn_handle = event->connect.conn_handle;
        hexe_ble_pairing_connection_state_changed(1, "connected", 0);
        discover_pairing_service(event->connect.conn_handle);
        return 0;
      }
      if (event->connect.status != 0) {
        advertise();
      } else {
        connected = 1;
      }
      return 0;
    case BLE_GAP_EVENT_DISCONNECT:
      if (client_connected && event->disconnect.conn.conn_handle == client_conn_handle) {
        reset_client_state(client_identity_sent ? 0 : 1);
        hexe_ble_pairing_connection_state_changed(0, "disconnected", event->disconnect.reason);
        advertise();
        if (pairing_scan_requested && !client_identity_sent) {
          start_pairing_scan();
        }
        return 0;
      }
      connected = 0;
      advertise();
      return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
      advertise();
      return 0;
    case BLE_GAP_EVENT_DISC:
      handle_pairing_scan_result(&event->disc);
      return 0;
    case BLE_GAP_EVENT_DISC_COMPLETE:
      pairing_scan_active = 0;
      hexe_ble_pairing_scan_state_changed(0, "scan_complete", event->disc_complete.reason);
      if (pairing_scan_requested && !client_connecting && !client_connected) {
        start_pairing_scan();
      }
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
  start_pairing_scan();
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

int hexe_ble_pairing_central_set_scanning(int enabled) {
  pairing_scan_requested = enabled ? 1 : 0;
  if (!pairing_scan_requested) {
    return stop_pairing_scan();
  }
  return start_pairing_scan();
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

int hexe_ble_pairing_central_set_scanning(int enabled) {
  (void)enabled;
  return -1;
}

#endif
