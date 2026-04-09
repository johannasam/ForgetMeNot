"""
ForgetMeNot - Pi Controller Service (Hardware Edition)
=======================================================
SIGNAL FLOW:
  Capacitive Soil Sensor (analog voltage out)
    → ADS1115 ADC (converts analog to 16-bit digital over I2C)
      → Raspberry Pi (reads digital value, classifies, drives LEDs)
        → Flask Server (logs reading, updates web UI)

The key hardware change from the original code:
  BEFORE: MCP3008 SPI ADC → 0 to 1023 range (10-bit)
  NOW:    ADS1115 I2C ADC → roughly 6000 to 28000 range (16-bit signed)

We normalize the ADS1115 values back to 0–1023 so the rest of the
app (thresholds, database, UI charts) doesn't need to change at all.
"""

import time
import board          # Adafruit Blinka - maps Pi GPIO pins to CircuitPython names
import busio          # Adafruit Blinka - handles I2C/SPI bus communication
import neopixel       # Adafruit CircuitPython NeoPixel driver
import adafruit_ads1x15.ads1115 as ADS          # ADS1115 chip driver
from adafruit_ads1x15.analog_in import AnalogIn  # Helper to read a single ADC channel

from dotenv import load_dotenv
load_dotenv()  # Loads LITELLM_TOKEN from your .env file

import requests   # For making HTTP calls to the Flask server
import argparse   # For --demo flag on the command line
from datetime import datetime


# =============================================================================
# CONFIGURATION
# =============================================================================

# Where your Flask app.py server is running.
# If app.py is on the SAME Pi as this script, use 127.0.0.1.
# If app.py is on your LAPTOP, replace with your laptop's local IP, e.g.:
#   SERVER_URL = "http://192.168.1.42:5000"
SERVER_URL = "http://127.0.0.1:5000"

DEFAULT_POLL_INTERVAL = 10  # seconds between sensor readings (fallback value)


# =============================================================================
# NEOPIXEL CONFIGURATION
# =============================================================================

NEOPIXEL_PIN = board.D18  # GPIO 18 — the standard PWM pin for NeoPixels on Pi
NUM_PIXELS   = 60         # Your strip has 60 LEDs
BRIGHTNESS   = 0.3        # 0.0 to 1.0. At 0.3 the 60 LEDs draw ~150mA — safe for Pi's 5V pin.
                          # Increase to 1.0 only if using an external 5V power supply.


# =============================================================================
# SENSOR CALIBRATION
# =============================================================================
# The ADS1115 returns signed 16-bit integers (up to ±32767).
# Capacitive sensors output LOWER voltage when WET, HIGHER when DRY.
# So: lower raw value = wetter soil, higher raw value = drier soil.
#
# YOU MUST CALIBRATE THESE FOR YOUR SPECIFIC SENSOR:
#   1. Run:  python calibrate.py
#   2. Hold sensor in open AIR for 10s → that reading is DRY_RAW
#   3. Dip sensor in a cup of WATER for 10s → that reading is WET_RAW
#   4. Paste those numbers below.
#
# These defaults are typical starting values — your sensor will differ slightly.

WET_RAW = 10000   # Raw ADS1115 value when sensor is submerged in water (fully wet)
DRY_RAW = 27000   # Raw ADS1115 value when sensor is in open air (completely dry)


# =============================================================================
# HARDWARE INITIALIZATION
# =============================================================================

def init_hardware():
    """
    Set up the I2C bus, ADS1115 ADC, and NeoPixel strip.

    I2C wiring (ADS1115 → Raspberry Pi):
      ADS1115 VDD  → Pi 3.3V  (Pin 1)
      ADS1115 GND  → Pi GND   (Pin 6)
      ADS1115 SDA  → Pi GPIO2 (Pin 3)
      ADS1115 SCL  → Pi GPIO3 (Pin 5)

    Moisture sensor wiring (Sensor → ADS1115):
      Sensor VCC   → ADS1115 VDD
      Sensor GND   → ADS1115 GND
      Sensor AOUT  → ADS1115 A0   ← the analog signal wire

    NeoPixel wiring (Strip → Pi):
      Strip 5V     → Pi 5V    (Pin 2)
      Strip GND    → Pi GND   (Pin 9)
      Strip DIN    → Pi GPIO18 (Pin 12)
    """
    # Initialize the I2C bus using the Pi's hardware I2C pins (GPIO2=SDA, GPIO3=SCL)
    i2c = busio.I2C(board.SCL, board.SDA)

    # Create the ADS1115 object — this talks to the chip at its default I2C address (0x48)
    ads = ADS.ADS1115(i2c)

    # Set up channel A0 — this is where your moisture sensor's analog output is connected
    # AnalogIn gives us both .value (raw integer) and .voltage (converted to volts)
    chan = AnalogIn(ads, ADS.P0)

    # Initialize the NeoPixel strip
    # auto_write=False means we control exactly when the strip updates (more efficient)
    # pixel_order=GRB because most NeoPixel strips use Green-Red-Blue byte order, not RGB
    pixels = neopixel.NeoPixel(
        NEOPIXEL_PIN,
        NUM_PIXELS,
        brightness=BRIGHTNESS,
        auto_write=False,
        pixel_order=neopixel.GRB
    )

    # Start with all LEDs off
    pixels.fill((0, 0, 0))
    pixels.show()  # .show() pushes the color data out to the strip (needed when auto_write=False)

    return chan, pixels


# =============================================================================
# ANALOG-TO-DIGITAL CONVERSION + NORMALIZATION
# =============================================================================

def read_sensor_normalized(chan) -> tuple[int, int]:
    """
    This is where the A/D conversion result gets processed.

    WHAT'S HAPPENING PHYSICALLY:
      1. The capacitive sensor detects soil moisture as a change in capacitance.
      2. That capacitance change shifts the sensor's output VOLTAGE (analog signal).
      3. The ADS1115 samples that voltage and converts it to a 16-bit INTEGER (digital).
         - This is the actual Analog-to-Digital Conversion (ADC) step.
         - chan.value reads the digital result over I2C from the ADS1115.
      4. We then NORMALIZE that 16-bit value down to 0–1023.
         - Why? The LLM-generated thresholds and the web UI were designed for 0–1023.
         - This way, app.py, the database, and the graphs need zero changes.

    NORMALIZATION MATH:
      normalized = (raw - WET_RAW) / (DRY_RAW - WET_RAW) * 1023

      Example with WET_RAW=10000, DRY_RAW=27000:
        raw=10000 (soaking wet) → normalized = 0
        raw=18500 (middle)      → normalized ≈ 511
        raw=27000 (bone dry)    → normalized = 1023

      Values are clamped to 0–1023 in case the sensor reads outside calibration range.

    Returns:
      (normalized, raw_ads_value)
      - normalized: 0–1023 integer sent to the server and used for classification
      - raw_ads_value: the original ADS1115 reading, printed for debugging
    """
    raw = chan.value  # Read the 16-bit digital value from ADS1115 over I2C

    # Normalize from the ADS1115's range to 0–1023
    normalized = (raw - WET_RAW) / (DRY_RAW - WET_RAW) * 1023

    # Clamp: if the sensor reads wetter than WET_RAW or drier than DRY_RAW, keep it in bounds
    normalized = int(max(0, min(1023, normalized)))

    return normalized, raw


# =============================================================================
# LED CONTROL
# =============================================================================

# Color tuples are (Red, Green, Blue) — but the strip uses GRB byte order,
# which the neopixel library handles automatically when pixel_order=GRB is set.
STATUS_COLORS = {
    "watered":     (0,   200,  0),    # Green  — soil moisture is healthy
    "almost_time": (255, 165,  0),    # Amber  — getting dry, water soon
    "needs_water": (200,   0,  0),    # Red    — dry, water now
    "overwatered": (0,    0,  200),   # Blue   — too wet, risk of root rot
    "unknown":     (128,  0,  128),   # Purple — error or no active plant
}

STATUS_LABELS = {
    "watered":     "GREEN  — Healthy moisture.",
    "almost_time": "AMBER  — Water soon.",
    "needs_water": "RED    — Water now!",
    "overwatered": "BLUE   — Too wet.",
    "unknown":     "PURPLE — Error / no plant.",
}

def set_led(pixels, status: str):
    """
    Set all 60 NeoPixels to the color corresponding to the plant's moisture status.
    pixels.fill() sets every pixel to the same color.
    pixels.show() sends the data signal down the strip to actually light them up.
    """
    color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
    pixels.fill(color)
    pixels.show()
    print(f"  LED: {STATUS_LABELS.get(status, STATUS_LABELS['unknown'])}")


def clear_leds(pixels):
    """Turn off all LEDs. Called on shutdown so the strip doesn't stay lit."""
    pixels.fill((0, 0, 0))
    pixels.show()


# =============================================================================
# MOISTURE CLASSIFICATION
# =============================================================================

def classify_moisture(normalized: int, thresholds: dict) -> str:
    """
    Map a normalized ADC reading (0–1023) to a human-readable status string.

    The thresholds were generated by the LLM in app.py when you created the plant.
    They follow this scale (lower = wetter):

    0 ──[overwatered_max]──[healthy_min]──[healthy_max]──[almost_time_min]── 1023
       ^                                                                    ^
      WET                                                                  DRY

    Example thresholds for a tropical houseplant:
      overwatered_max = 150   (below this = dangerously wet)
      healthy_min     = 200   (lower bound of ideal range)
      healthy_max     = 500   (upper bound of ideal range)
      almost_time_min = 650   (getting dry, water soon)
      needs_water_min = 800   (dry, water immediately)
    """
    ow_max = thresholds["overwatered_max"]
    h_max  = thresholds["healthy_max"]
    at_min = thresholds["almost_time_min"]

    if normalized < ow_max:
        return "overwatered"
    elif normalized <= h_max:
        return "watered"       # Covers both healthy_min→healthy_max range
    elif normalized <= at_min:
        return "almost_time"
    else:
        return "needs_water"


# =============================================================================
# MAIN CONTROLLER LOOP
# =============================================================================

def run_controller(demo_mode: bool = False):
    """
    Main entry point. Two modes:

    DEMO MODE (--demo flag):
      Sends 4 fake readings to the server (one per status) to test LED colors
      and server connectivity. Doesn't use the real sensor. Good for setup testing.

    NORMAL MODE:
      Continuous loop that:
        1. Fetches the active plant + LLM thresholds from Flask
        2. Reads real sensor value via ADS1115
        3. Normalizes the 16-bit ADC value to 0–1023
        4. Classifies the moisture level
        5. Sets the NeoPixel color
        6. POSTs the reading to Flask for storage and UI display
        7. Waits for the next interval (set by the LLM in the thresholds)
    """
    print("\n" + "=" * 60)
    print("  ForgetMeNot — Pi Controller (Hardware Edition)")
    print("  ADS1115 ADC + Capacitive Sensor + NeoPixel Strip")
    print("  Server:", SERVER_URL)
    print("=" * 60)

    chan, pixels = init_hardware()
    poll_interval = DEFAULT_POLL_INTERVAL

    # ------------------------------------------------------------------
    # DEMO MODE — cycles through all 4 LED colors to test wiring
    # ------------------------------------------------------------------
    if demo_mode:
        print("\n  [DEMO] Cycling through all LED colors...\n")

        # Still needs an active plant to log readings to the server
        try:
            resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)
            plant_id = resp.json()["plant"]["id"]
        except Exception as e:
            print(f"  Could not reach server: {e}")
            print("  Make sure app.py is running and a plant is activated.")
            clear_leds(pixels)
            return

        # Fake readings that hit each status zone
        demo_readings = [
            (120, "overwatered"),   # Very wet
            (420, "watered"),       # Healthy
            (650, "almost_time"),   # Getting dry
            (850, "needs_water"),   # Very dry
        ]

        for normalized, status in demo_readings:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"  [{ts}] Fake ADC: {normalized:>4}  →  {status.upper()}")
            set_led(pixels, status)  # Light up the strip

            # Log this fake reading to the server so you can see it in the web UI
            try:
                requests.post(f"{SERVER_URL}/api/log", json={
                    "plant_id": plant_id,
                    "raw_value": normalized,
                    "status": status
                }, timeout=5)
                print("  Logged to server.")
            except Exception as e:
                print(f"  Log failed: {e}")

            time.sleep(2)  # Hold each color for 2 seconds

        clear_leds(pixels)
        print("\n  [DEMO] Done. All LED colors confirmed.\n")
        return

    # ------------------------------------------------------------------
    # NORMAL MODE — real sensor readings, continuous loop
    # ------------------------------------------------------------------
    consecutive_errors = 0
    loop_count = 0

    try:
        while True:
            loop_count += 1
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n{'─' * 60}")
            print(f"  [{ts}] Loop #{loop_count}")

            # STEP 1: Fetch the active plant profile + LLM thresholds from Flask
            try:
                resp = requests.get(f"{SERVER_URL}/api/active-plant", timeout=5)

                if resp.status_code == 404:
                    # No plant activated yet — show purple and wait
                    print("  No active plant set in the web UI. Waiting...")
                    set_led(pixels, "unknown")
                    time.sleep(poll_interval)
                    continue

                plant_data = resp.json()
                plant  = plant_data["plant"]
                thresh = plant_data["thresholds"]
                plant_id = plant["id"]

                # The LLM sets how often to poll (in minutes). We convert to seconds.
                # The max(5, ...) ensures we never poll faster than every 5 seconds during testing.
                stored_interval = thresh.get("check_interval_minutes", 1)
                poll_interval = max(5, stored_interval * 60)

                # Print setup info on the first loop only
                if loop_count == 1:
                    print(f"  Plant    : {plant['name']} ({plant['plant_type']})")
                    print(f"  Interval : {stored_interval} min  (running at {poll_interval}s for demo)")
                    print(f"  Thresholds:")
                    print(f"    Overwatered if below : {thresh['overwatered_max']}")
                    print(f"    Healthy range        : {thresh['healthy_min']} – {thresh['healthy_max']}")
                    print(f"    Water soon above     : {thresh['almost_time_min']}")
                    print(f"    Water now above      : {thresh['needs_water_min']}")

                consecutive_errors = 0  # Reset error counter on success

            except requests.exceptions.ConnectionError:
                consecutive_errors += 1
                print(f"  Cannot reach Flask server ({consecutive_errors} failures in a row).")
                set_led(pixels, "unknown")
                if consecutive_errors >= 3:
                    print("  Is app.py running? Retrying in 15s...")
                    time.sleep(15)
                continue

            except Exception as e:
                print(f"  Unexpected error fetching plant data: {e}")
                time.sleep(poll_interval)
                continue

            # STEP 2 & 3: Read sensor and normalize
            # chan.value triggers the ADS1115 to sample the analog voltage and return digital int
            normalized, raw_ads = read_sensor_normalized(chan)
            print(f"  Sensor   : ADS1115 raw = {raw_ads}  →  normalized = {normalized} / 1023")

            # STEP 4: Classify moisture level against LLM thresholds
            status = classify_moisture(normalized, thresh)
            print(f"  Status   : {status.upper()}")

            # STEP 5: Update NeoPixel strip color
            set_led(pixels, status)

            # STEP 6: POST reading to Flask server for storage and UI display
            # We send the NORMALIZED value (not raw ADS1115) so the UI and graphs
            # stay on a consistent 0–1023 scale regardless of ADC hardware.
            try:
                log_resp = requests.post(f"{SERVER_URL}/api/log", json={
                    "plant_id": plant_id,
                    "raw_value": normalized,  # normalized to 0–1023
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
        # Clean shutdown on Ctrl+C — always turn off LEDs
        print("\n\n  Ctrl+C received — turning off LEDs and exiting.")
        clear_leds(pixels)


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ForgetMeNot Pi Controller (Hardware)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Cycle through all 4 LED colors once using fake values, then exit"
    )
    args = parser.parse_args()
    run_controller(demo_mode=args.demo)