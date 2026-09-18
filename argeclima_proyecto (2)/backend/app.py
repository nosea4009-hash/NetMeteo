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
import io
import time
import zipfile
import threading
import logging
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:
    AR_TZ = None

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

# Lista manual opcional: si la seteas, se usa TAL CUAL (sin descubrimiento automatico).
WUNDERGROUND_STATION_IDS = [
    s.strip() for s in os.environ.get("WUNDERGROUND_STATION_IDS", "").split(",") if s.strip()
]

# Descubrimiento automatico (para traer "la mayoria" de estaciones sin listarlas a mano)
WUNDERGROUND_AUTO_DISCOVER = os.environ.get("WUNDERGROUND_AUTO_DISCOVER", "true").lower() == "true"
WUNDERGROUND_MAX_STATIONS = int(os.environ.get("WUNDERGROUND_MAX_STATIONS", "120"))
WUNDERGROUND_DISCOVER_HOURS = float(os.environ.get("WUNDERGROUND_DISCOVER_HOURS", "24"))
# Free tier de Wunderground: 1500 llamadas/dia, 30/min. Dejamos margen de seguridad.
WUNDERGROUND_DAILY_BUDGET = int(os.environ.get("WUNDERGROUND_DAILY_BUDGET", "1400"))
WUNDERGROUND_RATE_SLEEP = 2.1  # segundos entre llamadas -> ~28/min, debajo del limite de 30/min

_cache = {"generated_at": None, "stations": []}
_cache_lock = threading.Lock()

# Grilla de puntos que cubre el pais, usada para descubrir estaciones cercanas
# a cada uno via el endpoint /v3/location/near. No es exhaustivo (Wunderground
# no ofrece "listame todas las estaciones de Argentina" en un solo llamado),
# pero con esta grilla se cubren las zonas mas pobladas de cada provincia.
ARGENTINA_GRID = [
    ("Buenos Aires (CABA)", "CABA", -34.6037, -58.3816),
    ("La Plata", "Buenos Aires", -34.9215, -57.9545),
    ("Mar del Plata", "Buenos Aires", -38.0055, -57.5426),
    ("Bahia Blanca", "Buenos Aires", -38.7183, -62.2663),
    ("Junin", "Buenos Aires", -34.5820, -60.9600),
    ("Cordoba", "Cordoba", -31.4201, -64.1888),
    ("Rio Cuarto", "Cordoba", -33.1232, -64.3492),
    ("Rosario", "Santa Fe", -32.9468, -60.6393),
    ("Santa Fe", "Santa Fe", -31.6333, -60.7000),
    ("Mendoza", "Mendoza", -32.8895, -68.8458),
    ("San Rafael", "Mendoza", -34.6177, -68.3301),
    ("San Miguel de Tucuman", "Tucuman", -26.8083, -65.2176),
    ("Salta", "Salta", -24.7859, -65.4117),
    ("San Salvador de Jujuy", "Jujuy", -24.1858, -65.2995),
    ("Santiago del Estero", "Santiago del Estero", -27.7834, -64.2642),
    ("Resistencia", "Chaco", -27.4514, -58.9867),
    ("Corrientes", "Corrientes", -27.4692, -58.8306),
    ("Posadas", "Misiones", -27.3671, -55.8961),
    ("Formosa", "Formosa", -26.1849, -58.1731),
    ("San Juan", "San Juan", -31.5375, -68.5364),
    ("La Rioja", "La Rioja", -29.4131, -66.8558),
    ("Catamarca", "Catamarca", -28.4696, -65.7852),
    ("San Luis", "San Luis", -33.2950, -66.3356),
    ("Parana", "Entre Rios", -31.7333, -60.5238),
    ("Santa Rosa", "La Pampa", -36.6167, -64.2833),
    ("Neuquen", "Neuquen", -38.9516, -68.0591),
    ("Bariloche", "Rio Negro", -41.1335, -71.3103),
    ("Viedma", "Rio Negro", -40.8135, -62.9967),
    ("Comodoro Rivadavia", "Chubut", -45.8642, -67.4966),
    ("Trelew", "Chubut", -43.2489, -65.3051),
    ("Rio Gallegos", "Santa Cruz", -51.6230, -69.2168),
    ("El Calafate", "Santa Cruz", -50.3379, -72.2648),
    ("Ushuaia", "Tierra del Fuego", -54.8019, -68.3030),
]

_station_directory = {}   # station_id -> {"province": ...}
_directory_lock = threading.Lock()
_last_discovery = None


def discover_stations():
    """Recorre ARGENTINA_GRID pidiendo a Wunderground las PWS mas cercanas a
    cada punto (endpoint /v3/location/near) y arma un directorio de IDs unicos
    hasta llegar a WUNDERGROUND_MAX_STATIONS. Esto reemplaza tener que listar
    estaciones a mano."""
    directory = {}
    for name, province, lat, lon in ARGENTINA_GRID:
        if len(directory) >= WUNDERGROUND_MAX_STATIONS:
            break
        try:
            r = requests.get(
                "https://api.weather.com/v3/location/near",
                params={"geocode": f"{lat},{lon}", "product": "pws",
                        "format": "json", "apiKey": WUNDERGROUND_API_KEY},
                timeout=10,
            )
            r.raise_for_status()
            ids = r.json().get("location", {}).get("stationIdentifier", [])
            for sid in ids:
                if sid not in directory:
                    directory[sid] = {"province": province}
                if len(directory) >= WUNDERGROUND_MAX_STATIONS:
                    break
        except Exception as e:
            app.logger.warning(f"Descubrimiento cerca de {name}: {e}")
        time.sleep(WUNDERGROUND_RATE_SLEEP)
    app.logger.info(f"Descubrimiento: {len(directory)} estaciones unicas encontradas")
    return directory


def maybe_refresh_directory():
    """Corre discover_stations() solo cada WUNDERGROUND_DISCOVER_HOURS, para no
    gastar el cupo diario de llamadas en re-descubrir todo el tiempo."""
    global _station_directory, _last_discovery
    now = datetime.now(timezone.utc)
    with _directory_lock:
        stale = _last_discovery is None or (now - _last_discovery).total_seconds() > WUNDERGROUND_DISCOVER_HOURS * 3600
    if stale:
        new_dir = discover_stations()
        with _directory_lock:
            if new_dir:
                _station_directory = new_dir
                _last_discovery = now


# ---------------------------------------------------------------------------
# 1) WEATHER UNDERGROUND
#    - Si WUNDERGROUND_STATION_IDS esta seteada: usa esa lista fija.
#    - Si no, y WUNDERGROUND_AUTO_DISCOVER=true (default): descubre estaciones
#      solas recorriendo ARGENTINA_GRID, hasta WUNDERGROUND_MAX_STATIONS.
#
#    OJO CON EL LIMITE GRATUITO: 1500 llamadas/dia, 30/min. Si pedis condicion
#    actual de N estaciones en cada ciclo de REFRESH_SECONDS, gastas
#    N * (86400 / REFRESH_SECONDS) llamadas por dia. Con los valores por
#    defecto (120 estaciones, refresh cada 15 min = 96 ciclos/dia) eso daria
#    11520/dia, muy por encima del limite -> por eso mas abajo el propio
#    programa CALCULA y te avisa el minimo refresco seguro segun cuantas
#    estaciones pidas (ver check_rate_budget()).
# ---------------------------------------------------------------------------
def fetch_wunderground():
    stations = []
    if not WUNDERGROUND_API_KEY:
        return stations

    if WUNDERGROUND_STATION_IDS:
        targets = {sid: {"province": os.environ.get(f"PROVINCE_{sid}", "")} for sid in WUNDERGROUND_STATION_IDS}
    elif WUNDERGROUND_AUTO_DISCOVER:
        maybe_refresh_directory()
        with _directory_lock:
            targets = dict(_station_directory)
    else:
        return stations

    for sid, meta in targets.items():
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
                "province": meta.get("province", ""),
                "source": "Wunderground",
                "max": metric.get("tempHigh", metric.get("temp")),
                "min": metric.get("tempLow", metric.get("temp")),
            })
        except Exception as e:
            app.logger.warning(f"Wunderground {sid}: {e}")
        time.sleep(WUNDERGROUND_RATE_SLEEP)
    return stations


def check_rate_budget():
    """Avisa en el log si la combinacion de MAX_STATIONS + REFRESH_SECONDS
    va a pasarse del cupo diario gratuito, y sugiere un refresco seguro."""
    if not WUNDERGROUND_API_KEY:
        return
    n = WUNDERGROUND_MAX_STATIONS if (WUNDERGROUND_AUTO_DISCOVER or not WUNDERGROUND_STATION_IDS) else len(WUNDERGROUND_STATION_IDS)
    if n <= 0:
        return
    calls_per_day = n * (86400 / REFRESH_SECONDS)
    if calls_per_day > WUNDERGROUND_DAILY_BUDGET:
        safe_refresh_min = round((n * 86400 / WUNDERGROUND_DAILY_BUDGET) / 60)
        app.logger.warning(
            f"[WU] Con {n} estaciones y REFRESH_SECONDS={REFRESH_SECONDS} se harian "
            f"~{int(calls_per_day)} llamadas/dia, arriba del presupuesto de "
            f"{WUNDERGROUND_DAILY_BUDGET}. Subi REFRESH_SECONDS a ~{safe_refresh_min * 60} "
            f"(≈{safe_refresh_min} min) o bajá WUNDERGROUND_MAX_STATIONS."
        )


# ---------------------------------------------------------------------------
# 2) SMN -- via el dataset oficial "Estado del Tiempo presente" publicado en
#    datos.gob.ar (portal de datos abiertos del Estado), que corre sobre CKAN
#    y tiene API documentada. Se actualiza cada hora. Mejor via que inspeccionar
#    con F12 el portal de descarga manual de smn.gob.ar.
#
#    OJO: ese dataset da la temperatura ACTUAL de cada estacion (una lectura
#    puntual), no un maximo/minimo del dia ya calculado. Por eso el backend
#    arma su propio Tmax/Tmin: cada vez que llega una lectura, actualiza un
#    registro corriendo por estacion (sube el maximo, baja el minimo) que se
#    reinicia a medianoche hora Argentina. Ver update_daily_minmax().
#
#    AJUSTAR: no pude verificar el separador/columnas exactas del archivo
#    desde este entorno (sin salida de red hacia datos.gob.ar). Corré el
#    backend con SMN_DEBUG=true una vez: te va a dejar en el log las primeras
#    lineas crudas del archivo para confirmar separador y en que columna esta
#    el nombre de estacion y la temperatura, y ajustas SMN_SEPARATOR /
#    SMN_COL_NAME / SMN_COL_TEMP en el .env sin tocar el codigo.
# ---------------------------------------------------------------------------
_daily_minmax = {}   # nombre_estacion -> {"max", "min", "day", "province", "source"}
_daily_lock = threading.Lock()


def _local_today():
    now = datetime.now(AR_TZ) if AR_TZ else datetime.utcnow()
    return now.date()


def update_daily_minmax(name, temp, province="", source=""):
    if temp is None:
        return
    today = _local_today()
    with _daily_lock:
        rec = _daily_minmax.get(name)
        if rec is None or rec["day"] != today:
            _daily_minmax[name] = {"max": temp, "min": temp, "day": today,
                                    "province": province, "source": source}
        else:
            rec["max"] = max(rec["max"], temp)
            rec["min"] = min(rec["min"], temp)
            if province:
                rec["province"] = province


def fetch_smn():
    stations = []
    meta_url = os.environ.get(
        "SMN_CKAN_PACKAGE_URL",
        "https://datos.gob.ar/api/3/action/package_show?id=smn-estado-tiempo-presente",
    )
    try:
        r = requests.get(meta_url, timeout=15)
        r.raise_for_status()
        resources = r.json().get("result", {}).get("resources", [])
        if not resources:
            app.logger.warning("SMN: el dataset no devolvio recursos descargables")
            return stations
        zip_url = resources[0]["url"]

        zr = requests.get(zip_url, timeout=20)
        zr.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(zr.content)) as zf:
            inner_name = zf.namelist()[0]
            raw = zf.read(inner_name)

        text = raw.decode("latin-1", errors="ignore")
        lines = text.splitlines()

        if os.environ.get("SMN_DEBUG", "false").lower() == "true":
            app.logger.info("SMN [DEBUG] primeras lineas del archivo:\n" + "\n".join(lines[:5]))

        sep = os.environ.get("SMN_SEPARATOR", ";")
        name_col = int(os.environ.get("SMN_COL_NAME", "0"))
        temp_col = int(os.environ.get("SMN_COL_TEMP", "1"))
        skip_header = os.environ.get("SMN_SKIP_HEADER", "true").lower() == "true"

        rows = lines[1:] if skip_header else lines
        for line in rows:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(sep)]
            if len(parts) <= max(name_col, temp_col):
                continue
            name = parts[name_col]
            if not name:
                continue
            try:
                temp = float(parts[temp_col].replace(",", "."))
            except ValueError:
                continue
            update_daily_minmax(name, temp, province="", source="SMN")

    except Exception as e:
        app.logger.warning(f"SMN: {e}")

    with _daily_lock:
        for name, rec in _daily_minmax.items():
            if rec["source"] == "SMN":
                stations.append({
                    "name": name,
                    "province": rec.get("province", ""),
                    "source": "SMN",
                    "max": rec["max"],
                    "min": rec["min"],
                })
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


check_rate_budget()
start_refresh_thread()  # arranca tambien cuando gunicorn importa el modulo

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
