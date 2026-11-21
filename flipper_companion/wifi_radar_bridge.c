#include <furi.h>
#include <furi_hal.h>
#include <furi_hal_serial.h>
#include <furi_hal_serial_types.h>
#include <furi_hal_usb_cdc.h>
#include <gui/gui.h>
#include <input/input.h>

#define BAUDRATE (115200)

typedef struct {
    Gui* gui;
    ViewPort* view_port;
    FuriMessageQueue* event_queue;
    FuriStreamBuffer* rx_stream;
    FuriHalSerialHandle* serial_handle;
    char last_line[64];
    uint32_t packet_count;
    bool is_running;
} RadarBridgeApp;

// UART kesmesi (IRQ) geldiğinde veriyi stream buffer'a yazar
static void uart_on_async_rx(FuriHalSerialHandle* handle, FuriHalSerialRxEvent event, void* context) {
    RadarBridgeApp* app = (RadarBridgeApp*)context;
    if(!app) return;

    if(event & FuriHalSerialRxEventData) {
        uint8_t data = furi_hal_serial_async_rx(handle);
        furi_stream_buffer_send(app->rx_stream, &data, 1, 0);
    }
}

static void render_callback(Canvas* canvas, void* ctx) {
    RadarBridgeApp* app = (RadarBridgeApp*)ctx;
    canvas_clear(canvas);

    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 12, "RF Spatial Radar Bridge");

    canvas_set_font(canvas, FontSecondary);
    canvas_draw_str(canvas, 2, 26, "ESP32-S3 -> Flipper -> Mac");

    canvas_draw_line(canvas, 0, 30, 128, 30);

    char count_str[32];
    snprintf(count_str, sizeof(count_str), "Paketler: %lu", app->packet_count);
    canvas_draw_str(canvas, 2, 43, count_str);

    canvas_draw_str(canvas, 2, 54, "Son Veri:");
    char display_buf[22];
    strncpy(display_buf, app->last_line, 20);
    display_buf[20] = '\0';
    canvas_draw_str(canvas, 50, 54, display_buf);

    canvas_draw_str(canvas, 2, 63, "[Geri] Cikis");
}

static void input_callback(InputEvent* input_event, void* ctx) {
    FuriMessageQueue* event_queue = ctx;
    furi_message_queue_put(event_queue, input_event, FuriWaitForever);
}

// Stream'den okuyup USB'ye ve ekrana basan iş parçacığı
static int32_t bridge_worker(void* context) {
    RadarBridgeApp* app = (RadarBridgeApp*)context;
    uint8_t buf[64];

    while(app->is_running) {
        size_t len = furi_stream_buffer_receive(app->rx_stream, buf, sizeof(buf) - 1, 50);
        if(len > 0) {
            // Mac USB portuna bas
            furi_hal_cdc_send(0, buf, len);

            buf[len] = '\0';
            size_t copy_len = len < 63 ? len : 63;
            memcpy(app->last_line, buf, copy_len);
            app->last_line[copy_len] = '\0';
            app->packet_count++;

            view_port_update(app->view_port);
        }
    }
    return 0;
}

int32_t wifi_radar_bridge_app(void* p) {
    UNUSED(p);
    RadarBridgeApp* app = malloc(sizeof(RadarBridgeApp));
    memset(app, 0, sizeof(RadarBridgeApp));

    app->is_running = true;
    app->event_queue = furi_message_queue_alloc(8, sizeof(InputEvent));
    app->rx_stream = furi_stream_buffer_alloc(512, 1);
    app->view_port = view_port_alloc();
    view_port_draw_callback_set(app->view_port, render_callback, app);
    view_port_input_callback_set(app->view_port, input_callback, app->event_queue);

    app->gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app->gui, app->view_port, GuiLayerFullscreen);

    // UART başlat
    app->serial_handle = furi_hal_serial_control_acquire(FuriHalSerialIdUsart);
    if(app->serial_handle) {
        furi_hal_serial_init(app->serial_handle, BAUDRATE);
        furi_hal_serial_async_rx_start(app->serial_handle, uart_on_async_rx, app, false);
    }

    // Arka plan iş parçacığı
    FuriThread* worker_thread = furi_thread_alloc();
    furi_thread_set_name(worker_thread, "BridgeWorker");
    furi_thread_set_stack_size(worker_thread, 1024);
    furi_thread_set_context(worker_thread, app);
    furi_thread_set_callback(worker_thread, bridge_worker);
    furi_thread_start(worker_thread);

    InputEvent event;
    while(1) {
        if(furi_message_queue_get(app->event_queue, &event, 100) == FuriStatusOk) {
            if(event.type == InputTypeShort && event.key == InputKeyBack) {
                break;
            }
        }
    }

    // Kapatma ve bellek temizliği
    app->is_running = false;
    furi_thread_join(worker_thread);
    furi_thread_free(worker_thread);

    if(app->serial_handle) {
        furi_hal_serial_async_rx_stop(app->serial_handle);
        furi_hal_serial_deinit(app->serial_handle);
        furi_hal_serial_control_release(app->serial_handle);
    }

    view_port_enabled_set(app->view_port, false);
    gui_remove_view_port(app->gui, app->view_port);
    view_port_free(app->view_port);
    furi_record_close(RECORD_GUI);
    furi_stream_buffer_free(app->rx_stream);
    furi_message_queue_free(app->event_queue);
    free(app);

    return 0;
}
