"""
ForgetMeNot - Pi Controller Service (Hardware Edition)
=======================================================
SIGNAL FLOW:
  Capacitive Soil Sensor (analog voltage)
    → ADS1115 ADC (converts analog to 16-bit digital over I2C)
      → Raspberry Pi (reads digital value, classifies, drives LEDs)
        → Flask Server on Mac (logs reading, updates web UI)

Run this on the Pi in a terminal:
  python controller.py

For a quick LED + server test before using the real sensor:
  python controller.py --demo

BEFORE RUNNING:
  1. Run calibrate.py and update WET_RAW and DRY_RAW below
  2. Find your Mac's IP with: ipconfig getifaddr en0
     then update SERVER_URL below
"""

import time
import board
import busio
import neopixel
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.ads1115 import ADS1115
from adafruit_ads1x15.analog_in import AnalogIn

from dotenv import load_dotenv
load_dotenv()

import requests
import argparse
from datetime import datetime


# =============================================================================
# CONFIGURATION — update these before running
# =============================================================================

# Your Mac's local IP address. Find it by running: ipconfig getifaddr en0
# Both Mac and Pi must be on the same WiFi network.
SERVER_URL = "http://172.28.116.152:5000"  # e.g. "http://192.168.1.42:5000"

DEFAULT_POLL_INTERVAL = 10  # seconds between readings (fallback)


# =============================================================================
# NEOPIXEL CONFIGURATION
# =============================================================================

NEOPIXEL_PIN = board.D18  # GPIO 18 — standard PWM pin for NeoPixels on Pi
NUM_PIXELS   = 60         # Your strip has 60 LEDs
BRIGHTNESS   = 0.3        # 0.0 to 1.0. At 0.3, 60 LEDs draw ~150mA — safe for Pi's 5V pin.
                          # Only increase to 1.0 if using an external 5V power supply.


# =============================================================================
# SENSOR CALIBRATION — update after running calibrate.py
# =============================================================================
# The ADS1115 returns signed 16-bit integers.
# Capacitive sensors output LOWER voltage when WET, HIGHER when DRY.
# Lower raw value = wetter, higher raw value = drier.
#
# Run calibrate.py first, then replace these with your actual readings:

WET_RAW = 8560
DRY_RAW = 17720


# =============================================================================
# HARDWARE INITIALIZATION
# =============================================================================

def init_hardware():
    """
    Initialize I2C bus, ADS1115 ADC, and NeoPixel strip.

    WIRING — ADS1115 to Raspberry Pi:
      ADS1115 VDD  →  Pi 3.3V   (Pin 1)
      ADS1115 GND  →  Pi GND    (Pin 6)
      ADS1115 SDA  →  Pi GPIO2  (Pin 3)
      ADS1115 SCL  →  Pi GPIO3  (Pin 5)

    WIRING — Moisture Sensor to ADS1115:
      Sensor VCC   →  ADS1115 VDD
      Sensor GND   →  ADS1115 GND
      Sensor AOUT  →  ADS1115 A0   ← the analog signal wire

    WIRING — NeoPixel Strip to Pi:
      Strip 5V     →  Pi 5V     (Pin 2)
      Strip GND    →  Pi GND    (Pin 9)
      Strip DIN    →  Pi GPIO18 (Pin 12)
    """
    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS.ADS1115(i2c)
    chan = AnalogIn(ads, 0)

    pixels = neopixel.NeoPixel(
        NEOPIXEL_PIN,
        NUM_PIXELS,
        brightness=BRIGHTNESS,
        auto_write=False,
        pixel_order=neopixel.GRB
    )

    pixels.fill((0, 0, 0))
    pixels.show()

    return chan, pixels


# =============================================================================
# ANALOG TO DIGITAL CONVERSION + NORMALIZATION
# =============================================================================

def read_sensor_normalized(chan) -> tuple[int, int]:
    """
    Reads the ADS1115 and normalizes the result to 0-1023.

    WHAT'S HAPPENING:
      1. The capacitive sensor detects moisture as a change in capacitance
      2. That capacitance shifts the sensor's output VOLTAGE (analog signal)
      3. The ADS1115 converts that voltage to a 16-bit INTEGER (digital) —
         this is the actual Analog-to-Digital Conversion step
      4. chan.value reads that integer from the ADS1115 over I2C
      5. We normalize from the ADS1115's range down to 0-1023 so the rest
         of the app (thresholds, database, UI charts) needs zero changes

    NORMALIZATION MATH:
      normalized = (raw - WET_RAW) / (DRY_RAW - WET_RAW) * 1023

      Example with WET_RAW=10000, DRY_RAW=27000:
        raw = 10000 (soaking wet) → normalized =    0
        raw = 18500 (middle)      → normalized ~  511
        raw = 27000 (bone dry)    → normalized = 1023

    Returns:
      normalized  — 0 to 1023 integer, used for classification and logging
      raw         — original ADS1115 reading, printed for debugging
    """
    raw = chan.value  # Triggers ADS1115 to sample analog voltage → returns digital int

    normalized = (raw - WET_RAW) / (DRY_RAW - WET_RAW) * 1023
    normalized = int(max(0, min(1023, normalized)))

    return normalized, raw


# =============================================================================
# LED CONTROL
# =============================================================================

STATUS_COLORS = {
    "watered":     (0,   200,   0),   # Green  — healthy moisture
    "almost_time": (255, 165,   0),   # Amber  — water soon
    "needs_water": (200,   0,   0),   # Red    — water now
    "overwatered": (0,     0, 200),   # Blue   — too wet, root rot risk
    "unknown":     (128,   0, 128),   # Purple — error or no active plant
}

STATUS_LABELS = {
    "watered":     "GREEN  — Healthy moisture.",
    "almost_time": "AMBER  — Water soon.",
    "needs_water": "RED    — Water now!",
    "overwatered": "BLUE   — Too wet.",
    "unknown":     "PURPLE — Error / no plant.",
}

def set_led(pixels, status: str):
    """Set all 60 NeoPixels to the color for the given moisture status."""
    color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
    pixels.fill(color)
    pixels.show()
    print(f"  LED: {STATUS_LABELS.get(status, STATUS_LABELS['unknown'])}")


def clear_leds(pixels):
    """Turn off all LEDs. Called on shutdown."""
    pixels.fill((0, 0, 0))
    pixels.show()


# =============================================================================
# MOISTURE CLASSIFICATION
# =============================================================================

def classify_moisture(normalized: int, thresholds: dict) -> str:
    """
    Maps a normalized 0-1023 reading to a status string using the
    LLM-generated thresholds stored in the Flask server's database.

    Scale (lower = wetter, higher = drier):
    0 --[overwatered_max]--[healthy_max]--[almost_time_min]-- 1023
       ^                                                     ^
      WET                                                   DRY
    """
    ow_max = thresholds["overwatered_max"]
    h_max  = thresholds["healthy_max"]
    at_min = thresholds["almost_time_min"]

    if normalized < ow_max:
        return "overwatered"
    elif normalized <= h_max:
        return "watered"
    elif normalized <= at_min:
        return "almost_time"
    else:
        return "needs_water"


# =============================================================================
# MAIN CONTROLLER LOOP
# =============================================================================

def run_controller(demo_mode: bool = False):
    """
    Main entry point.

    DEMO MODE (python controller.py --demo):
      Sends 4 fake readings to test LED colors and server connectivity.
      Does not use the real sensor. Good for verifying wiring before planting.

    NORMAL MODE (python controller.py):
      Continuous loop:
        1. Fetch active plant + LLM thresholds from Flask on Mac
        2. Read real sensor via ADS1115
        3. Normalize the 16-bit ADC value to 0-1023
        4. Classify moisture level against thresholds
        5. Set NeoPixel strip color
        6. POST reading to Flask for storage and UI display
        7. Wait for next interval
    """
    print("\n" + "=" * 60)
    print("  ForgetMeNot — Pi Controller (Hardware Edition)")
    print("  ADS1115 ADC + Capacitive Sensor + NeoPixel Strip")
    print(f"  Server: {SERVER_URL}")
    print("=" * 60)

    chan, pixels = init_hardware()
    poll_interval = DEFAULT_POLL_INTERVAL

    # -------------------------------------------------------------------------
    # DEMO MODE
    # -------------------------------------------------------------------------
    if demo_mode:
        print("\n  [DEMO] Cycling through all LED colors...\n")

        try:
            resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)
            plant_id = resp.json()["plant"]["id"]
        except Exception as e:
            print(f"  Could not reach server: {e}")
            print("  Make sure app.py is running on your Mac and a plant is activated.")
            clear_leds(pixels)
            return

        demo_readings = [
            (120, "overwatered"),
            (420, "watered"),
            (650, "almost_time"),
            (850, "needs_water"),
        ]

        for normalized, status in demo_readings:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] Fake ADC: {normalized:>4}  →  {status.upper()}")
            set_led(pixels, status)
            try:
                requests.post(f"{SERVER_URL}/api/log", json={
                    "plant_id": plant_id,
                    "raw_value": normalized,
                    "status": status
                }, timeout=5)
                print("  Logged to server.")
            except Exception as e:
                print(f"  Log failed: {e}")
            time.sleep(2)

        clear_leds(pixels)
        print("\n  [DEMO] Done. All LED colors confirmed.\n")
        return

    # -------------------------------------------------------------------------
    # NORMAL MODE
    # -------------------------------------------------------------------------
    consecutive_errors = 0
    loop_count = 0

    try:
        while True:
            loop_count += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n{'─' * 60}")
            print(f"  [{ts}] Loop #{loop_count}")

            # STEP 1: Fetch active plant + thresholds from Flask on Mac
            try:
                resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)

                if resp.status_code == 404:
                    print("  No active plant set. Go to the web UI and activate one.")
                    set_led(pixels, "unknown")
                    time.sleep(poll_interval)
                    continue

                plant_data = resp.json()
                plant    = plant_data["plant"]
                thresh   = plant_data["thresholds"]
                plant_id = plant["id"]

                stored_interval = thresh.get("check_interval_minutes", 1)
                poll_interval = 10

                if loop_count == 1:
                    print(f"  Plant    : {plant['name']} ({plant['plant_type']})")
                    print(f"  Interval : {stored_interval} min  ({poll_interval}s for demo)")
                    print(f"  Thresholds:")
                    print(f"    Overwatered below : {thresh['overwatered_max']}")
                    print(f"    Healthy range     : {thresh['healthy_min']} – {thresh['healthy_max']}")
                    print(f"    Water soon above  : {thresh['almost_time_min']}")
                    print(f"    Water now above   : {thresh['needs_water_min']}")

                consecutive_errors = 0

            except requests.exceptions.ConnectionError:
                consecutive_errors += 1
                print(f"  Cannot reach Flask server ({consecutive_errors} failures).")
                set_led(pixels, "unknown")
                if consecutive_errors >= 3:
                    print("  Is app.py running on your Mac? Retrying in 15s...")
                    time.sleep(15)
                continue

            except Exception as e:
                print(f"  Unexpected error: {e}")
                time.sleep(poll_interval)
                continue

            # STEP 2 + 3: Read sensor and normalize ADC value
            normalized, raw_ads = read_sensor_normalized(chan)
            print(f"  Sensor   : ADS1115 raw = {raw_ads}  →  normalized = {normalized} / 1023")

            # STEP 4: Classify against LLM thresholds
            status = classify_moisture(normalized, thresh)
            print(f"  Status   : {status.upper()}")

            # STEP 5: Drive NeoPixel strip
            set_led(pixels, status)

            # STEP 6: POST to Flask — send normalized value so UI stays on 0-1023 scale
            try:
                log_resp = requests.post(f"{SERVER_URL}/api/log", json={
                    "plant_id": plant_id,
                    "raw_value": normalized,
                    "status": status
                }, timeout=5)

                if log_resp.status_code == 200:
                    print("  Logged to server.")
                else:
                    print(f"  Server returned HTTP {log_resp.status_code}")

            except Exception as e:
                print(f"  Could not log reading: {e}")

            # STEP 7: Wait before next reading
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n  Shutting down — clearing LEDs.")
        clear_leds(pixels)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForgetMeNot Pi Controller")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Cycle through all 4 LED colors with fake values, then exit"
    )
    args = parser.parse_args()
    run_controller(demo_mode=args.demo)
