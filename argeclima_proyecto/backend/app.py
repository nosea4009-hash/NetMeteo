#!/usr/bin/env python3
"""
ArgeClima backend
------------------
Junta datos de estaciones meteorologicas cada REFRESH_SECONDS y los sirve
como JSON en /api/estaciones para que la pagina HTML los consuma con fetch().

Las API keys (Wunderground, OHMC si la piden) viven SOLO como variables de
entorno de este servidor, nunca en el HTML publico.

Correr local:
    pip install -r requirements.txt
    cp .env.example .env   # y completar valores
    export $(cat .env | xargs)   # o usar python-dotenv / la UI de tu hosting
    python3 app.py

Deploy (Render, Railway, Fly.io, PythonAnywhere, etc.): ver README.md
"""

import os
import time
import threading
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify
from flask_cors import CORS
import requests

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# --- CORS: solo permite que tu frontend (el dominio donde alojes el HTML) llame a esta API ---
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGIN}})

REFRESH_SECONDS = int(os.environ.get("REFRESH_SECONDS", "900"))  # 15 min por defecto

WUNDERGROUND_API_KEY = os.environ.get("WUNDERGROUND_API_KEY")
WUNDERGROUND_STATION_IDS = [
    s.strip() for s in os.environ.get("WUNDERGROUND_STATION_IDS", "").split(",") if s.strip()
]

_cache = {"generated_at": None, "stations": []}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 1) WEATHER UNDERGROUND -- funcional, requiere API key + IDs de estacion PWS
# ---------------------------------------------------------------------------
def fetch_wunderground():
    stations = []
    if not WUNDERGROUND_API_KEY or not WUNDERGROUND_STATION_IDS:
        return stations
    for sid in WUNDERGROUND_STATION_IDS:
        try:
            r = requests.get(
                "https://api.weather.com/v2/pws/observations/current",
                params={"stationId": sid, "format": "json", "units": "m", "apiKey": WUNDERGROUND_API_KEY},
                timeout=10,
            )
            r.raise_for_status()
            obs = r.json()["observations"][0]
            metric = obs["metric"]
            stations.append({
                "name": obs.get("neighborhood") or sid,
                "province": os.environ.get(f"PROVINCE_{sid}", ""),
                "source": "Wunderground",
                "max": metric.get("tempHigh", metric.get("temp")),
                "min": metric.get("tempLow", metric.get("temp")),
            })
        except Exception as e:
            app.logger.warning(f"Wunderground {sid}: {e}")
    return stations


# ---------------------------------------------------------------------------
# 2) SMN -- el portal es de descarga manual, no una API con key.
#    AJUSTAR: si encontras una URL de descarga directa y estable de un
#    archivo que el SMN actualiza solo (inspeccionando la pestaña Network
#    de https://www.smn.gob.ar/descarga-de-datos), setealá en SMN_CSV_URL.
# ---------------------------------------------------------------------------
def fetch_smn():
    url = os.environ.get("SMN_CSV_URL")
    if not url:
        return []
    stations = []
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        for line in r.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(";")  # AJUSTAR separador/columnas al formato real
            if len(parts) < 3:
                continue
            try:
                stations.append({
                    "name": parts[0].strip(),
                    "province": "",
                    "source": "SMN",
                    "max": float(parts[1].replace(",", ".")),
                    "min": float(parts[2].replace(",", ".")),
                })
            except ValueError:
                continue
    except Exception as e:
        app.logger.warning(f"SMN: {e}")
    return stations


# ---------------------------------------------------------------------------
# 3) INTA / Red SIGA -- es una SPA, no hay API documentada publicamente.
#    AJUSTAR: abrí https://siga.inta.gob.ar/#/data, F12 -> Network -> Fetch/XHR,
#    navegá el mapa/tabla y copiá la URL JSON real que usa a INTA_API_URL.
# ---------------------------------------------------------------------------
def fetch_inta():
    url = os.environ.get("INTA_API_URL")
    if not url:
        return []
    stations = []
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        for item in data:  # AJUSTAR a la forma real del JSON devuelto
            tmax, tmin = item.get("temp_max"), item.get("temp_min")
            if tmax is None or tmin is None:
                continue
            stations.append({
                "name": item.get("nombre", "?"),
                "province": item.get("provincia", ""),
                "source": "INTA",
                "max": tmax,
                "min": tmin,
            })
    except Exception as e:
        app.logger.warning(f"INTA: {e}")
    return stations


# ---------------------------------------------------------------------------
# 4) OHMC -- el link que diste es el panel /admin/ de Django, de uso interno,
#    no apto para scraping publico. Pedile al OHMC un endpoint/API de lectura
#    y setealo en OHMC_API_URL (+ OHMC_API_KEY si te la dan).
# ---------------------------------------------------------------------------
def fetch_ohmc():
    url = os.environ.get("OHMC_API_URL")
    if not url:
        return []
    stations = []
    try:
        headers = {}
        key = os.environ.get("OHMC_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        for item in data:  # AJUSTAR a la forma real del JSON devuelto
            tmax, tmin = item.get("tmax"), item.get("tmin")
            if tmax is None or tmin is None:
                continue
            stations.append({
                "name": item.get("estacion", "?"),
                "province": item.get("provincia", "Cordoba"),
                "source": "OHMC",
                "max": tmax,
                "min": tmin,
            })
    except Exception as e:
        app.logger.warning(f"OHMC: {e}")
    return stations


def refresh_cache_once():
    stations = fetch_wunderground() + fetch_smn() + fetch_inta() + fetch_ohmc()
    with _cache_lock:
        _cache["generated_at"] = datetime.now(timezone.utc).isoformat()
        _cache["stations"] = stations
    app.logger.info(f"Cache actualizado: {len(stations)} estaciones")


def refresh_loop():
    while True:
        try:
            refresh_cache_once()
        except Exception as e:
            app.logger.error(f"Error refrescando cache: {e}")
        time.sleep(REFRESH_SECONDS)


@app.route("/api/estaciones")
def api_estaciones():
    with _cache_lock:
        return jsonify(_cache)


@app.route("/")
def index():
    return "ArgeClima backend OK. Datos en /api/estaciones", 200


_refresh_thread_started = False


def start_refresh_thread():
    global _refresh_thread_started
    if not _refresh_thread_started:
        threading.Thread(target=refresh_loop, daemon=True).start()
        _refresh_thread_started = True


start_refresh_thread()  # arranca tambien cuando gunicorn importa el modulo

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
