# qnxcam

Reads a USB webcam on QNX 8.0 through the QNX Sensor Framework camera API (`libcamapi`) and keeps
the newest frame in a file for the Python detector.

- Build on the Pi: `gcc -O2 -o qnxcam qnxcam.c -lcamapi`
- Run: `./qnxcam 5 /tmp/qnxcam.frame 10` (camera unit, output file, max fps). `camera_example1_callback`
  lists the units; on our Pi 5 the Logitech C270 is unit 5.
- Check a frame: `python3 qnxcam_read.py /tmp/qnxcam.frame out.jpg`

The camera SDK headers are not in QNX's public package repository, so the declarations in
`qnxcam.c` come from the debug symbols of QNX's own `camera_example1_callback`
(`qnx-sf-camapi-examples-dbg`). It is C, not Python ctypes, because libcamapi delivers frames on
its own thread and a Python callback from that thread crashed CPython 3.14 on QNX.
