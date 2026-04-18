"""
ForgetMeNot - Plant Monitoring Web Server
Flask app serving the UI and REST API for the Pi Controller Service
"""

import os
from dotenv import load_dotenv
load_dotenv()

import json
import sqlite3
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "forgetmenot.db")

# ─── Database Setup ────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            plant_type TEXT NOT NULL,
            location TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS thresholds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL,
            overwatered_max INTEGER NOT NULL,
            healthy_min INTEGER NOT NULL,
            healthy_max INTEGER NOT NULL,
            almost_time_min INTEGER NOT NULL,
            needs_water_min INTEGER NOT NULL,
            check_interval_minutes INTEGER NOT NULL,
            care_notes TEXT,
            sunlight TEXT,
            humidity TEXT,
            temperature TEXT,
            soil_type TEXT,
            raw_llm_response TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        );

        CREATE TABLE IF NOT EXISTS sensor_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL,
            raw_value INTEGER NOT NULL,
            status TEXT NOT NULL,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        );
    """)
    conn.commit()
    conn.close()

# ─── LLM Integration ───────────────────────────────────────────────────────────

def call_llm_for_thresholds(plant_type: str, notes: str) -> dict:
    """
    Calls the Duke LiteLLM Gateway to generate plant-specific watering thresholds
    AND care recommendations (sunlight, humidity, temperature, soil type).

    The ADC returns values 0-1023 where LOWER = wetter, HIGHER = drier.
    """
    from openai import OpenAI

    Token = os.getenv("LITELLM_TOKEN")
    ChatGPT = OpenAI(api_key=Token, base_url="https://litellm.oit.duke.edu/v1")

    prompt = f"""You are an expert botanist and IoT systems engineer.

A capacitive soil moisture sensor returns values from 0 to 1023 via an ADC:
- LOW values (near 0) = very wet soil
- HIGH values (near 1023) = very dry soil

Generate watering thresholds AND care recommendations for this plant:

Plant Type: {plant_type}
Additional Notes: {notes if notes else "None provided"}

Return ONLY a valid JSON object with these exact fields:
{{
  "overwatered_max": <int, ADC value above which soil is dangerously wet>,
  "healthy_min": <int, lower bound of healthy moisture ADC range>,
  "healthy_max": <int, upper bound of healthy moisture ADC range>,
  "almost_time_min": <int, ADC value where watering will be needed soon>,
  "needs_water_min": <int, ADC value where plant urgently needs water>,
  "check_interval_minutes": <int, how often to poll in minutes>,
  "care_notes": "<string, 1-2 sentences of watering care tips>",
  "sunlight": "<string, sunlight requirements in 1 sentence>",
  "humidity": "<string, humidity preference in 1 sentence>",
  "temperature": "<string, ideal temperature range in 1 sentence>",
  "soil_type": "<string, recommended soil type in 1 sentence>"
}}

Remember: lower ADC = wetter. Do not include any explanation, only the JSON object."""

    response = ChatGPT.chat.completions.create(
        model="gpt-5-nano",
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    thresholds = json.loads(raw)
    thresholds["raw_llm_response"] = response.choices[0].message.content
    return thresholds

# ─── Web Routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    conn = get_db()
    plants = conn.execute("SELECT * FROM plants ORDER BY created_at DESC").fetchall()
    active = conn.execute("SELECT * FROM plants WHERE is_active = 1").fetchone()
    conn.close()
    return render_template("index.html", plants=plants, active_plant=active)

@app.route("/plant/<int:plant_id>")
def plant_detail(plant_id):
    conn = get_db()
    plant = conn.execute("SELECT * FROM plants WHERE id = ?", (plant_id,)).fetchone()
    thresholds = conn.execute(
        "SELECT * FROM thresholds WHERE plant_id = ? ORDER BY created_at DESC LIMIT 1",
        (plant_id,)
    ).fetchone()
    logs = conn.execute(
        "SELECT * FROM sensor_log WHERE plant_id = ? ORDER BY timestamp DESC LIMIT 50",
        (plant_id,)
    ).fetchall()
    conn.close()
    return render_template("plant_detail.html", plant=plant, thresholds=thresholds, logs=logs)

# ─── REST API ──────────────────────────────────────────────────────────────────

@app.route("/api/plants", methods=["POST"])
def create_plant():
    """Create a plant profile and generate LLM thresholds + care guide."""
    data = request.json
    name       = data.get("name", "").strip()
    plant_type = data.get("plant_type", "").strip()
    location   = data.get("location", "").strip()
    notes      = data.get("notes", "").strip()

    if not name or not plant_type:
        return jsonify({"error": "name and plant_type are required"}), 400

    try:
        thresholds = call_llm_for_thresholds(plant_type, notes)
    except Exception as e:
        return jsonify({"error": f"LLM call failed: {str(e)}"}), 500

    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO plants (name, plant_type, location, notes) VALUES (?, ?, ?, ?)",
        (name, plant_type, location, notes)
    )
    plant_id = cursor.lastrowid

    conn.execute("""
        INSERT INTO thresholds
        (plant_id, overwatered_max, healthy_min, healthy_max, almost_time_min, needs_water_min,
         check_interval_minutes, care_notes, sunlight, humidity, temperature, soil_type, raw_llm_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        plant_id,
        thresholds["overwatered_max"],
        thresholds["healthy_min"],
        thresholds["healthy_max"],
        thresholds["almost_time_min"],
        thresholds["needs_water_min"],
        thresholds["check_interval_minutes"],
        thresholds.get("care_notes", ""),
        thresholds.get("sunlight", ""),
        thresholds.get("humidity", ""),
        thresholds.get("temperature", ""),
        thresholds.get("soil_type", ""),
        thresholds.get("raw_llm_response", "")
    ))

    conn.commit()
    conn.close()
    return jsonify({"plant_id": plant_id, "thresholds": thresholds}), 201

@app.route("/api/plants/<int:plant_id>/activate", methods=["POST"])
def activate_plant(plant_id):
    """Set a plant as the active monitored plant."""
    conn = get_db()
    conn.execute("UPDATE plants SET is_active = 0")
    conn.execute("UPDATE plants SET is_active = 1 WHERE id = ?", (plant_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "activated", "plant_id": plant_id})

@app.route("/api/plants/<int:plant_id>", methods=["DELETE"])
def delete_plant(plant_id):
    conn = get_db()
    conn.execute("DELETE FROM sensor_log WHERE plant_id = ?", (plant_id,))
    conn.execute("DELETE FROM thresholds WHERE plant_id = ?", (plant_id,))
    conn.execute("DELETE FROM plants WHERE id = ?", (plant_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted"})

@app.route("/api/plants/<int:plant_id>/thresholds", methods=["GET"])
def get_plant_thresholds(plant_id):
    """Returns the latest thresholds including care guide for a specific plant."""
    conn = get_db()
    t = conn.execute(
        "SELECT * FROM thresholds WHERE plant_id = ? ORDER BY created_at DESC LIMIT 1",
        (plant_id,)
    ).fetchone()
    conn.close()
    if not t:
        return jsonify({"error": "no thresholds found"}), 404
    return jsonify(dict(t))

@app.route("/api/active-plant", methods=["GET"])
def get_active_plant():
    """Used by the Pi Controller to get the current active plant + thresholds."""
    conn = get_db()
    plant = conn.execute("SELECT * FROM plants WHERE is_active = 1").fetchone()
    if not plant:
        conn.close()
        return jsonify({"error": "no active plant"}), 404

    thresholds = conn.execute(
        "SELECT * FROM thresholds WHERE plant_id = ? ORDER BY created_at DESC LIMIT 1",
        (plant["id"],)
    ).fetchone()
    conn.close()
    return jsonify({
        "plant": dict(plant),
        "thresholds": dict(thresholds) if thresholds else None
    })

@app.route("/api/log", methods=["POST"])
def log_reading():
    """Receives a sensor reading + status from the Pi Controller and stores it."""
    data = request.json
    plant_id  = data.get("plant_id")
    raw_value = data.get("raw_value")
    status    = data.get("status")

    if not all([plant_id, raw_value is not None, status]):
        return jsonify({"error": "plant_id, raw_value, and status are required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO sensor_log (plant_id, raw_value, status) VALUES (?, ?, ?)",
        (plant_id, raw_value, status)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "logged"})

@app.route("/api/status", methods=["GET"])
def get_current_status():
    """Returns the most recent sensor reading for the active plant (for UI polling)."""
    conn = get_db()
    plant = conn.execute("SELECT * FROM plants WHERE is_active = 1").fetchone()
    if not plant:
        conn.close()
        return jsonify({"error": "no active plant"}), 404

    log = conn.execute(
        "SELECT * FROM sensor_log WHERE plant_id = ? ORDER BY timestamp DESC LIMIT 1",
        (plant["id"],)
    ).fetchone()

    history = conn.execute(
        "SELECT raw_value, status, timestamp FROM sensor_log WHERE plant_id = ? ORDER BY timestamp DESC LIMIT 20",
        (plant["id"],)
    ).fetchall()

    thresholds = conn.execute(
        "SELECT * FROM thresholds WHERE plant_id = ? ORDER BY created_at DESC LIMIT 1",
        (plant["id"],)
    ).fetchone()
    conn.close()

    return jsonify({
        "plant":      dict(plant),
        "latest":     dict(log) if log else None,
        "history":    [dict(r) for r in history],
        "thresholds": dict(thresholds) if thresholds else None
    })

@app.route("/api/history/<int:plant_id>", methods=["GET"])
def get_history(plant_id):
    conn = get_db()
    logs = conn.execute(
        "SELECT raw_value, status, timestamp FROM sensor_log WHERE plant_id = ? ORDER BY timestamp DESC LIMIT 100",
        (plant_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in logs])

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000, host='0.0.0.0')
