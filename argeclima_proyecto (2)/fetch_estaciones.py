#!/usr/bin/env python3
"""
fetch_estaciones.py
--------------------
Script de arranque para juntar datos reales de estaciones meteorologicas
de Argentina y dejarlos en un datos.json que la pagina argentina_clima_2000s.html
pueda cargar (reemplazando el array STATIONS de ejemplo).

IMPORTANTE SOBRE SEGURIDAD:
- Nunca pongas tu API key adentro del HTML/JS de una pagina publica: cualquiera
  que abra "Ver codigo fuente" la puede copiar y usarla. Este script la toma de
  una variable de entorno, para que corra en tu maquina o en un servidor tuyo,
  nunca en el navegador del visitante.
- Antes de usar la key que compartiste en el chat, generá una nueva en tu cuenta
  de Weather Underground / IBM PWS y revocá la anterior, ya que quedó expuesta
  en esta conversación.

USO:
    export WUNDERGROUND_API_KEY="tu_api_key_nueva"
    python3 fetch_estaciones.py

Esto genera ./datos.json con la lista de estaciones.

NOTA SOBRE LAS FUENTES:
- SMN (https://www.smn.gob.ar/descarga-de-datos): es un portal de descarga manual
  de archivos (no una API con key). Este script trae un ejemplo de como parsear
  el CSV una vez descargado a mano o via requests si el archivo tiene URL directa.
- INTA / Red SIGA (https://siga.inta.gob.ar/#/data): expone datos via un backend
  propio; hay que inspeccionar las llamadas de red de esa SPA (pestaña Network del
  navegador) para encontrar el endpoint JSON real que usa el mapa, porque no hay
  documentacion publica formal.
- OHMC (https://meteocordoba.ohmc.com.ar/admin/...): esa URL es el panel de
  administracion de Django, pensado para uso interno, no para scraping publico.
  Para integrarlo en serio hay que pedirle al OHMC un endpoint/API de lectura.
- Wunderground (PWS API / IBM): tiene una API real con key, se ejemplifica abajo.
"""

import os
import json
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    sys.exit("Falta 'requests'. Instalalo con: pip install requests --break-system-packages")


# ---------------------------------------------------------------------------
# 1) WEATHER UNDERGROUND (PWS Current Conditions API)
# ---------------------------------------------------------------------------
def fetch_wunderground_station(station_id: str, api_key: str):
    """
    Trae la condicion actual de UNA estacion personal (PWS) de Wunderground.
    station_id: el codigo de la estacion, ej "IBUENOSA123" (lo sacas de wunderground.com/dashboard/pws/<ID>)
    Doc oficial: https://docs.google.com/document/d/1eKCnKXI9xnoMGRRzOL1xPCBihNV2rOet08qpE_gArAY
    """
    url = "https://api.weather.com/v2/pws/observations/current"
    params = {
        "stationId": station_id,
        "format": "json",
        "units": "m",
        "apiKey": api_key,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    obs = data["observations"][0]
    return {
        "name": obs.get("neighborhood", station_id),
        "province": "",  # Wunderground no siempre da provincia; completar a mano o via geocoding
        "source": "Wunderground",
        "max": obs["metric"].get("tempHigh", obs["metric"]["temp"]),
        "min": obs["metric"].get("tempLow", obs["metric"]["temp"]),
    }


# ---------------------------------------------------------------------------
# 2) SMN - descarga de datos (portal manual, no API)
# ---------------------------------------------------------------------------
def parse_smn_csv(path_to_downloaded_file: str):
    """
    El SMN no ofrece una API con key: hay que descargar el archivo a mano desde
    https://www.smn.gob.ar/descarga-de-datos y despues parsearlo aca.
    Este parser es un esqueleto: hay que ajustarlo al formato real de columnas
    del archivo que descargues (suele venir como texto de ancho fijo o CSV
    separado por ';').
    """
    stations = []
    with open(path_to_downloaded_file, encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # AJUSTAR: separador y columnas segun el archivo real del SMN
            parts = line.split(";")
            if len(parts) < 4:
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
    return stations


# ---------------------------------------------------------------------------
# 3) INTA / SIGA - hay que inspeccionar el endpoint real de la SPA
# ---------------------------------------------------------------------------
def fetch_inta_siga_placeholder():
    """
    https://siga.inta.gob.ar/#/data es una Single Page Application: los datos
    los trae por atras con llamadas fetch/XHR propias. Para encontrarlas:
      1. Abrir la pagina en el navegador.
      2. F12 -> pestaña "Network" -> filtrar por "Fetch/XHR".
      3. Navegar el mapa/tabla de estaciones y ver que URL JSON se llama.
      4. Replicar esa llamada aca con 'requests'.
    Se deja como placeholder porque el endpoint puede cambiar sin aviso.
    """
    return []


# ---------------------------------------------------------------------------
def main():
    api_key = os.environ.get("WUNDERGROUND_API_KEY=53b89abc03d14d7ab89abc03d1dd7ab6")
    all_stations = []

    if api_key:
        # Ejemplo con un puñado de estaciones PWS de ejemplo (reemplazar por las reales)
        example_station_ids = []  # ej: ["IBUENOSA123", "ICORDOB456"]
        for sid in example_station_ids:
            try:
                all_stations.append(fetch_wunderground_station(sid, api_key))
            except Exception as e:
                print(f"[WARN] No se pudo traer {sid}: {e}", file=sys.stderr)
    else:
        print("[INFO] WUNDERGROUND_API_KEY no seteada, se omite Wunderground.", file=sys.stderr)

    # all_stations.extend(parse_smn_csv("smn_descarga.csv"))  # descomentar cuando tengas el archivo
    # all_stations.extend(fetch_inta_siga_placeholder())

    if not all_stations:
        print("[INFO] No se trajo ningun dato real todavia (completa las funciones de arriba).",
              file=sys.stderr)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stations": all_stations,
    }
    with open("datos.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Listo: {len(all_stations)} estaciones guardadas en datos.json")


if __name__ == "__main__":
    main()
