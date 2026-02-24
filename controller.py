"""
ForgetMeNot - Pi Controller Service
====================================
Simulates the Raspberry Pi monitoring loop.
- Fetches active plant + thresholds from the Flask web server via REST API
- Generates mock ADC sensor readings (0–1023) with realistic drift
- Classifies plant moisture status
- "Controls" LED (prints color + logs)
- POSTs readings back to the web server for storage and UI display

Run this in a SEPARATE terminal from app.py:
    python controller.py

Communication Protocol: REST API (HTTP) — satisfies the midterm's 
"two device/process communication" requirement.
"""

import time
from dotenv import load_dotenv
load_dotenv()
import random
import requests
import argparse
from datetime import datetime

# ─── Configuration ─────────────────────────────────────────────────────────────
SERVER_URL = "http://127.0.0.1:5000"
DEFAULT_POLL_INTERVAL = 10  # seconds between readings (overridden by thresholds)

# ANSI color codes for terminal LED simulation
LED_COLORS = {
    "watered":    "\033[92m",   # Green
    "almost_time": "\033[93m",  # Yellow
    "needs_water": "\033[91m",  # Red
    "overwatered": "\033[33m",  # Orange
    "unknown":    "\033[95m",   # Purple
    "reset":      "\033[0m",
}

LED_DISPLAY = {
    "watered":     "🟢 GREEN  — Soil moisture is healthy. No action needed.",
    "almost_time": "🟡 YELLOW — Moisture dropping. Water soon.",
    "needs_water": "🔴 RED    — Soil is dry! Plant needs water now.",
    "overwatered": "🟠 ORANGE — Soil too wet. Risk of root rot.",
    "unknown":     "🟣 PURPLE — Error: unable to classify moisture.",
}


# ─── Sensor Simulation ─────────────────────────────────────────────────────────

class MockSensor:
    """
    Simulates a capacitive soil moisture sensor with realistic drift.
    
    ADC range: 0–1023
    - Low values (~100–300) = very wet
    - Mid values (~400–650) = healthy range
    - High values (~700–900) = dry
    - Very high (>900) = extremely dry

    Sensor drifts upward over time (soil drying) with occasional noise.
    Call water() to reset moisture level.
    """

    def __init__(self, initial_value: int = None):
        # Start in a random but reasonable zone
        self.value = initial_value if initial_value else random.randint(350, 550)
        self.drift_rate = random.uniform(3, 8)   # ADC units per reading (drying)
        self.noise_range = 15                      # ±15 ADC units of noise

    def read(self) -> int:
        """Return a noisy sensor reading and apply drying drift."""
        noise = random.uniform(-self.noise_range, self.noise_range)
        self.value += self.drift_rate + noise
        self.value = max(0, min(1023, self.value))
        return int(self.value)

    def water(self):
        """Simulate watering — resets moisture to wet range."""
        self.value = random.randint(80, 250)
        print(f"\n  💧 [SIMULATED WATERING] Sensor reset to {int(self.value)}\n")


# ─── Classification Logic ──────────────────────────────────────────────────────

def classify_moisture(raw: int, thresholds: dict) -> str:
    """
    Classify a raw ADC reading against the plant's stored thresholds.
    
    ADC: lower = wetter, higher = drier
    
    Zones (example for a typical houseplant):
        0   ──── overwatered_max ──── healthy_min ──── healthy_max ──── almost_time_min ──── needs_water_min ──── 1023
        [overwatered]               [  healthy  ]                      [almost time]         [needs water]
    """
    ow_max  = thresholds["overwatered_max"]
    h_min   = thresholds["healthy_min"]
    h_max   = thresholds["healthy_max"]
    at_min  = thresholds["almost_time_min"]

    if raw < ow_max:
        return "overwatered"
    elif raw <= h_max:
        return "watered"
    elif raw <= at_min:
        return "almost_time"
    else:
        return "needs_water"


def set_led(status: str):
    """Print a colored LED status to the terminal (simulates GPIO output)."""
    color = LED_COLORS.get(status, LED_COLORS["unknown"])
    reset = LED_COLORS["reset"]
    message = LED_DISPLAY.get(status, LED_DISPLAY["unknown"])
    print(f"  LED: {color}{message}{reset}")


# ─── Main Controller Loop ──────────────────────────────────────────────────────

def run_controller(auto_water_threshold: int = 850, demo_mode: bool = False):
    """
    Main monitoring loop. Mirrors the pseudocode from the system architecture doc.
    
    auto_water_threshold: if raw ADC exceeds this, auto-simulate a watering event
    demo_mode: if True, cycles through all statuses quickly for demonstration
    """
    print("\n" + "═" * 60)
    print("  🌿 ForgetMeNot — Pi Controller Service")
    print("  Communicating with Flask server at", SERVER_URL)
    print("═" * 60)

    sensor = MockSensor()
    poll_interval = DEFAULT_POLL_INTERVAL

    # ── Demo mode: cycle through all statuses ──────────────────────────────────
    if demo_mode:
        print("\n  [DEMO MODE] Cycling through all statuses...\n")
        fake_readings = [
            (120, "overwatered"),
            (420, "watered"),
            (650, "almost_time"),
            (850, "needs_water"),
        ]
        try:
            resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)
            plant_data = resp.json()
            plant_id = plant_data["plant"]["id"]
        except Exception as e:
            print(f"  ⚠ Could not reach server: {e}")
            print("  Make sure app.py is running and a plant is activated.\n")
            return

        for raw, status in fake_readings:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"\n  [{ts}] Raw ADC: {raw:>4}  →  Status: {status.upper()}")
            set_led(status)
            try:
                requests.post(f"{SERVER_URL}/api/log", json={
                    "plant_id": plant_id,
                    "raw_value": raw,
                    "status": status
                }, timeout=5)
                print("  ✓ Reading logged to server.")
            except Exception as e:
                print(f"  ✗ Log failed: {e}")
            time.sleep(2)
        print("\n  [DEMO MODE] Complete.\n")
        return

    # ── Normal polling loop ────────────────────────────────────────────────────
    consecutive_errors = 0
    loop_count = 0

    while True:
        loop_count += 1
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n{'─' * 60}")
        print(f"  [{ts}]  Loop #{loop_count}")

        # 1. Fetch active plant + thresholds from web server
        try:
            resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)
            if resp.status_code == 404:
                print("  ⚠ No active plant set. Waiting...")
                time.sleep(poll_interval)
                continue

            plant_data = resp.json()
            plant    = plant_data["plant"]
            thresh   = plant_data["thresholds"]
            plant_id = plant["id"]

            # Update poll interval from stored thresholds (converted to seconds)
            stored_interval = thresh.get("check_interval_minutes", 1)
            poll_interval = max(5, stored_interval * 60)  # min 5 seconds for demo
            if loop_count == 1:
                print(f"  Plant:    {plant['name']} ({plant['plant_type']})")
                print(f"  Interval: every {stored_interval} min (running at {poll_interval}s for demo)")
                print(f"  Thresholds — OW: <{thresh['overwatered_max']} | "
                      f"OK: {thresh['healthy_min']}–{thresh['healthy_max']} | "
                      f"Soon: {thresh['almost_time_min']}+ | "
                      f"Dry: {thresh['needs_water_min']}+")

            consecutive_errors = 0

        except requests.exceptions.ConnectionError:
            consecutive_errors += 1
            print(f"  ✗ Cannot reach server ({consecutive_errors} consecutive failures).")
            if consecutive_errors >= 3:
                print("  Make sure app.py is running. Retrying in 15s...")
            time.sleep(15)
            continue

        except Exception as e:
            print(f"  ✗ Unexpected error fetching plant data: {e}")
            time.sleep(poll_interval)
            continue

        # 2. Read sensor
        raw = sensor.read()
        print(f"  Sensor:   Raw ADC = {raw}")

        # 3. Classify
        status = classify_moisture(raw, thresh)
        print(f"  Status:   {status.upper()}")

        # 4. Set LED
        set_led(status)

        # 5. Auto-water simulation (resets sensor when very dry)
        if raw > auto_water_threshold:
            sensor.water()

        # 6. Log reading to web server
        try:
            log_resp = requests.post(f"{SERVER_URL}/api/log", json={
                "plant_id": plant_id,
                "raw_value": raw,
                "status": status
            }, timeout=5)
            if log_resp.status_code == 200:
                print("  ✓ Reading logged to server.")
            else:
                print(f"  ✗ Log failed: {log_resp.status_code}")
        except Exception as e:
            print(f"  ✗ Could not log reading: {e}")

        time.sleep(poll_interval)


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForgetMeNot Pi Controller Service")
    parser.add_argument(
        "--demo", action="store_true",
        help="Run in demo mode: cycles through all statuses once and exits"
    )
    parser.add_argument(
        "--auto-water", type=int, default=850,
        help="ADC threshold above which the sensor auto-resets (simulates watering). Default: 850"
    )
    args = parser.parse_args()

    run_controller(auto_water_threshold=args.auto_water, demo_mode=args.demo)
