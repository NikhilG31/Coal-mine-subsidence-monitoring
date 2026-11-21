#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <math.h>

#include "esp_wifi.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "esp_now.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "driver/gpio.h"
#include "driver/i2c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "espnow_sensor";

#define I2C_MASTER_SCL_IO          22
#define I2C_MASTER_SDA_IO          21
#define I2C_MASTER_NUM             I2C_NUM_0
#define I2C_MASTER_FREQ_HZ         100000
#define I2C_MASTER_TX_BUF_DISABLE  0
#define I2C_MASTER_RX_BUF_DISABLE  0

#define MPU6050_ADDR               0x68
#define MPU6050_REG_PWR_MGMT_1     0x6B
#define MPU6050_REG_ACCEL_XOUT     0x3B

#define VIBRATION_SENSOR_PIN       GPIO_NUM_4
#define RECALIBRATE_BUTTON_PIN     GPIO_NUM_18
#define SOS_BUTTON_PIN             GPIO_NUM_19   /* Button between GPIO19 and GND */

#define SOS_DEBOUNCE_MS            300           /* Ignore re-triggers within this time */
#define SOS_BURST_COUNT            3             /* Send the SOS packet this many times */
#define SOS_BURST_GAP_MS           100           /* Gap between burst packets */

/* Buzzer (driven by server commands). ASSUMED pin/polarity: change to match your wiring. */
#define BUZZER_PIN                 GPIO_NUM_23
#define BUZZER_ACTIVE_LEVEL        1             /* 1 = buzzer sounds when the pin is HIGH */
#define BUZZER_AUTO_OFF_MS         60000         /* Safety: buzzer stops itself after this long */

#define WIFI_CHANNEL               7

#define WINDOW_SAMPLES             100
#define SAMPLE_PERIOD_MS           10
#define CAL_WINDOWS                5
#define SEND_INTERVAL_NORMAL_MS    5000          /* Normal reporting interval */
#define SEND_INTERVAL_FAST_MS      2000          /* Reporting interval while in fast mode */
#define MEASUREMENT_WINDOW_MS      (WINDOW_SAMPLES * SAMPLE_PERIOD_MS)

_Static_assert(SEND_INTERVAL_FAST_MS > MEASUREMENT_WINDOW_MS,
               "fast interval must be longer than the measurement window");

/* Hysteresis: a metric must fall this far below a threshold before the level steps down. */
#define TILT_HYSTERESIS_DEG        0.1f
#define VIB_HYSTERESIS_G           0.01f

#define PLACEHOLDER_BATTERY_VOLTAGE     3.85f
#define PLACEHOLDER_BATTERY_PERCENTAGE  82.0f

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Gateway receiver's Wi-Fi STA MAC address. */
static uint8_t receiver_mac[6] = {
    0x38, 0x3E, 0x51, 0x57, 0x63, 0x50
};

/*
 * Must exactly match the gateway's ESP-NOW receive structure.
 * NOTE: sos_active was appended at the END, so the gateway struct must be
 * updated with the same field in the same position.
 */
typedef struct __attribute__((packed)) {
    uint8_t  mac_address[6];
    uint32_t counter;
    float    tiltX;                 // Pitch change from startup baseline
    float    tiltY;                 // Roll change from startup baseline
    float    vibration_magnitude;   // MPU6050 vibration RMS in g
    bool     vibration_detected;    // Direct SW160 digital state
    float    battery_voltage;       // Temporary placeholder
    float    battery_percentage;    // Temporary placeholder
    bool     sos_active;            // true = SOS button was pressed
    uint8_t  danger_level;          // 0 normal, 1 watch, 2 warning, 3 critical
    bool     fast_mode;             // true = reporting every 2 s
    bool     buzzer_on;             // true = buzzer currently sounding
    uint16_t last_cmd_seq;          // last server command applied (acknowledgement)
} payload_t;

_Static_assert(sizeof(payload_t) == 37, "payload_t must be 37 bytes");

static uint8_t sender_mac[6];
static uint32_t packet_counter = 0;   /* Shared by main loop and SOS task (guarded by mux) */

/*
 * SW160 interrupt event latch.
 * Any rising edge is remembered until the next ESP-NOW packet is prepared.
 */
static volatile bool sw160_event_latched = false;
static volatile bool recalibrate_requested = false;
static volatile bool sos_event_latched = false;
static portMUX_TYPE sw160_mux = portMUX_INITIALIZER_UNLOCKED;

/* Most recent regular reading; the SOS packet reuses it for context. */
static payload_t latest_packet;
static TaskHandle_t sos_task_handle = NULL;

/* ------------------------------------------------------------------------- */
/* Danger level classification                                               */
/* ------------------------------------------------------------------------- */

typedef enum { LVL_NORMAL = 0, LVL_WATCH, LVL_WARNING, LVL_CRITICAL } level_t;

/* Lower bound of Watch / Warning / Critical. Keep in sync with the server rules. */
static const float TILT_THRESHOLDS_DEG[3] = { 0.5f, 1.0f, 2.0f };
static const float VIB_THRESHOLDS_G[3]    = { 0.05f, 0.15f, 0.30f };
static const char *const LEVEL_NAMES[4]   = { "NORMAL", "WATCH", "WARNING", "CRITICAL" };

static level_t classify_level(float value, const float up[3], float hysteresis,
                              level_t prev)
{
    level_t lvl = prev;

    while (lvl < LVL_CRITICAL && value >= up[lvl]) {
        lvl++;
    }
    while (lvl > LVL_NORMAL && value < up[lvl - 1] - hysteresis) {
        lvl--;
    }
    return lvl;
}

/* ------------------------------------------------------------------------- */
/* Server commands (gateway -> node) and node status                         */
/* ------------------------------------------------------------------------- */

#define CMD_MAGIC  0xA5

enum {
    CMD_BUZZER_ON     = 1,
    CMD_BUZZER_OFF    = 2,
    CMD_RESUME_NORMAL = 3,   /* leave fast mode, back to the normal interval */
};

/* Must exactly match the gateway's command_t. */
typedef struct __attribute__((packed)) {
    uint8_t  magic;          /* CMD_MAGIC */
    uint8_t  cmd;
    uint8_t  target_mac[6];  /* this node's MAC, or FF:FF:FF:FF:FF:FF for all */
    uint16_t seq;            /* server-assigned, starts at 1; used to ignore repeats */
} command_t;

static volatile uint8_t  current_danger_level = LVL_NORMAL;
static volatile bool     fast_mode            = false;  /* latched until RESUME_NORMAL */
static volatile bool     buzzer_is_on         = false;
static volatile uint16_t last_cmd_seq         = 0;
static volatile bool     resume_requested     = false;
static esp_timer_handle_t buzzer_timer        = NULL;

static void fill_status_fields(payload_t *p)
{
    p->danger_level = current_danger_level;
    p->fast_mode    = fast_mode;
    p->buzzer_on    = buzzer_is_on;
    p->last_cmd_seq = last_cmd_seq;
}

static bool take_resume_request(void)
{
    bool requested;

    portENTER_CRITICAL(&sw160_mux);
    requested = resume_requested;
    resume_requested = false;
    portEXIT_CRITICAL(&sw160_mux);

    return requested;
}

static void buzzer_set(bool on)
{
    gpio_set_level(BUZZER_PIN, on ? BUZZER_ACTIVE_LEVEL : !BUZZER_ACTIVE_LEVEL);
    buzzer_is_on = on;
}

static void buzzer_timeout_cb(void *arg)
{
    (void)arg;
    buzzer_set(false);
    ESP_LOGW(TAG, "Buzzer auto-off after %d ms", BUZZER_AUTO_OFF_MS);
}

static void buzzer_init(void)
{
    gpio_config_t conf = {
        .pin_bit_mask = (1ULL << BUZZER_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&conf));
    gpio_set_level(BUZZER_PIN, !BUZZER_ACTIVE_LEVEL);

    const esp_timer_create_args_t timer_args = {
        .callback = buzzer_timeout_cb,
        .name = "buzzer_off"
    };
    ESP_ERROR_CHECK(esp_timer_create(&timer_args, &buzzer_timer));

    ESP_LOGI(TAG, "Buzzer enabled on GPIO %d", BUZZER_PIN);
}

/*
 * ESP-NOW receive callback (Wi-Fi task context): validate, then act.
 * Only commands from the gateway and addressed to this node are accepted.
 */
static void on_command_recv(const esp_now_recv_info_t *info,
                            const uint8_t *data, int len)
{
    static const uint8_t broadcast_mac[6] = {
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF
    };

    if (info == NULL || info->src_addr == NULL || data == NULL) {
        return;
    }
    if (len != (int)sizeof(command_t)) {
        return;
    }
    if (memcmp(info->src_addr, receiver_mac, 6) != 0) {
        return;
    }

    command_t cmd;
    memcpy(&cmd, data, sizeof(cmd));

    if (cmd.magic != CMD_MAGIC) {
        return;
    }
    if (memcmp(cmd.target_mac, sender_mac, 6) != 0 &&
        memcmp(cmd.target_mac, broadcast_mac, 6) != 0) {
        return;
    }

    /* Repeats of an already-applied command are ignored (still acknowledged). */
    if (cmd.seq == last_cmd_seq) {
        ESP_LOGI(TAG, "Repeat command seq %u ignored", (unsigned)cmd.seq);
        return;
    }

    switch (cmd.cmd) {
    case CMD_BUZZER_ON:
        esp_timer_stop(buzzer_timer);   /* restart the auto-off timer */
        buzzer_set(true);
        esp_timer_start_once(buzzer_timer, (uint64_t)BUZZER_AUTO_OFF_MS * 1000ULL);
        ESP_LOGW(TAG, "Command: BUZZER ON (seq %u)", (unsigned)cmd.seq);
        break;

    case CMD_BUZZER_OFF:
        esp_timer_stop(buzzer_timer);
        buzzer_set(false);
        ESP_LOGW(TAG, "Command: BUZZER OFF (seq %u)", (unsigned)cmd.seq);
        break;

    case CMD_RESUME_NORMAL:
        portENTER_CRITICAL(&sw160_mux);
        resume_requested = true;
        portEXIT_CRITICAL(&sw160_mux);
        ESP_LOGW(TAG, "Command: RESUME NORMAL (seq %u)", (unsigned)cmd.seq);
        break;

    default:
        ESP_LOGW(TAG, "Unknown command %u ignored", (unsigned)cmd.cmd);
        return;
    }

    last_cmd_seq = cmd.seq;
}

/* GPIO ISR: keep this short; do not log or perform I2C/ESP-NOW operations here. */
static void IRAM_ATTR sw160_gpio_isr_handler(void *arg)
{
    (void)arg;

    portENTER_CRITICAL_ISR(&sw160_mux);
    sw160_event_latched = true;
    portEXIT_CRITICAL_ISR(&sw160_mux);
}

/* Read and clear all SW160 events accumulated since the previous packet. */
static bool sw160_take_event(void)
{
    bool detected;

    portENTER_CRITICAL(&sw160_mux);
    detected = sw160_event_latched;
    sw160_event_latched = false;
    portEXIT_CRITICAL(&sw160_mux);

    return detected;
}

/* Recalibration button ISR: latch the press until the main task handles it. */
static void IRAM_ATTR recalibrate_button_isr_handler(void *arg)
{
    (void)arg;

    portENTER_CRITICAL_ISR(&sw160_mux);
    recalibrate_requested = true;
    portEXIT_CRITICAL_ISR(&sw160_mux);
}

static bool take_recalibrate_request(void)
{
    bool requested;

    portENTER_CRITICAL(&sw160_mux);
    requested = recalibrate_requested;
    recalibrate_requested = false;
    portEXIT_CRITICAL(&sw160_mux);

    return requested;
}

/*
 * SOS button ISR: debounce, latch the event (so the next regular packet also
 * carries it as a backup), and wake the SOS task to transmit immediately.
 */
static void IRAM_ATTR sos_button_isr_handler(void *arg)
{
    (void)arg;

    static TickType_t last_tick = 0;
    TickType_t now = xTaskGetTickCountFromISR();

    if (last_tick != 0 && (now - last_tick) < pdMS_TO_TICKS(SOS_DEBOUNCE_MS)) {
        return;
    }
    last_tick = now;

    portENTER_CRITICAL_ISR(&sw160_mux);
    sos_event_latched = true;
    portEXIT_CRITICAL_ISR(&sw160_mux);

    BaseType_t higher_priority_woken = pdFALSE;
    if (sos_task_handle != NULL) {
        vTaskNotifyGiveFromISR(sos_task_handle, &higher_priority_woken);
    }
    portYIELD_FROM_ISR(higher_priority_woken);
}

/* Read and clear a pending SOS event (used to piggyback on regular packets). */
static bool sos_take_event(void)
{
    bool pending;

    portENTER_CRITICAL(&sw160_mux);
    pending = sos_event_latched;
    sos_event_latched = false;
    portEXIT_CRITICAL(&sw160_mux);

    return pending;
}

static uint32_t next_packet_counter(void)
{
    uint32_t value;

    portENTER_CRITICAL(&sw160_mux);
    value = ++packet_counter;
    portEXIT_CRITICAL(&sw160_mux);

    return value;
}

static void on_sent(const esp_now_send_info_t *tx_info,
                    esp_now_send_status_t status)
{
    (void)tx_info;

    if (status == ESP_NOW_SEND_SUCCESS) {
        ESP_LOGI(TAG, "ESP-NOW packet delivered to gateway");
    } else {
        ESP_LOGW(TAG, "ESP-NOW delivery failed");
    }
}

/*
 * Dedicated task: sleeps until the SOS button is pressed, then immediately
 * sends a short burst of SOS packets (independent of the 5 s sensor cycle).
 */
static void sos_task(void *arg)
{
    (void)arg;

    while (1) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        ESP_LOGW(TAG, "SOS button pressed! Sending SOS to gateway.");

        payload_t sos_packet;
        portENTER_CRITICAL(&sw160_mux);
        sos_packet = latest_packet;
        portEXIT_CRITICAL(&sw160_mux);

        fill_status_fields(&sos_packet);
        sos_packet.sos_active = true;

        for (int i = 0; i < SOS_BURST_COUNT; i++) {
            sos_packet.counter = next_packet_counter();

            esp_err_t err = esp_now_send(
                receiver_mac, (const uint8_t *)&sos_packet, sizeof(sos_packet));

            if (err == ESP_OK) {
                ESP_LOGW(TAG, "SOS TX #%lu (%d/%d)",
                         (unsigned long)sos_packet.counter,
                         i + 1, SOS_BURST_COUNT);
            } else {
                ESP_LOGE(TAG, "SOS send error: %s", esp_err_to_name(err));
            }

            vTaskDelay(pdMS_TO_TICKS(SOS_BURST_GAP_MS));
        }
    }
}

static esp_err_t i2c_master_init(void)
{
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = I2C_MASTER_SDA_IO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = I2C_MASTER_SCL_IO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = I2C_MASTER_FREQ_HZ,
    };

    esp_err_t err = i2c_param_config(I2C_MASTER_NUM, &conf);
    if (err != ESP_OK) {
        return err;
    }

    return i2c_driver_install(I2C_MASTER_NUM, conf.mode,
                              I2C_MASTER_TX_BUF_DISABLE,
                              I2C_MASTER_RX_BUF_DISABLE, 0);
}

static esp_err_t mpu6050_init_sensor(void)
{
    /* Wake the MPU6050. */
    uint8_t wake_data[2] = { MPU6050_REG_PWR_MGMT_1, 0x00 };
    esp_err_t err = i2c_master_write_to_device(
        I2C_MASTER_NUM, MPU6050_ADDR, wake_data, sizeof(wake_data),
        pdMS_TO_TICKS(1000));

    if (err != ESP_OK) {
        return err;
    }

    /* Set accelerometer full-scale range to +/-2g. */
    uint8_t accel_config[2] = { 0x1C, 0x00 };
    return i2c_master_write_to_device(
        I2C_MASTER_NUM, MPU6050_ADDR, accel_config,
        sizeof(accel_config), pdMS_TO_TICKS(1000));
}

static void sw160_gpio_init(void)
{
    gpio_config_t io_conf = {
        .pin_bit_mask = (1ULL << VIBRATION_SENSOR_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_POSEDGE  // Match SW_160_main: rising-edge interrupt
    };
    ESP_ERROR_CHECK(gpio_config(&io_conf));

    /*
     * Install the GPIO interrupt service and attach the SW160 ISR.
     * The ISR latches an event even if the pulse ends before the next packet.
     */
    esp_err_t isr_err = gpio_install_isr_service(ESP_INTR_FLAG_IRAM);
    if (isr_err != ESP_OK && isr_err != ESP_ERR_INVALID_STATE) {
        ESP_ERROR_CHECK(isr_err);
    }

    ESP_ERROR_CHECK(gpio_isr_handler_add(
        VIBRATION_SENSOR_PIN, sw160_gpio_isr_handler, NULL));

    ESP_LOGI(TAG, "SW160 rising-edge interrupt enabled on GPIO %d",
             VIBRATION_SENSOR_PIN);
}

/* Button connects GPIO18 to GND; internal pull-up keeps it HIGH when idle. */
static void recalibrate_button_init(void)
{
    gpio_config_t button_conf = {
        .pin_bit_mask = (1ULL << RECALIBRATE_BUTTON_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE
    };
    ESP_ERROR_CHECK(gpio_config(&button_conf));

    /* The GPIO ISR service was installed by sw160_gpio_init(). */
    ESP_ERROR_CHECK(gpio_isr_handler_add(
        RECALIBRATE_BUTTON_PIN, recalibrate_button_isr_handler, NULL));

    ESP_LOGI(TAG, "Recalibration button enabled on GPIO %d (press to recalibrate)",
             RECALIBRATE_BUTTON_PIN);
}

/* SOS button connects GPIO19 to GND; internal pull-up keeps it HIGH when idle. */
static void sos_button_init(void)
{
    gpio_config_t sos_conf = {
        .pin_bit_mask = (1ULL << SOS_BUTTON_PIN),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_NEGEDGE
    };
    ESP_ERROR_CHECK(gpio_config(&sos_conf));

    /* The GPIO ISR service was installed by sw160_gpio_init(). */
    ESP_ERROR_CHECK(gpio_isr_handler_add(
        SOS_BUTTON_PIN, sos_button_isr_handler, NULL));

    ESP_LOGI(TAG, "SOS button enabled on GPIO %d", SOS_BUTTON_PIN);
}

static bool read_accel(float *ax, float *ay, float *az)
{
    uint8_t reg = MPU6050_REG_ACCEL_XOUT;
    uint8_t data[6];

    esp_err_t err = i2c_master_write_read_device(
        I2C_MASTER_NUM, MPU6050_ADDR, &reg, 1,
        data, sizeof(data), pdMS_TO_TICKS(100));

    if (err != ESP_OK) {
        return false;
    }

    int16_t raw_x = (int16_t)(((uint16_t)data[0] << 8) | data[1]);
    int16_t raw_y = (int16_t)(((uint16_t)data[2] << 8) | data[3]);
    int16_t raw_z = (int16_t)(((uint16_t)data[4] << 8) | data[5]);

    /* +/-2g range: 16384 LSB/g. */
    *ax = (float)raw_x / 16384.0f;
    *ay = (float)raw_y / 16384.0f;
    *az = (float)raw_z / 16384.0f;
    return true;
}

/*
 * Measure one 1-second window:
 * - Average pitch and roll.
 * - Remove the mean acceleration magnitude.
 * - Calculate vibration RMS and peak from the remaining variation.
 */
static bool measure_window(float *roll, float *pitch,
                           float *rms, float *peak)
{
    float magnitude[WINDOW_SAMPLES];
    float sum_magnitude = 0.0f;
    float sum_roll = 0.0f;
    float sum_pitch = 0.0f;

    TickType_t last_wake = xTaskGetTickCount();
    const TickType_t sample_ticks = pdMS_TO_TICKS(SAMPLE_PERIOD_MS);

    for (int i = 0; i < WINDOW_SAMPLES; i++) {
        float ax, ay, az;

        if (!read_accel(&ax, &ay, &az)) {
            return false;
        }

        magnitude[i] = sqrtf(ax * ax + ay * ay + az * az);
        sum_magnitude += magnitude[i];

        sum_roll += atan2f(ay, az) * (180.0f / (float)M_PI);
        sum_pitch += atan2f(-ax, sqrtf(ay * ay + az * az))
                     * (180.0f / (float)M_PI);

        vTaskDelayUntil(&last_wake, sample_ticks);
    }

    const float mean_magnitude = sum_magnitude / (float)WINDOW_SAMPLES;
    float sum_squared = 0.0f;
    float peak_value = 0.0f;

    for (int i = 0; i < WINDOW_SAMPLES; i++) {
        const float deviation = magnitude[i] - mean_magnitude;
        sum_squared += deviation * deviation;

        const float absolute_deviation = fabsf(deviation);
        if (absolute_deviation > peak_value) {
            peak_value = absolute_deviation;
        }
    }

    *rms = sqrtf(sum_squared / (float)WINDOW_SAMPLES);
    *peak = peak_value;
    *roll = sum_roll / (float)WINDOW_SAMPLES;
    *pitch = sum_pitch / (float)WINDOW_SAMPLES;

    return true;
}

/* Sensor must remain still in its installed position during calibration. */
static bool calibrate_tilt(float *base_roll, float *base_pitch)
{
    float roll_sum = 0.0f;
    float pitch_sum = 0.0f;
    int valid_windows = 0;

    ESP_LOGI(TAG, "Tilt calibration started.");
    ESP_LOGW(TAG, "Keep the sensor still for approximately 5 seconds.");

    for (int i = 0; i < CAL_WINDOWS; i++) {
        float roll, pitch, rms, peak;

        if (measure_window(&roll, &pitch, &rms, &peak)) {
            roll_sum += roll;
            pitch_sum += pitch;
            valid_windows++;
        } else {
            ESP_LOGW(TAG, "Calibration window %d failed", i + 1);
        }
    }

    if (valid_windows == 0) {
        ESP_LOGE(TAG, "Calibration failed: no valid MPU6050 readings");
        return false;
    }

    *base_roll = roll_sum / (float)valid_windows;
    *base_pitch = pitch_sum / (float)valid_windows;

    ESP_LOGI(TAG, "Baseline pitch=%.2f deg, roll=%.2f deg",
             *base_pitch, *base_roll);
    return true;
}

static void espnow_init(void)
{
    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_sent));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_command_recv));

    esp_now_peer_info_t peer_info = {0};
    memcpy(peer_info.peer_addr, receiver_mac, 6);
    peer_info.channel = WIFI_CHANNEL;
    peer_info.ifidx = WIFI_IF_STA;
    peer_info.encrypt = false;

    if (!esp_now_is_peer_exist(receiver_mac)) {
        ESP_ERROR_CHECK(esp_now_add_peer(&peer_info));
    }

    ESP_LOGI(TAG, "ESP-NOW peer configured on channel %d", WIFI_CHANNEL);
}

void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES ||
        ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    wifi_init_config_t wifi_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_channel(WIFI_CHANNEL, WIFI_SECOND_CHAN_NONE));

    ESP_ERROR_CHECK(esp_read_mac(sender_mac, ESP_MAC_WIFI_STA));
    ESP_LOGI(TAG, "Sender MAC: %02X:%02X:%02X:%02X:%02X:%02X",
             sender_mac[0], sender_mac[1], sender_mac[2],
             sender_mac[3], sender_mac[4], sender_mac[5]);

    ESP_ERROR_CHECK(i2c_master_init());
    ESP_ERROR_CHECK(mpu6050_init_sensor());
    sw160_gpio_init();
    recalibrate_button_init();
    buzzer_init();

    ESP_LOGI(TAG, "MPU6050 initialized; SW160 interrupt detection configured");

    espnow_init();

    /* Seed the "latest reading" so an early SOS still has a valid MAC/battery. */
    memcpy(latest_packet.mac_address, sender_mac, sizeof(sender_mac));
    latest_packet.battery_voltage = PLACEHOLDER_BATTERY_VOLTAGE;
    latest_packet.battery_percentage = PLACEHOLDER_BATTERY_PERCENTAGE;

    /* Start the SOS task BEFORE enabling the button interrupt, and before the
     * blocking 5 s calibration, so SOS works at any time after boot. */
    BaseType_t task_ok = xTaskCreate(sos_task, "sos_task", 4096, NULL,
                                     configMAX_PRIORITIES - 2, &sos_task_handle);
    if (task_ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create SOS task");
        return;
    }
    sos_button_init();

    float base_roll = 0.0f;
    float base_pitch = 0.0f;

    if (!calibrate_tilt(&base_roll, &base_pitch)) {
        ESP_LOGE(TAG, "Stopping: tilt baseline calibration failed");
        return;
    }

    payload_t packet = {0};
    memcpy(packet.mac_address, sender_mac, sizeof(sender_mac));

    packet.battery_voltage = PLACEHOLDER_BATTERY_VOLTAGE;
    packet.battery_percentage = PLACEHOLDER_BATTERY_PERCENTAGE;

    ESP_LOGI(TAG, "Sensor node ready. Normal interval: %d s; fast interval: %d s "
             "(from Watch level until the server resumes normal).",
             SEND_INTERVAL_NORMAL_MS / 1000, SEND_INTERVAL_FAST_MS / 1000);

    level_t tilt_lvl = LVL_NORMAL;
    level_t vib_lvl = LVL_NORMAL;
    level_t resume_level = LVL_NORMAL;   /* level at the last RESUME_NORMAL */

    while (1) {
        float roll, pitch, vibration_rms, vibration_peak;

        if (measure_window(&roll, &pitch, &vibration_rms, &vibration_peak)) {
            if (take_recalibrate_request()) {
                ESP_LOGW(TAG, "Recalibration button pressed.");
                ESP_LOGW(TAG, "Hold the sensor at the new starting position and keep it still.");

                if (calibrate_tilt(&base_roll, &base_pitch)) {
                    ESP_LOGI(TAG, "New reference position set. Pitch=%.2f deg, Roll=%.2f deg",
                             base_pitch, base_roll);
                } else {
                    ESP_LOGE(TAG, "Recalibration failed; previous reference position retained.");
                }

                /* Discard this pre-calibration measurement; next window uses the new baseline. */
                continue;
            }

            /* tiltX is pitch; tiltY is roll, both relative to the latest calibrated baseline. */
            packet.tiltX = pitch - base_pitch;
            packet.tiltY = roll - base_roll;
            packet.vibration_magnitude = vibration_rms;

            /* Danger level = worse of the tilt level and the vibration (RMS) level. */
            tilt_lvl = classify_level(fmaxf(fabsf(packet.tiltX), fabsf(packet.tiltY)),
                                      TILT_THRESHOLDS_DEG, TILT_HYSTERESIS_DEG, tilt_lvl);
            vib_lvl = classify_level(vibration_rms,
                                     VIB_THRESHOLDS_G, VIB_HYSTERESIS_G, vib_lvl);
            level_t danger = (tilt_lvl > vib_lvl) ? tilt_lvl : vib_lvl;

            /*
             * Fast mode is latched when the level reaches Watch or higher and is
             * released only by the server's RESUME_NORMAL command. After a resume,
             * fast mode re-latches only if the level rises above the level it had
             * at that moment (resume_level), so a node sitting at Watch can resume.
             */
            if (take_resume_request()) {
                fast_mode = false;
                resume_level = danger;
                ESP_LOGW(TAG, "Server resumed normal reporting (level %s)",
                         LEVEL_NAMES[danger]);
            } else if (danger < resume_level) {
                resume_level = danger;
            }

            if (danger >= LVL_WATCH && danger > resume_level && !fast_mode) {
                fast_mode = true;
                ESP_LOGW(TAG, "Danger level %s: switching to %d s reporting",
                         LEVEL_NAMES[danger], SEND_INTERVAL_FAST_MS / 1000);
            }
            current_danger_level = (uint8_t)danger;

            /*
             * Report whether the SW160 generated at least one rising edge
             * since the previous ESP-NOW packet. Short pulses are not missed.
             */
            packet.vibration_detected = sw160_take_event();

            /*
             * Backup path: if an SOS press happened since the last regular
             * packet, flag it here too in case the immediate burst was lost.
             * The gateway should treat repeated SOS flags as one incident.
             */
            packet.sos_active = sos_take_event();

            packet.battery_voltage = PLACEHOLDER_BATTERY_VOLTAGE;
            packet.battery_percentage = PLACEHOLDER_BATTERY_PERCENTAGE;
            packet.counter = next_packet_counter();
            fill_status_fields(&packet);

            /* Share the latest reading (SOS flag cleared) for the SOS task. */
            portENTER_CRITICAL(&sw160_mux);
            latest_packet = packet;
            latest_packet.sos_active = false;
            portEXIT_CRITICAL(&sw160_mux);

            esp_err_t send_err = esp_now_send(
                receiver_mac, (const uint8_t *)&packet, sizeof(packet));

            if (send_err == ESP_OK) {
                ESP_LOGI(TAG,
                         "TX #%lu | Pitch:%+.2f deg | Roll:%+.2f deg | "
                         "Vib RMS:%.4f g | Peak:%.4f g | SW160:%s | "
                         "SOS:%s | Battery:%.2f V, %.1f%% | "
                         "Level:%s | Mode:%s | Buzzer:%s",
                         (unsigned long)packet.counter,
                         packet.tiltX,
                         packet.tiltY,
                         packet.vibration_magnitude,
                         vibration_peak,
                         packet.vibration_detected ? "DETECTED" : "clear",
                         packet.sos_active ? "ACTIVE" : "no",
                         packet.battery_voltage,
                         packet.battery_percentage,
                         LEVEL_NAMES[packet.danger_level],
                         packet.fast_mode ? "FAST 2s" : "normal 5s",
                         packet.buzzer_on ? "ON" : "off");
            } else {
                ESP_LOGE(TAG, "ESP-NOW send error: %s",
                         esp_err_to_name(send_err));
            }
        } else {
            ESP_LOGW(TAG, "MPU6050 measurement failed; packet not sent");
        }

        /*
         * A measurement window is approximately 1 second.
         * Waiting the rest of the interval gives approximately 5 seconds
         * between packets normally, or 2 seconds in fast mode.
         */
        const uint32_t interval_ms = fast_mode ? SEND_INTERVAL_FAST_MS
                                               : SEND_INTERVAL_NORMAL_MS;
        vTaskDelay(pdMS_TO_TICKS(interval_ms - MEASUREMENT_WINDOW_MS));
    }
}
