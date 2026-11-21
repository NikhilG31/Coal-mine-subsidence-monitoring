#include <stdio.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>

#include "esp_wifi.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_now.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_event.h"
#include "esp_http_client.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

static const char *TAG = "espnow_gateway";

#define MAX_NODES               10
#define WIFI_CHANNEL            7
#define RX_QUEUE_LENGTH         20
#define NVS_NAMESPACE           "node_reg"
#define NVS_REGISTRY_KEY        "registry"

/*
 * A node sends each SOS press several times (burst + backup in the next
 * regular packet). Repeats from the same node within this window are treated
 * as the same incident and are not flagged to the server again.
 */
#define SOS_DEDUPE_MS           10000

/* Wi-Fi + server settings. Fill these in; keep real credentials out of git. */
#define WIFI_SSID               "POCO M4 PRO 5g"
#define WIFI_PASS               "heyboiii"
#define SERVER_URL              "http://10.228.21.27:8000/api/ingest"
#define GATEWAY_ID              "gw0"
#define SERVER_TIMEOUT_MS       2000
/* #define SERVER_API_KEY       "secret" */   /* uncomment if the server sets API_KEY */

/*
 * ESP-NOW input format.
 * This must match the sender's transmitted structure exactly,
 * including field order, types, and packing.
 */
typedef struct __attribute__((packed)) {
    uint8_t  mac_address[6];
    uint32_t counter;
    float    tiltX;
    float    tiltY;
    float    vibration_magnitude;
    bool     vibration_detected;
    float    battery_voltage;
    float    battery_percentage;
    bool     sos_active;            /* NEW: SOS button pressed on the node */
    uint8_t  danger_level;          /* NEW: 0 normal, 1 watch, 2 warning, 3 critical */
    bool     fast_mode;             /* NEW: node is reporting every 2 s */
    bool     buzzer_on;             /* NEW: node buzzer is sounding */
    uint16_t last_cmd_seq;          /* NEW: last server command the node applied */
} espnow_rx_payload_t;

/* Sizes of the older sender formats; still accepted (missing fields read as 0). */
#define RX_PAYLOAD_LEGACY_SIZE     offsetof(espnow_rx_payload_t, sos_active)
#define RX_PAYLOAD_NO_STATUS_SIZE  offsetof(espnow_rx_payload_t, danger_level)

/*
 * Gateway-to-server dataset.
 * This is the format to use when the gateway forwards data to the server.
 */
typedef struct __attribute__((packed)) {
    uint8_t  gateway_mac_address[6];
    uint8_t  node_id;
    uint32_t counter;
    float    tiltX;
    float    tiltY;
    float    vibration_magnitude;
    bool     vibration_detected;
    float    battery_voltage;
    float    battery_percentage;
    bool     sos_active;            /* NEW: appended at the end */
    uint8_t  danger_level;          /* NEW: 0 normal, 1 watch, 2 warning, 3 critical */
    bool     fast_mode;             /* NEW */
    bool     buzzer_on;             /* NEW */
    uint16_t last_cmd_seq;          /* NEW: little-endian */
} payload_t;

/*
 * Gateway MAC (6) + node ID (1) + counter (4) + 5 floats (20) + 2 bools (2)
 * + danger_level (1) + fast_mode (1) + buzzer_on (1) + last_cmd_seq (2)
 * = 38 bytes. This was 33 before the status fields; the server's ingest
 * parser must accept the 5 extra trailing bytes.
 */
_Static_assert(sizeof(payload_t) == 38, "payload_t must be 38 bytes");

/* Gateway -> node command. Must exactly match the node's command_t. */
#define CMD_MAGIC  0xA5

enum {
    CMD_BUZZER_ON     = 1,
    CMD_BUZZER_OFF    = 2,
    CMD_RESUME_NORMAL = 3,
};

typedef struct __attribute__((packed)) {
    uint8_t  magic;          /* CMD_MAGIC */
    uint8_t  cmd;
    uint8_t  target_mac[6];  /* the node's own MAC */
    uint16_t seq;            /* server-assigned, starts at 1 */
} command_t;

#define CMD_SEND_GAP_MS      20     /* pause between consecutive ESP-NOW sends */
#define SERVER_RESP_BUF_SIZE 256    /* max server reply body kept for commands */

typedef struct {
    uint8_t mac[6];
    uint8_t node_id;
    bool active;
} registered_node_t;

/* Stored in NVS so node IDs remain the same after a gateway reboot. */
typedef struct {
    uint16_t next_node_id;
    registered_node_t nodes[MAX_NODES];
} node_registry_t;

/* One queue item is produced for every received ESP-NOW packet. */
typedef struct {
    uint8_t source_mac[6];
    espnow_rx_payload_t data;
} received_packet_t;

static node_registry_t node_registry;
static nvs_handle_t registry_nvs;
static QueueHandle_t rx_queue;

/* Per-node SOS dedupe state, indexed by node ID (1-255). */
static TickType_t last_sos_tick[256];
static bool       sos_seen[256];

/* Set by the Wi-Fi event handler, read by publish_to_server(). */
static volatile bool wifi_connected = false;

static void wifi_event_handler(void *arg, esp_event_base_t base,
                               int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_connected = false;
        ESP_LOGW(TAG, "Wi-Fi disconnected; reconnecting");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        wifi_connected = true;
        ESP_LOGI(TAG, "Wi-Fi connected. IP: " IPSTR, IP2STR(&ev->ip_info.ip));

        /*
         * Once connected, the radio follows the router's channel. ESP-NOW nodes
         * only reach this gateway if they use the same channel.
         */
        uint8_t ch;
        wifi_second_chan_t second;
        if (esp_wifi_get_channel(&ch, &second) == ESP_OK && ch != WIFI_CHANNEL) {
            ESP_LOGW(TAG, "Router is on channel %u but nodes use channel %d: "
                     "ESP-NOW packets will be missed", ch, WIFI_CHANNEL);
        }
    }
}

/* -------------------------------------------------------------------------- */
/* Node registry                                                              */
/* -------------------------------------------------------------------------- */

static void load_node_registry(void)
{
    size_t required_size = sizeof(node_registry);
    esp_err_t err = nvs_get_blob(registry_nvs, NVS_REGISTRY_KEY,
                                 &node_registry, &required_size);

    if (err == ESP_OK && required_size == sizeof(node_registry)) {
        if (node_registry.next_node_id == 0 ||
            node_registry.next_node_id > 256) {
            node_registry.next_node_id = 1;
        }

        ESP_LOGI(TAG, "Loaded node registry from NVS. Next node ID: %u",
                 (unsigned int)node_registry.next_node_id);

        for (int i = 0; i < MAX_NODES; i++) {
            if (node_registry.nodes[i].active) {
                const uint8_t *m = node_registry.nodes[i].mac;
                ESP_LOGI(TAG, "Registered NODE %u: %02X:%02X:%02X:%02X:%02X:%02X",
                         node_registry.nodes[i].node_id,
                         m[0], m[1], m[2], m[3], m[4], m[5]);
            }
        }
    } else {
        memset(&node_registry, 0, sizeof(node_registry));
        node_registry.next_node_id = 1;

        if (err != ESP_ERR_NVS_NOT_FOUND) {
            ESP_LOGW(TAG, "Node registry unavailable (%s); creating a new registry",
                     esp_err_to_name(err));
        } else {
            ESP_LOGI(TAG, "No saved node registry. Starting a new registry.");
        }

        ESP_ERROR_CHECK(nvs_set_blob(registry_nvs, NVS_REGISTRY_KEY,
                                     &node_registry, sizeof(node_registry)));
        ESP_ERROR_CHECK(nvs_commit(registry_nvs));
    }
}

static int get_or_register_node(const uint8_t *mac)
{
    /* Existing MAC: always reuse its original node ID. */
    for (int i = 0; i < MAX_NODES; i++) {
        if (node_registry.nodes[i].active &&
            memcmp(node_registry.nodes[i].mac, mac, 6) == 0) {
            return node_registry.nodes[i].node_id;
        }
    }

    /* New MAC: allocate the next unused table slot and ID. */
    for (int i = 0; i < MAX_NODES; i++) {
        if (!node_registry.nodes[i].active) {
            if (node_registry.next_node_id > 255) {
                ESP_LOGE(TAG, "Node ID range exhausted (uint8_t supports 1-255)");
                return -1;
            }

            registered_node_t *node = &node_registry.nodes[i];
            memcpy(node->mac, mac, 6);
            node->node_id = (uint8_t)node_registry.next_node_id++;
            node->active = true;

            esp_err_t err = nvs_set_blob(registry_nvs, NVS_REGISTRY_KEY,
                                         &node_registry, sizeof(node_registry));
            if (err == ESP_OK) {
                err = nvs_commit(registry_nvs);
            }

            if (err != ESP_OK) {
                ESP_LOGE(TAG, "Could not save node registry: %s",
                         esp_err_to_name(err));
                node->active = false;
                node_registry.next_node_id--;
                return -1;
            }

            ESP_LOGI(TAG,
                     "NEW NODE: assigned NODE %u to MAC %02X:%02X:%02X:%02X:%02X:%02X",
                     node->node_id,
                     mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

            return node->node_id;
        }
    }

    ESP_LOGE(TAG, "Node table full. Maximum supported nodes: %d", MAX_NODES);
    return -1;
}

/* -------------------------------------------------------------------------- */
/* ESP-NOW receive callback                                                   */
/* -------------------------------------------------------------------------- */

static void on_recv(const esp_now_recv_info_t *info,
                    const uint8_t *data,
                    int len)
{
    if (info == NULL || info->src_addr == NULL || data == NULL) {
        return;
    }

    if (len < 0 ||
        ((size_t)len != sizeof(espnow_rx_payload_t) &&
         (size_t)len != RX_PAYLOAD_NO_STATUS_SIZE &&
         (size_t)len != RX_PAYLOAD_LEGACY_SIZE)) {
        ESP_LOGW(TAG, "Invalid packet size: received %d bytes, expected %u (or older %u / %u)",
                 len, (unsigned int)sizeof(espnow_rx_payload_t),
                 (unsigned int)RX_PAYLOAD_NO_STATUS_SIZE,
                 (unsigned int)RX_PAYLOAD_LEGACY_SIZE);
        return;
    }

    received_packet_t packet;
    memset(&packet, 0, sizeof(packet));          /* legacy packets: sos_active = false */
    memcpy(packet.source_mac, info->src_addr, 6);
    memcpy(&packet.data, data, (size_t)len);

    /*
     * Use the actual ESP-NOW source MAC for node identification.
     * The MAC inside the payload is also checked to catch mismatched packets.
     */
    if (memcmp(packet.source_mac, packet.data.mac_address, 6) != 0) {
        ESP_LOGW(TAG, "Source MAC does not match payload MAC; packet ignored");
        return;
    }

    if (xQueueSend(rx_queue, &packet, 0) != pdTRUE) {
        ESP_LOGW(TAG, "RX queue full; packet dropped");
    }
}

/* -------------------------------------------------------------------------- */
/* Gateway -> node commands (sent only when the server asks for them)        */
/* -------------------------------------------------------------------------- */

/*
 * The server puts commands in the body of its reply to the ingest POST, one
 * per line, and repeats a command until the node acknowledges it (the node
 * echoes the command's seq in last_cmd_seq):
 *
 *     CMD,<node_id|ALL>,<BUZZER_ON|BUZZER_OFF|RESUME>,<seq>
 *
 * An empty body means nothing is sent to any node.
 */

static char   server_resp[SERVER_RESP_BUF_SIZE];
static size_t server_resp_len;

static esp_err_t http_event_handler(esp_http_client_event_t *evt)
{
    if (evt->event_id == HTTP_EVENT_ON_DATA && evt->data != NULL && evt->data_len > 0) {
        size_t room = sizeof(server_resp) - 1 - server_resp_len;
        size_t n = ((size_t)evt->data_len < room) ? (size_t)evt->data_len : room;

        memcpy(server_resp + server_resp_len, evt->data, n);
        server_resp_len += n;
        server_resp[server_resp_len] = '\0';
    }
    return ESP_OK;
}

static void on_cmd_sent(const esp_now_send_info_t *tx_info, esp_now_send_status_t status)
{
    (void)tx_info;
    if (status != ESP_NOW_SEND_SUCCESS) {
        ESP_LOGW(TAG, "Command delivery failed (no ACK from node)");
    }
}

static void send_command_to_node(const registered_node_t *node, uint8_t cmd, uint16_t seq)
{
    if (!esp_now_is_peer_exist(node->mac)) {
        esp_now_peer_info_t peer = {0};

        memcpy(peer.peer_addr, node->mac, 6);
        peer.channel = 0;               /* use the gateway's current channel */
        peer.ifidx = WIFI_IF_STA;
        peer.encrypt = false;

        esp_err_t add_err = esp_now_add_peer(&peer);
        if (add_err != ESP_OK) {
            ESP_LOGE(TAG, "Cannot add node %u as peer: %s",
                     node->node_id, esp_err_to_name(add_err));
            return;
        }
    }

    command_t c = { .magic = CMD_MAGIC, .cmd = cmd, .seq = seq };
    memcpy(c.target_mac, node->mac, 6);

    esp_err_t err = esp_now_send(node->mac, (const uint8_t *)&c, sizeof(c));
    if (err == ESP_OK) {
        ESP_LOGW(TAG, "Command %u (seq %u) sent to node %u",
                 (unsigned)cmd, (unsigned)seq, node->node_id);
    } else {
        ESP_LOGE(TAG, "Command to node %u failed: %s",
                 node->node_id, esp_err_to_name(err));
    }
}

static void handle_server_commands(char *body)
{
    char *save = NULL;

    for (char *line = strtok_r(body, "\r\n", &save);
         line != NULL;
         line = strtok_r(NULL, "\r\n", &save)) {
        char target[8];
        char name[16];
        unsigned int seq = 0;

        if (sscanf(line, "CMD,%7[^,],%15[^,],%u", target, name, &seq) != 3 ||
            seq == 0 || seq > 0xFFFF) {
            ESP_LOGW(TAG, "Ignoring malformed server command: %s", line);
            continue;
        }

        uint8_t cmd;
        if (strcmp(name, "BUZZER_ON") == 0) {
            cmd = CMD_BUZZER_ON;
        } else if (strcmp(name, "BUZZER_OFF") == 0) {
            cmd = CMD_BUZZER_OFF;
        } else if (strcmp(name, "RESUME") == 0) {
            cmd = CMD_RESUME_NORMAL;
        } else {
            ESP_LOGW(TAG, "Unknown server command name: %s", name);
            continue;
        }

        if (strcmp(target, "ALL") == 0) {
            /* One unicast per registered node: gives MAC-level ACK and retries. */
            for (size_t i = 0; i < MAX_NODES; i++) {
                if (node_registry.nodes[i].active) {
                    send_command_to_node(&node_registry.nodes[i], cmd, (uint16_t)seq);
                    vTaskDelay(pdMS_TO_TICKS(CMD_SEND_GAP_MS));
                }
            }
        } else {
            char *end = NULL;
            unsigned long id = strtoul(target, &end, 10);
            const registered_node_t *found = NULL;

            if (end != target && *end == '\0') {
                for (size_t i = 0; i < MAX_NODES; i++) {
                    if (node_registry.nodes[i].active && node_registry.nodes[i].node_id == id) {
                        found = &node_registry.nodes[i];
                        break;
                    }
                }
            }
            if (found != NULL) {
                send_command_to_node(found, cmd, (uint16_t)seq);
                vTaskDelay(pdMS_TO_TICKS(CMD_SEND_GAP_MS));
            } else {
                ESP_LOGW(TAG, "Server command for unknown node '%s'", target);
            }
        }
    }
}

/* -------------------------------------------------------------------------- */
/* Gateway output / future server interface                                   */
/* -------------------------------------------------------------------------- */

/*
 * Output function.
 *
 * Prints one JSON record per line to the serial monitor, then POSTs the raw
 * payload_t bytes (38 bytes) to SERVER_URL over HTTP. Any commands in the
 * server's reply body are forwarded to the nodes (see handle_server_commands).
 * Returns true only if the server answered HTTP 200.
 */
static bool publish_to_server(const payload_t *out)
{
    ESP_LOGI(TAG,
             "SERVER_DATA,{\"gateway_mac_address\":\"%02X:%02X:%02X:%02X:%02X:%02X\","
             "\"node_id\":%u,\"counter\":%lu,"
             "\"tiltX\":%.2f,\"tiltY\":%.2f,"
             "\"vibration_magnitude\":%.3f,"
             "\"vibration_detected\":%s,"
             "\"battery_voltage\":%.2f,"
             "\"battery_percentage\":%.1f,"
             "\"sos_active\":%s,"
             "\"danger_level\":%u,\"fast_mode\":%s,\"buzzer_on\":%s,"
             "\"last_cmd_seq\":%u}",
             out->gateway_mac_address[0],
             out->gateway_mac_address[1],
             out->gateway_mac_address[2],
             out->gateway_mac_address[3],
             out->gateway_mac_address[4],
             out->gateway_mac_address[5],
             out->node_id,
             (unsigned long)out->counter,
             out->tiltX,
             out->tiltY,
             out->vibration_magnitude,
             out->vibration_detected ? "true" : "false",
             out->battery_voltage,
             out->battery_percentage,
             out->sos_active ? "true" : "false",
             (unsigned int)out->danger_level,
             out->fast_mode ? "true" : "false",
             out->buzzer_on ? "true" : "false",
             (unsigned int)out->last_cmd_seq);

    if (!wifi_connected) {
        ESP_LOGW(TAG, "Wi-Fi not connected; reading not sent to server");
        return false;
    }

    /* Created once and reused; the client reconnects on its own if needed. */
    static esp_http_client_handle_t client = NULL;
    if (client == NULL) {
        esp_http_client_config_t cfg = {
            .url = SERVER_URL,
            .method = HTTP_METHOD_POST,
            .timeout_ms = SERVER_TIMEOUT_MS,
            .keep_alive_enable = true,
            .event_handler = http_event_handler,
        };
        client = esp_http_client_init(&cfg);
        if (client == NULL) {
            ESP_LOGE(TAG, "HTTP client init failed");
            return false;
        }
        esp_http_client_set_header(client, "Content-Type", "application/octet-stream");
        esp_http_client_set_header(client, "X-Gateway-Id", GATEWAY_ID);
#ifdef SERVER_API_KEY
        esp_http_client_set_header(client, "X-API-Key", SERVER_API_KEY);
#endif
    }

    esp_http_client_set_post_field(client, (const char *)out, sizeof(payload_t));
    server_resp_len = 0;
    server_resp[0] = '\0';
    esp_err_t err = esp_http_client_perform(client);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Server POST failed: %s", esp_err_to_name(err));
        return false;
    }

    int status = esp_http_client_get_status_code(client);
    if (status != 200) {
        ESP_LOGW(TAG, "Server replied HTTP %d", status);
        return false;
    }

    if (server_resp_len > 0) {
        handle_server_commands(server_resp);
    }
    return true;
}

/* -------------------------------------------------------------------------- */
/* Application                                                                */
/* -------------------------------------------------------------------------- */

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(nvs_open(NVS_NAMESPACE, NVS_READWRITE, &registry_nvs));
    load_node_registry();

    rx_queue = xQueueCreate(RX_QUEUE_LENGTH, sizeof(received_packet_t));
    if (rx_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create RX queue");
        return;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();    /* needed to get an IP address */

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
                                               wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               wifi_event_handler, NULL));

    wifi_init_config_t wifi_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));

    wifi_config_t sta_cfg = {0};
    strncpy((char *)sta_cfg.sta.ssid, WIFI_SSID, sizeof(sta_cfg.sta.ssid) - 1);
    strncpy((char *)sta_cfg.sta.password, WIFI_PASS, sizeof(sta_cfg.sta.password) - 1);
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));

    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE));
    /* Modem sleep can make a connected station miss ESP-NOW packets. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    /* Connect after the channel call above; the first connect is not event-driven. */
    ESP_ERROR_CHECK(esp_wifi_connect());

    uint8_t gateway_mac[6];
    ESP_ERROR_CHECK(esp_read_mac(gateway_mac, ESP_MAC_WIFI_STA));

    ESP_LOGI(TAG, "Gateway MAC: %02X:%02X:%02X:%02X:%02X:%02X",
             gateway_mac[0], gateway_mac[1], gateway_mac[2],
             gateway_mac[3], gateway_mac[4], gateway_mac[5]);

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_recv));
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_cmd_sent));

    ESP_LOGI(TAG, "ESP-NOW gateway ready. Listening on channel %d for up to %d nodes.",
             WIFI_CHANNEL, MAX_NODES);

    received_packet_t received;

    while (1) {
        if (xQueueReceive(rx_queue, &received, portMAX_DELAY) == pdTRUE) {
            /*
             * Register by the sender's real Wi-Fi MAC.
             * A known MAC keeps its ID; a newly detected MAC gets a new ID.
             */
            int assigned_node = get_or_register_node(received.source_mac);

            if (assigned_node < 0) {
                continue;
            }

            /* Convert ESP-NOW input data into the server dataset. */
            payload_t server_data = {
                .gateway_mac_address = {
                    gateway_mac[0], gateway_mac[1], gateway_mac[2],
                    gateway_mac[3], gateway_mac[4], gateway_mac[5]
                },
                .node_id = (uint8_t)assigned_node,
                .counter = received.data.counter,
                .tiltX = received.data.tiltX,
                .tiltY = received.data.tiltY,
                .vibration_magnitude = received.data.vibration_magnitude,
                .vibration_detected = received.data.vibration_detected,
                .battery_voltage = received.data.battery_voltage,
                .battery_percentage = received.data.battery_percentage,
                .sos_active = false,
                .danger_level = received.data.danger_level,
                .fast_mode = received.data.fast_mode,
                .buzzer_on = received.data.buzzer_on,
                .last_cmd_seq = received.data.last_cmd_seq
            };

            /*
             * SOS: flag it to the server once per incident. Repeats from the
             * same node within SOS_DEDUPE_MS are still forwarded as normal
             * readings, but without the SOS flag.
             */
            const uint8_t nid = server_data.node_id;
            bool new_sos = false;

            if (received.data.sos_active) {
                TickType_t now = xTaskGetTickCount();
                if (!sos_seen[nid] ||
                    (now - last_sos_tick[nid]) >= pdMS_TO_TICKS(SOS_DEDUPE_MS)) {
                    new_sos = true;
                    server_data.sos_active = true;
                    ESP_LOGW(TAG, "!!! SOS from NODE %u !!!", nid);
                } else {
                    ESP_LOGI(TAG, "[NODE %u] Repeat SOS packet ignored (same incident)", nid);
                }
            }

            ESP_LOGI(TAG,
                     "[NODE %u] Counter:%lu | Pitch:%.2f | Roll:%.2f | "
                     "Vibration:%.3fg (%s) | Battery:%.2fV %.1f%% | SOS:%s | "
                     "Level:%u Fast:%s Buzzer:%s Ack:%u",
                     server_data.node_id,
                     (unsigned long)server_data.counter,
                     server_data.tiltX,
                     server_data.tiltY,
                     server_data.vibration_magnitude,
                     server_data.vibration_detected ? "DETECTED" : "clear",
                     server_data.battery_voltage,
                     server_data.battery_percentage,
                     server_data.sos_active ? "ACTIVE" : "no",
                     (unsigned int)server_data.danger_level,
                     server_data.fast_mode ? "yes" : "no",
                     server_data.buzzer_on ? "ON" : "off",
                     (unsigned int)server_data.last_cmd_seq);

            bool delivered = publish_to_server(&server_data);

            /*
             * Start the dedupe window only after the server accepted the SOS,
             * so a failed delivery is retried by the next duplicate packet.
             */
            if (new_sos && delivered) {
                sos_seen[nid] = true;
                last_sos_tick[nid] = xTaskGetTickCount();
            }
        }
    }
}
