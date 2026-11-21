---
name: qnx-pi-facts
description: Measured facts about the QNX 8.0 RPi5 demo unit (what exists, what stalls, how to test safely)
metadata:
  type: project
---

QNX 8.0 Pi (172.23.44.244): no `aps`/`ham`; `slm` + `slmctl` (own instance via `sudo slm -n /dev/slm-x cfg`) and `wdtkick` exist. ACT LED = AON GPIO 9 via `sudo gpio-aon-bcm set 9 op dl|dh`; header GPIO via `echo out/on > /dev/gpio/N`. HDMI: GTK4 qnx backend opens but screenshot shows nothing.

**Why:** Overseer asked for QNX-native features; these were checked on the device 2026-11-21.
**How to apply:** Do not give the AI detector SCHED_RR above the camera helper (RR 20 starved qnxcam: stream stops after 3 frames); AI RR 12, camera RR 15, core FIFO 30. Never `slay -f slm` (kills the system SLM). `sudo gpioctrl` opens a GUI and hangs ssh. A/B harness must kill the whole process group of each arm or orphans corrupt the next arm. `pidin` as qnxuser only lists own processes; use `sudo cat /proc/<pid>/cmdline`. Tool sandbox rejects git/source/heredoc combos in one command: write scripts to files, keep commands simple.
