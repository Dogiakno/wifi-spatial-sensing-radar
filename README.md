# Wi-Fi Spatial Sensing Radar (Dual-Node: macOS CoreWLAN + Flipper Zero / ESP32 Bridge)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform: macOS](https://img.shields.io/badge/Platform-macOS%20CoreWLAN-lightgrey.svg)](#installation--usage)
[![Hardware: Flipper Zero + ESP32-S3](https://img.shields.io/badge/Hardware-Flipper%20Zero%20%2B%20ESP32--S3-orange.svg)](#hardware--pinout)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](#1-python-dependencies-macos-host)

> **Turn a MacBook and a Flipper Zero + ESP32-S3 into a real-time, camera-free Wi-Fi motion radar.**

An experimental dual-node RF spatial sensing system that detects and maps indoor human movement across physical zones (**Zone A**, **Zone B**, and **Center Transition**) using Wi-Fi signal perturbation (**RF disturbance**, **RSSI moving-average delta**, and **RTT variance**).

---

## Try It in 10 Seconds (No Hardware Needed)

Want to test the live SSE dashboard right away—even on Linux/Windows or before wiring your Flipper Zero? Run the built-in simulation mode:

```bash
python3 server.py --demo
```

Then open **http://localhost:8000** in your browser to watch live Zone A, Zone B, and Center Transition waveforms stream over Server-Sent Events (`/api/stream`).

---

## Overview

Human bodies absorb, scatter, and reflect 2.4 GHz and 5 GHz Wi-Fi waves. By placing two spatially separated RF sensing nodes inside a room, perturbations in Wi-Fi propagation can be correlated in real time to localize motion without cameras or wearables:

- **Zone A (macOS Node):** Polls live Wi-Fi signal metrics via macOS `CoreWLAN` (with optional Ping RTT variance sensing) to detect RF perturbations near the MacBook.
- **Zone B (Flipper Zero + ESP32-S3 Node):** Captures remote RF signal telemetry on an ESP32-S3 Mini, bridges it over GPIO UART (`115200` baud) through a native Flipper Zero companion app (`wifi_radar_bridge`), and forwards it over USB-CDC to the host.
- **Central SSE Dashboard:** Fuses both sensor streams in a multi-threaded Python server (`server.py`) and pushes real-time zone alerts and live HTML5 Canvas waveforms to `index.html` over `/api/stream`.

---

## System Architecture

```text
+--------------------------------------+      +-----------------------------------------------+
|         NODE A: macOS Host           |      |       NODE B: Flipper Zero + ESP32 Bridge     |
|                                      |      |                                               |
|  [ CoreWLAN API + Ping RTT Sensor ]  |      |  [ ESP32-S3 Mini RF Scanner ]                 |
|      • 80 ms RSSI polling            |      |               |                               |
|      • Fast / Slow EMA delta         |      |               | GPIO UART (115200 8N1)        |
|      • RF disturbance / RTT var.     |      |               v                               |
+------------------+-------------------+      |  [ Flipper Zero: wifi_radar_bridge.fap ]      |
                   |                          |      • Async USART IRQ -> FuriStreamBuffer    |
                   |                          |      • Live OLED packet counter & preview     |
                   |                          |      • UART-to-USB-CDC forwarder              |
                   |                          +-----------------------+-----------------------+
                   |                                                  |
                   | In-process                                       | USB-CDC Serial
                   | (PyObjC)                                         | (/dev/tty.usbmodem*)
                   v                                                  v
         +-----------------------------------------------------------------------+
         |                 Central Fusion Server (server.py)                     |
         |                                                                       |
         |   • DualSensorManager (threaded CoreWLAN poller + pyserial worker)    |
         |   • Sliding-window variance & threshold detection                     |
         |   • Spatial Zone Classifier: Zone A | Zone B | Center | Idle          |
         +-----------------------------------+-----------------------------------+
                                             |
                                             | HTTP Server-Sent Events (SSE)
                                             | GET /api/stream (20 Hz JSON)
                                             v
         +-----------------------------------------------------------------------+
         |                  Real-Time Web Dashboard (index.html)                 |
         |                                                                       |
         |   • Active Zone Banner (Zone A / Zone B / Center Transition)          |
         |   • Flipper Zero Auto-Discovery & Live Connection Indicator           |
         |   • Dual HTML5 Canvas Live RF Signal Waveforms                        |
         +-----------------------------------------------------------------------+
```

---

## Hardware & Pinout

### Required Hardware

- **Node A:** macOS machine (Apple Silicon or Intel) connected to Wi-Fi.
- **Node B:** Flipper Zero + ESP32-S3 Mini wired over Flipper GPIO.
- **Link:** USB-C cable connecting the Flipper Zero to the macOS host.

### ESP32-S3 Mini <-> Flipper Zero GPIO Pinout

> **Important:** Cross-connect UART TX and RX lines (`TX <-> RX`). If powering the ESP32-S3 Mini from the Flipper Zero, enable **5V on GPIO** (*GPIO -> 5V on GPIO -> ON*).

| Flipper Zero Pin | Flipper Signal | ESP32-S3 Mini Pin | Direction / Function |
| :---: | :---: | :---: | :--- |
| **Pin 1** | `5V` | `5V` / `VBUS` | Power supply (5V output from Flipper GPIO) |
| **Pin 8** | `GND` | `GND` | Common ground reference |
| **Pin 13** | `TX` (USART) | `RX` | UART Transmit (Flipper -> ESP32-S3) |
| **Pin 14** | `RX` (USART) | `TX` | UART Receive (ESP32-S3 -> Flipper) |

- **Serial Parameters:** `115200` baud, 8 data bits, no parity, 1 stop bit (`115200 8N1`).
- **Expected Telemetry Format:** Newline-delimited strings containing `RSSI:` or `SIG:` followed by a signed dBm value (for example, `RSSI: -54` or `SIG: -62`).

---

## Installation & Usage

### 1. Python Dependencies (macOS Host)

Python 3.9+ is recommended:

```bash
git clone https://github.com/Dogiakno/wifi-spatial-sensing-radar.git
cd wifi-spatial-sensing-radar

python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt
# Or install directly:
# pip install pyobjc-framework-CoreWLAN pyserial
```

---

### 2. Building the Flipper Zero Companion App (`ufbt`)

The Flipper Zero companion app lives in [`flipper_companion/`](flipper_companion/) (`application.fam` + `wifi_radar_bridge.c`) and implements an interrupt-driven UART-to-USB-CDC bridge under the **GPIO** app category.

1. **Install uFBT:**
   ```bash
   pip install ufbt
   ```

2. **Sync the SDK matching your Flipper firmware:**
   - **Official Firmware:**
     ```bash
     ufbt update
     ```
   - **Momentum / Unleashed Firmware API Support:**
     Point uFBT to your firmware's SDK catalog so the compiled `.fap` matches your device's API version:
     ```bash
     # Momentum firmware SDK catalog
     ufbt update --index-url=https://up.momentum-fw.dev/firmware/directory.json

     # Or specify an Unleashed / custom firmware uFBT index URL
     ufbt update --index-url=<FIRMWARE_UFBT_INDEX_URL>
     ```

3. **Build and launch on Flipper Zero:**
   ```bash
   cd flipper_companion
   ufbt          # Compiles wifi_radar_bridge.fap
   ufbt launch   # Uploads to /ext/apps/GPIO/wifi_radar_bridge.fap and launches
   ```

   The Flipper OLED displays **RF Spatial Radar Bridge**, a live forwarded packet counter, and the latest received line. Press **Back** to exit cleanly.

---

### 3. Starting the Web Dashboard

From the repository root:

```bash
# Live hardware mode (macOS CoreWLAN + Flipper Zero USB-CDC auto-discovery)
python3 server.py

# Optional flags
python3 server.py --port 8080
python3 server.py --serial-port /dev/tty.usbmodemflip_XXXX
python3 server.py --demo
```

Open **http://localhost:8000** in your browser.

---

## Spatial Detection Logic

| Node | Metric | Sampling / Window | Motion Threshold |
| :--- | :--- | :--- | :--- |
| **Zone A (macOS)** | `2.0 * abs(EMA_fast - EMA_slow)` (`α_fast=0.35`, `α_slow=0.05`) | 80 ms interval, 60-sample buffer | `mac_score > 0.8` |
| **Zone B (Flipper / ESP32)** | Sliding-window RSSI variance (`Var(last 8 samples)`) | Event-driven serial stream, 60-sample buffer | `flipper_score > 1.5` |

- **Zone A active only:** Motion near MacBook
- **Zone B active only:** Motion near Flipper Zero / ESP32-S3
- **Both nodes active:** Center transition between sensors
- **Neither active:** Calm / static environment

---

## Repository Structure

```text
wifi-spatial-sensing-radar/
├── server.py                        # Multi-threaded dual-node SSE backend (/api/stream)
├── index.html                       # Real-time dual-zone web radar dashboard
├── requirements.txt                 # Python dependencies (CoreWLAN + pyserial)
├── flipper_companion/
│   ├── application.fam              # Flipper Zero FAP manifest (GPIO category)
│   ├── wifi_radar_bridge.c          # Async UART-to-USB-CDC Flipper bridge app
│   └── wifi_radar.png               # Flipper app icon asset
├── .gitignore                       # Python, macOS, venv, and uFBT build ignores
├── LICENSE                          # MIT License
└── README.md                        # Project documentation
```

---

## Troubleshooting

- **Zone A stays at `0.00` on macOS:** Ensure your Mac is associated with a Wi-Fi network and that your terminal app has Location / Wi-Fi permissions on macOS Sequoia/Sonoma if `CoreWLAN` reports `0` dBm.
- **Flipper Zero not detected:** Confirm `wifi_radar_bridge` is running on Flipper (and that qFlipper or another serial monitor isn't holding the USB-CDC port). You can specify the device explicitly via `--serial-port`.
- **Flipper API mismatch error:** Run `ufbt update` against the exact firmware channel (Official, Momentum, or Unleashed) installed on your Flipper Zero, then rebuild with `ufbt -c && ufbt`.

---

## Contributing

Issues, hardware test reports, ESP32-S3 firmware sketches, and pull requests are welcome! If you find this experiment useful, consider giving the repository a star.

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
