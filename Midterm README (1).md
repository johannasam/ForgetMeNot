# 🌱 ForgetMeNot: SmartPlant Monitor

Smart Plant Monitor that keeps you and your household zen!

---

## Architecture

```
[ Web Interface ]  →  [ LLM API ]  →  [ SQLite DB ]
                                           ↑  ↓
[ Mock Sensor ]  →  [ Controller Service (REST) ]  →  [ LED Output (terminal) ]
```

Two processes communicate via **REST API** :

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
---

## How It Works

### Phase 1 — Personalization (Setup)
1. User submits plant profile via web form
2. Flask calls the Duke Gateway OpenAi API with plant type + notes
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
