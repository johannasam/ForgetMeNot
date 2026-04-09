"""
ForgetMeNot - Sensor Calibration Helper
========================================
Run this BEFORE controller.py to find your sensor's WET_RAW and DRY_RAW values.

The ADS1115 reads the analog voltage from the capacitive sensor and converts it
to a 16-bit signed integer. Because every sensor is manufactured slightly
differently, you need to measure YOUR sensor's specific output range.

HOW TO USE:
  1. Run: python calibrate.py
  2. Hold the sensor in OPEN AIR for ~10 seconds and note the printed value
     → This is your DRY_RAW
  3. Dip the sensor in a cup of WATER for ~10 seconds and note the printed value
     → This is your WET_RAW
  4. Open controller.py and update the two constants at the top:
       WET_RAW = <your water reading>
       DRY_RAW = <your air reading>

EXPECTED BEHAVIOR:
  - Air reading should be higher (e.g. 25000–28000)
  - Water reading should be lower (e.g. 8000–12000)
  - If the values are reversed, your sensor's wiring is flipped — swap VCC and GND.
"""

import time
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# Initialize I2C bus and ADS1115 (same setup as controller.py)
i2c  = busio.I2C(board.SCL, board.SDA)
ads  = ADS.ADS1115(i2c)
chan = AnalogIn(ads, ADS.P0)  # Sensor connected to channel A0

print("ADS1115 Soil Moisture Calibration")
print("=" * 45)
print("Reads every second. Press Ctrl+C to stop.")
print()
print(f"{'Time':>10}  {'Raw (digital)':>14}  {'Voltage':>10}")
print("-" * 45)

try:
    while True:
        raw     = chan.value    # 16-bit integer from ADS1115 (the ADC output)
        voltage = chan.voltage  # Converted to volts — useful to verify sensor is powered correctly
        ts      = time.strftime("%H:%M:%S")
        print(f"{ts:>10}  {raw:>14}  {voltage:>9.3f}V")
        time.sleep(1.0)

except KeyboardInterrupt:
    print()
    print("Done. Copy your WET_RAW and DRY_RAW values into controller.py.")