# Hormuz radar validation

Generated: 2026-11-21

## Method

For each event we pool the dark share (unmatched radar detections divided by all detections) over the 14 full days before the event and the 14 days after it, in Hormuz and in an open Gulf of Oman control box fetched from the same SAR swaths. Days with fewer than 20 detections are dropped, and the difference-in-differences removes changes shared by both areas; its 95% interval is a 1,000-resample day-block bootstrap with seed 0. A change is reported as coinciding with an event only when that interval excludes zero; this is a descriptive comparison, not a causal estimate.

## Event study

| Date | Event | Hormuz pre → post | Control pre → post | DiD (95% CI) | Verdict |
| --- | --- | --- | --- | --- | --- |
| 2025-03-13 | OFAC sanctions Iran oil minister, shadow fleet operators | 0.268 → 0.267 | 0.236 → 0.219 | +0.016 (-0.083 to +0.139) | no detectable change |
| 2025-05-13 | OFAC targets Sepehr Energy oil shipping network | 0.264 → 0.269 | 0.242 → 0.156 | +0.090 (-0.019 to +0.184) | no detectable change |
| 2025-05-18 to 2025-05-20 | UKMTO/JMIC: hours-long GNSS interference in Hormuz | 0.251 → 0.270 | 0.245 → 0.211 | +0.054 (-0.078 to +0.180) | no detectable change |
| 2025-06-13 | Israel begins strikes on Iran (Operation Rising Lion) | 0.269 → 0.347 | 0.205 → 0.199 | +0.085 (-0.054 to +0.239) | no detectable change |
| 2025-06-12 to 2025-06-18 | Windward: ~1,000 Gulf ships jammed since 12 June | 0.269 → 0.330 | 0.205 → 0.180 | +0.086 (-0.066 to +0.234) | no detectable change |
| 2025-06-16 | JMIC/UKMTO: electronic interference surges, Bandar Abbas | 0.251 → 0.335 | 0.146 → 0.194 | +0.036 (-0.113 to +0.212) | no detectable change |
| 2025-06-17 | Front Eagle and Adalynn tankers collide off Fujairah | 0.275 → 0.335 | 0.165 → 0.194 | +0.031 (-0.117 to +0.172) | no detectable change |
| 2025-06-21 to 2025-06-22 | US strikes Iranian nuclear sites | 0.293 → 0.325 | 0.156 → 0.196 | -0.008 (-0.157 to +0.115) | no detectable change |
| 2025-06-24 | Israel-Iran ceasefire takes effect | 0.323 → 0.340 | 0.180 → 0.224 | -0.027 (-0.153 to +0.090) | no detectable change |
| 2025-07-03 | OFAC targets networks facilitating Iranian oil trade | 0.330 → 0.312 | 0.180 → 0.187 | -0.024 (-0.141 to +0.092) | no detectable change |
| 2025-08-21 | OFAC targets Iranian oil exports and shadow fleet | 0.280 → 0.287 | 0.207 → 0.183 | +0.031 (-0.062 to +0.140) | no detectable change |
| 2025-09-28 to 2025-09-29 | UN snapback sanctions reimposed; EU follows 29 Sept | 0.257 → 0.392 | 0.232 → 0.479 | -0.112 (-0.641 to +0.259) | no detectable change |
| 2025-10-09 | OFAC sanctions 23 vessels, Chinese teapot and terminal | 0.373 → 0.394 | 0.589 → 0.344 | +0.267 (-0.325 to +0.907) | no detectable change |
| 2025-10-12 to 2025-10-16 | Iranian NITC tankers briefly switch AIS on, then dark | 0.386 → 0.434 | 0.426 → 0.328 | +0.146 (-0.287 to +0.624) | no detectable change |
| 2025-11-14 | IRGC seizes tanker Talara after Hormuz transit | 0.389 → 0.419 | 0.325 → 0.414 | -0.058 (-0.398 to +0.326) | no detectable change |
| 2025-12-18 | OFAC sanctions 29 vessels, Iran shadow fleet | 0.401 → 0.411 | 0.340 → 0.389 | -0.040 (-0.563 to +0.473) | no detectable change |
| 2026-01-23 | OFAC sanctions 9 vessels over protester killings | 0.400 → 0.405 | 0.322 → 0.329 | -0.001 (-0.447 to +0.444) | no detectable change |
| 2026-02-25 | OFAC targets 12 shadow fleet vessels | 0.471 → 0.592 | 0.407 → 0.328 | +0.201 (-0.232 to +0.681) | no detectable change |
| 2026-02-28 | US-Israel strikes on Iran begin; Hormuz closed | 0.394 → 0.779 | 0.322 → 0.509 | +0.198 (-0.342 to +0.664) | no detectable change |
| 2026-03-01 | UKMTO reports three ships hit off UAE and Oman | 0.373 → 0.779 | 0.322 → 0.509 | +0.219 (-0.228 to +0.686) | no detectable change |
| 2026-02-28 to 2026-03-02 | Windward: 1,100+ Gulf vessels hit by GPS/AIS interference | 0.394 → 0.762 | 0.322 → 0.607 | +0.083 (-0.324 to +0.551) | no detectable change |
| 2026-03-02 | IRGC confirms strait closed to 'unfriendly nations' | 0.380 → 0.762 | 0.385 → 0.607 | +0.160 (-0.267 to +0.715) | no detectable change |
| 2026-04-08 | US-Iran two-week ceasefire declared | 0.634 → 0.456 | 0.250 → 0.418 | -0.346 (-0.669 to -0.017) | moves with event |
| 2026-04-13 to 2026-06-18 | US naval blockade of Iranian ports (first period) | 0.691 → 0.616 | 0.397 → 0.565 | -0.244 (-0.733 to +0.265) | no detectable change |
| 2026-04-24 | OFAC 'Economic Fury': Hengli refinery, 19 vessels | 0.441 → 0.539 | 0.407 → 0.436 | +0.069 (-0.327 to +0.532) | no detectable change |
| 2026-06-17 to 2026-06-18 | Islamabad MoU signed; strait reopens, blockade lifted | 0.751 → 0.616 | 0.512 → 0.565 | -0.188 (-0.592 to +0.114) | no detectable change |
| 2026-06-27 | UKMTO: tanker hit by projectile, threat raised | 0.698 → 0.501 | 0.579 → 0.435 | -0.054 (-0.415 to +0.360) | no detectable change |
| 2026-07-07 to 2026-07-13 | MoU collapses; Iran re-closes strait | 0.554 → 0.487 | 0.494 → 0.383 | +0.044 (-0.422 to +0.497) | no detectable change |
| 2026-07-14 | US naval blockade of Iran resumes | 0.523 → 0.494 | 0.405 → 0.383 | -0.007 (-0.397 to +0.359) | no detectable change |
| 2026-07-30 | Iranian tankers go dark off Malaysia for STS | 0.495 → 0.504 | 0.443 → 0.586 | -0.134 (-0.664 to +0.450) | no detectable change |
| 2026-08-24 | Treasury launches 'Operation Economic Outcast' | 0.548 → 0.444 | 0.691 → 0.508 | +0.079 (-0.367 to +0.488) | no detectable change |
| 2026-08-29 to 2026-08-30 | UKMTO Warning 122-26: tanker hit north of Khasab | 0.491 → 0.461 | 0.621 → 0.462 | +0.129 (-0.308 to +0.582) | no detectable change |
| 2026-10-08 | OFAC sanctions 17 shadow-fleet vessels | 0.475 → 0.554 | 0.469 → 0.681 | -0.133 (-0.613 to +0.496) | no detectable change |
| 2026-10-11 | UKMTO reports up to six ship strikes in a day | 0.548 → 0.492 | 0.697 → 0.510 | +0.130 (-0.370 to +0.573) | no detectable change |
| 2026-10-20 | IRGC declares Hormuz closed again | 0.535 → 0.488 | 0.611 → 0.531 | +0.032 (-0.480 to +0.492) | no detectable change |
| 2026-11-10 to 2026-11-16 | Tanker strikes: Fujairah Energy 10 Nov, fire 16 Nov | 0.552 → 0.559 | 0.620 → — | — | insufficient coverage |

## Overall regime

Baseline 2025-01-01 to 2025-12-31 against 2026-03-01 to 2026-06-30.

| Area | Baseline → current | Change |
| --- | --- | --- |
| Hormuz | 0.316 → 0.660 | +0.343 |
| Control (Gulf of Oman) | 0.260 → 0.458 | +0.198 |
| DiD (95% CI) | +0.145 (+0.022 to +0.258) | moves with event |

## Coverage

Days used are days with at least 20 radar detections (a partial satellite pass is left out).

| Month | Hormuz days used | Hormuz mean detections/day | Control (Gulf of Oman) days used | Control (Gulf of Oman) mean detections/day |
| --- | --- | --- | --- | --- |
| 2025-01 | 18 | 358.4 | 10 | 50.4 |
| 2025-02 | 17 | 363.5 | 10 | 51.4 |
| 2025-03 | 18 | 353.6 | 10 | 50.0 |
| 2025-04 | 16 | 384.2 | 10 | 50.3 |
| 2025-05 | 17 | 341.6 | 10 | 51.5 |
| 2025-06 | 16 | 366.3 | 7 | 52.3 |
| 2025-07 | 17 | 381.1 | 10 | 41.7 |
| 2025-08 | 16 | 328.6 | 9 | 44.7 |
| 2025-09 | 17 | 362.3 | 8 | 53.5 |
| 2025-10 | 17 | 392.5 | 9 | 49.6 |
| 2025-11 | 16 | 372.4 | 10 | 49.9 |
| 2025-12 | 18 | 380.4 | 9 | 46.8 |
| 2026-01 | 18 | 391.2 | 11 | 46.5 |
| 2026-02 | 16 | 414.6 | 8 | 56.6 |
| 2026-03 | 18 | 342.4 | 8 | 34.5 |
| 2026-04 | 16 | 400.1 | 9 | 37.8 |
| 2026-05 | 15 | 301.1 | 6 | 34.2 |
| 2026-06 | 17 | 366.0 | 8 | 38.2 |
| 2026-07 | 30 | 361.0 | 12 | 38.8 |
| 2026-08 | 30 | 397.7 | 10 | 37.4 |
| 2026-09 | 27 | 416.8 | 8 | 40.5 |
| 2026-10 | 26 | 464.2 | 9 | 38.8 |
| 2026-11 | 16 | 491.3 | 5 | 37.6 |

## Caveats

- Radar misses vessels under ~25 m
- 'Dark' means no AIS match, not wrongdoing
- Coincides with, does not establish cause
- Control is open Gulf of Oman water (57.6-59.8E, 23.6-25.2N) on the same SAR swaths

Source: Global Fishing Watch SAR vessel detections (Sentinel-1). Powered by Global Fishing Watch.
