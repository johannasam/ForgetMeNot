# 🌱 ForgetMeNot — Intelligent Plant Monitor

A Raspberry Pi–based (or fully simulated) plant monitoring system that uses an LLM to generate
personalized watering thresholds and a continuous controller loop to simulate real-time soil sensing.

---

## Architecture

```
[ Web Interface ]  →  [ LLM API ]  →  [ SQLite DB ]
                                           ↑  ↓
[ Mock Sensor ]  →  [ Controller Service (REST) ]  →  [ LED Output (terminal) ]
```

Two processes communicate via **REST API** (the midterm's "device connectivity" requirement):

| Process | File | Role |
|---|---|---|
| Web Server | `app.py` | Hosts UI, calls LLM, stores data |
| Controller | `controller.py` | Polls sensor, classifies, POSTs readings |

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Duke LiteLLM API key
```bash
export LITELLM_TOKEN=sk-...
```
Get your key from the Duke AI Gateway dashboard at **ai-gateway.duke.edu** (log in with your Duke NetID).

### 3. Run the web server (Terminal 1)
```bash
python app.py
```
Open http://127.0.0.1:5000 in your browser.

### 4. Add a plant via the UI
- Enter a plant name, type (e.g. "Pothos"), and optional notes
- Click **"Generate Thresholds with AI"** — the LLM will create personalized thresholds
- Click **"Monitor"** to set the plant as active

### 5. Run the controller service (Terminal 2)
```bash
python controller.py
```

Or run a quick demo that cycles through all LED statuses:
```bash
python controller.py --demo
```

---

## How It Works

### Phase 1 — Personalization (Setup)
1. User submits plant profile via web form
2. Flask calls the Anthropic API with plant type + notes
3. LLM returns JSON thresholds: `overwatered_max`, `healthy_min`, `healthy_max`, `almost_time_min`, `needs_water_min`, `check_interval_minutes`, `care_notes`
4. Thresholds are stored in SQLite

### Phase 2 — Continuous Monitoring (Runtime)
1. `controller.py` fetches active plant + thresholds from Flask via `GET /api/active-plant`
2. `MockSensor` generates a drifting ADC value (0=wet, 1023=dry)
3. `classify_moisture()` compares value to thresholds
4. LED status is printed with ANSI colors (simulates GPIO)
5. Reading is POSTed to `POST /api/log`
6. UI polls `GET /api/status` every 5 seconds and updates live

### LED Color Mapping
| Status | Color | ADC Range |
|---|---|---|
| Overwatered | 🟠 Orange | < overwatered_max |
| Watered/Healthy | 🟢 Green | healthy_min–healthy_max |
| Almost Time | 🟡 Yellow | healthy_max–almost_time_min |
| Needs Water | 🔴 Red | > needs_water_min |

---

## REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/plants` | Create plant + generate LLM thresholds |
| POST | `/api/plants/:id/activate` | Set active plant |
| DELETE | `/api/plants/:id` | Remove plant |
| GET | `/api/active-plant` | Controller fetches current plant + thresholds |
| POST | `/api/log` | Controller posts sensor reading |
| GET | `/api/status` | UI polls for latest status + history |
| GET | `/api/history/:id` | Full reading history for a plant |

---

## Midterm Requirements Checklist

-  **Device Connectivity** — Two processes (`app.py` + `controller.py`) communicate via REST API over HTTP
-  **LLM Data Enrichment** — Anthropic API generates plant-specific watering thresholds
-  **Visual Presentation** — Real-time dashboard with LED orb, moisture bar, history chart
-  **Design Diagrams** — See `531_I-5_Systems_Diagram.png` (submitted separately)

---

## Real Hardware (Optional)
To run on an actual Raspberry Pi with a real sensor:

1. Replace `MockSensor.read()` in `controller.py` with real MCP3008 SPI reads using `spidev`
2. Replace `set_led()` with actual GPIO calls using `RPi.GPIO` or `rpi_ws281x`
3. No changes needed to `app.py` or the database

```python
# Real sensor example (replace MockSensor.read):
import spidev
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 1350000

def read_adc(channel=0):
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    return ((adc[1] & 3) << 8) + adc[2]
```
