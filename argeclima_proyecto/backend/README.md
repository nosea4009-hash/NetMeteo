# ArgeClima backend

Servidor chiquito que junta datos de estaciones meteorologicas y los expone
en `/api/estaciones` como JSON, para que `argentina_clima_2000s_live.html`
los muestre en vivo.

## Por que hace falta un backend

Un sitio estatico (HTML/JS en el navegador) no puede llamar de forma
confiable a Wunderground/SMN/INTA/OHMC directamente:
- Wunderground exige una API key, y ponerla en JS visible para cualquiera
  que abra "ver codigo fuente" es un agujero de seguridad.
- La mayoria de estos servicios no habilita CORS para que cualquier pagina
  del mundo les pegue desde el navegador.

Por eso el patron correcto es: **backend propio con la key guardada como
variable de entorno -> guarda un JSON en cache -> el HTML le pide ese JSON
(sin key) por fetch()**.

## 1. Probar localmente

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# completar .env con tu WUNDERGROUND_API_KEY nueva y tus estaciones
export $(grep -v '^#' .env | xargs)
python3 app.py
```

Abrí `http://localhost:5000/api/estaciones` y deberias ver el JSON.

## 2. Deploy gratis (ejemplo con Render.com)

1. Subí esta carpeta `backend/` a un repo de GitHub.
2. En [render.com](https://render.com) -> "New +" -> "Web Service" -> conectá el repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. En "Environment" cargá las mismas variables del `.env.example`
   (WUNDERGROUND_API_KEY, WUNDERGROUND_STATION_IDS, ALLOWED_ORIGIN con el
   dominio donde vas a alojar el HTML, etc).
6. Deploy. Vas a obtener una URL tipo `https://argeclima-backend.onrender.com`.

(Railway, Fly.io o PythonAnywhere funcionan con pasos muy parecidos.)

## 3. Conectar el HTML

En `argentina_clima_2000s_live.html`, cerca del principio del `<script>`,
cambiá:

```js
const API_URL = "https://TU-BACKEND.onrender.com/api/estaciones";
```

por la URL real que te dio tu hosting. Si el fetch falla (backend caido,
CORS mal configurado, etc.) la pagina cae sola a modo demo y lo avisa con
un cartel, no se rompe.

## 4. Completar las fuentes que faltan

- **Wunderground**: ya funciona si completaste `WUNDERGROUND_API_KEY` y
  `WUNDERGROUND_STATION_IDS`.
- **SMN**: el portal es de descarga manual. Si encontras una URL de archivo
  que se actualiza sola (mirando la pestaña Network del navegador en
  smn.gob.ar/descarga-de-datos), poné esa URL en `SMN_CSV_URL` y ajustá el
  parseo en `fetch_smn()` al formato real de columnas.
- **INTA / SIGA**: es una SPA; hay que inspeccionar que endpoint JSON llama
  internamente (F12 -> Network -> Fetch/XHR) y ponerlo en `INTA_API_URL`,
  ajustando `fetch_inta()` a la forma real de esa respuesta.
- **OHMC**: la URL que diste es el panel de administracion interno de
  Django, no pensado para scraping publico. Conviene escribirles y pedir
  un endpoint/API de lectura; una vez que te lo den, `OHMC_API_URL` (y
  `OHMC_API_KEY` si corresponde).

Cada fuente que dejes sin configurar simplemente no aporta estaciones ese
ciclo (no rompe nada); podes sumarlas de a una.
