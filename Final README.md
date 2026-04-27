# 🌱 ForgetMeNot
## Intelligent Plant Monitor: Making hobbies simplified 
---

## What is ForgetMeNot?

ForgetMeNot is an intelligent plant monitoring system built for busy people who love plants but struggle with consistency. A capacitive soil moisture sensor reads the soil in real time, a Raspberry Pi 4 classifies the moisture level against AI-generated thresholds, and a NeoPixel LED strip on the pot glows green, amber, or red to tell you exactly what your plant needs — no guessing, no guilt.

---

## How It Works

1. Add a plant profile in the web UI (name + plant type)
2. The LLM generates personalized moisture thresholds and a care guide
3. Activate the plant — the Pi begins monitoring
4. Insert the sensor into the soil
5. The LED strip updates in real time based on moisture readings
6. Check the live dashboard for history, graphs, and care recommendations

---

## LED Status Guide

| Color | Status | Action |
|---|---|---|
| 🟢 Green | Healthy | No action needed |
| 🟡 Amber | Getting dry | Water soon |
| 🔴 Red | Needs water | Water now |
| 🔵 Blue | Overwatered | Let it dry out |
| 🟣 Purple | Error | Check server connection |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Edge compute | Raspberry Pi 4 + ADS1115 16-bit ADC (I2C) |
| Sensor | Capacitive Soil Moisture Sensor v1.2 |
| LED output | Adafruit NeoPixel strip (60 LEDs, 1m) on GPIO 18 |
| Communication | REST API (HTTP) over local WiFi |
| ML / LLM | Duke LiteLLM Gateway (GPT) |
| Backend | Python Flask + SQLite |
| Frontend | Vanilla JS + Chart.js |

---

## Hardware Wiring

### ADS1115 → Raspberry Pi 4
| Wire | ADS1115 | Pi Pin |
|---|---|---|
| Red | VIN | Pin 1 — 3.3V |
| Black | GND | Pin 6 — GND |
| Blue | SDA | Pin 3 — GPIO 2 |
| Yellow | SCL | Pin 5 — GPIO 3 |

### Soil Sensor → ADS1115
| Wire | Sensor | ADS1115 |
|---|---|---|
| Red | VCC | VIN |
| Black | GND | GND |
| Yellow | AOUT | A0 |

### NeoPixel Strip → Raspberry Pi 4
| Wire | Strip | Pi Pin |
|---|---|---|
| Red (bare) | 5V | Pin 2 — 5V |
| White (JST) | DIN | Pin 12 — GPIO 18 |
| Black #1 (JST) | GND | Pin 14 — GND |
| Black #2 (bare) | GND | Pin 9 — GND |

---

## Project Setup

### Requirements

On your Mac (runs app.py):
pip install -r requirements-mac.txt

On your Pi (runs controller.py):
pip install -r requirements-pi.txt --break-system-packages

### Environment Variables
Create a .env file in the root folder:
LITELLM_TOKEN=your_duke_litellm_token_here

### Running the App

Step 1 — Start the Flask server on your Mac:
python3 app.py

Step 2 — Calibrate the sensor on your Pi (first time only):
sudo python calibrate.py
Hold sensor in open air for 10 seconds (DRY_RAW), then dip in water for 10 seconds (WET_RAW). Update the values in controller.py.

Step 3 — Start the controller on your Pi:
sudo python controller.py

Step 4 — Open the web UI on your Mac:
http://localhost:5000

### Demo Mode
To test the LED strip without a sensor reading:
sudo python controller.py --demo

---

## Sensor Calibration

Update these two values in controller.py after running calibrate.py:

WET_RAW = 8560   # raw ADS1115 value when sensor is in water
DRY_RAW = 17720  # raw ADS1115 value when sensor is in open air

---

## File Structure

ForgetMeNot/
- app.py — Flask server, REST API, LLM calls, database
- controller.py — Pi controller, sensor reading, LED control
- calibrate.py — One-time sensor calibration helper
- requirements-mac.txt — Mac dependencies
- requirements-pi.txt — Pi dependencies
- .env — API keys (not committed to GitHub)
- data/
  - forgetmenot.db — SQLite database
- Templates/
  - index.html — Main web UI
  - plant_detail.html — Plant detail and monitoring page

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | / | Main web UI |
| POST | /api/plants | Create a plant and generate LLM thresholds |
| POST | /api/plants/:id/activate | Set plant as active monitor |
| DELETE | /api/plants/:id | Delete a plant |
| GET | /api/plants/:id/thresholds | Get thresholds and care guide |
| GET | /api/active-plant | Get active plant and thresholds (used by Pi) |
| POST | /api/log | Log a sensor reading (used by Pi) |
| GET | /api/status | Get latest reading for UI polling |
| GET | /api/history/:id | Get sensor history for a plant |

---

## The Problem It Solves

Improper watering is the number one cause of houseplant death. The root cause is not laziness, it is the absence of feedback and knowledge. ForgetMeNot replaces guesswork with a real-time visual signal and AI-powered plant knowledge, making plant care accessible for busy people with ADHD, demanding schedules, or anyone who has ever killed a gifted plant and felt terrible about it.

---

## Built With

- Flask
- Adafruit CircuitPython NeoPixel
- Adafruit CircuitPython ADS1x15
- Chart.js
- Duke LiteLLM Gateway
- ClaudeCode

---
