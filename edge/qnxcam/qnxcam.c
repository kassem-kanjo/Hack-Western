/*
 * qnxcam: grab frames from a QNX camera unit (QNX Sensor Framework camera API, libcamapi)
 * and keep the newest one in a file the Python detector reads.
 *
 * Why C: libcamapi delivers frames on its own thread; calling back into Python from that thread
 * crashed CPython 3.14 on QNX 8.0, so the camera side stays native, as in QNX's own examples.
 *
 * The SDK headers are not in QNX's public package repository, so the few declarations used here
 * were taken from the debug symbols of QNX's camera_example1_callback (package
 * qnx-sf-camapi-examples-dbg): camera_buffer_t is 328 bytes, framedesc at offset 72.
 *
 * Output file layout (little endian), written to <out>.tmp then renamed (atomic for readers):
 *   uint32 magic 'QCAM', uint32 frametype, uint32 height, uint32 width, uint32 stride,
 *   uint32 reserved, int64 uv_offset, int64 timestamp_us, uint64 nbytes, then nbytes of frame.
 *
 * Build on the Pi:  gcc -O2 -o qnxcam qnxcam.c -lcamapi
 * Run:              ./qnxcam 5 /tmp/qnxcam.frame 10      (unit, output file, max fps)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <signal.h>

typedef int camera_handle_t;

typedef struct {
    int32_t frametype;
    uint64_t framesize;
    uint8_t *framebuf;
    uint64_t framemetasize;
    void *framemeta;
    int64_t frametimestamp;
    int32_t frameorientation;
    uint32_t reserved[4];
    uint64_t framedesc[32]; /* 256-byte union */
} camera_buffer_t;

_Static_assert(sizeof(camera_buffer_t) == 328, "camera_buffer_t layout");

extern int camera_open(int unit, uint32_t mode, camera_handle_t *handle);
extern int camera_start_viewfinder(camera_handle_t h,
                                   void (*vf)(camera_handle_t, camera_buffer_t *, void *),
                                   void (*status)(camera_handle_t, int, uint16_t, void *), void *arg);
extern int camera_stop_viewfinder(camera_handle_t h);
extern int camera_close(camera_handle_t h);

#define FT_NV12 1
#define FT_CBYCRY 8

static const char *g_out;
static char g_tmp[512];
static double g_min_gap_s;
static double g_last_write;
static volatile sig_atomic_t g_stop;

static double now_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void on_frame(camera_handle_t h, camera_buffer_t *b, void *arg) {
    (void)h; (void)arg;
    double t = now_s();
    if (t - g_last_write < g_min_gap_s) return;
    g_last_write = t;

    const uint32_t *d32 = (const uint32_t *)b->framedesc;
    uint32_t height, width, stride;
    int64_t uv_offset = 0;
    if (b->frametype == FT_CBYCRY) { /* uint64 bufsize, then height, width, stride */
        height = d32[2]; width = d32[3]; stride = d32[4];
    } else {                         /* NV12 / YCbYCr / RGB: height, width, stride first */
        height = d32[0]; width = d32[1]; stride = d32[2];
        if (b->frametype == FT_NV12) memcpy(&uv_offset, (const uint8_t *)b->framedesc + 16, 8);
    }

    FILE *f = fopen(g_tmp, "wb");
    if (!f) return;
    uint32_t head[6] = {0x4D414351u /* 'QCAM' */, (uint32_t)b->frametype, height, width, stride, 0};
    int64_t ts = b->frametimestamp;
    /* framesize is not the pixel-buffer length on QNX 8.0.5 (reads 84 for a 640x480 YUYV frame),
       so size the copy from the frame description instead. */
    uint64_t n;
    if (b->frametype == FT_NV12) n = (uint64_t)uv_offset + (uint64_t)(height / 2) * stride;
    else n = (uint64_t)height * stride;
    fwrite(head, sizeof head, 1, f);
    fwrite(&uv_offset, 8, 1, f);
    fwrite(&ts, 8, 1, f);
    fwrite(&n, 8, 1, f);
    fwrite(b->framebuf, 1, (size_t)n, f);
    fclose(f);
    rename(g_tmp, g_out);
}

static void on_signal(int s) { (void)s; g_stop = 1; }

int main(int argc, char **argv) {
    int unit = argc > 1 ? atoi(argv[1]) : 5;
    g_out = argc > 2 ? argv[2] : "/tmp/qnxcam.frame";
    double fps = argc > 3 ? atof(argv[3]) : 10.0;
    g_min_gap_s = fps > 0 ? 1.0 / fps : 0.0;
    snprintf(g_tmp, sizeof g_tmp, "%s.tmp", g_out);
    signal(SIGINT, on_signal);
    signal(SIGTERM, on_signal);

    camera_handle_t h = -1;
    int err = camera_open(unit, 7 /* mode used by QNX's camera_example1_callback */, &h);
    if (err) { fprintf(stderr, "qnxcam: camera_open(unit %d) failed: %d\n", unit, err); return 1; }
    err = camera_start_viewfinder(h, on_frame, NULL, NULL);
    if (err) { fprintf(stderr, "qnxcam: camera_start_viewfinder failed: %d\n", err); camera_close(h); return 1; }
    fprintf(stderr, "qnxcam: unit %d streaming to %s at <= %.1f fps\n", unit, g_out, fps);
    while (!g_stop) sleep(1);
    camera_stop_viewfinder(h);
    camera_close(h);
    return 0;
}
