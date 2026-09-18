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

## 4. Traer "la mayoria" de estaciones de Wunderground automaticamente

Ya no hace falta listar estaciones a mano: con `WUNDERGROUND_API_KEY` seteada
y `WUNDERGROUND_AUTO_DISCOVER=true` (default), el backend usa el endpoint
`/v3/location/near` de Wunderground para descubrir solo las PWS activas cerca
de ~30 puntos que cubren todo el pais (`ARGENTINA_GRID` en `app.py`), hasta
`WUNDERGROUND_MAX_STATIONS` estaciones unicas. Ese descubrimiento se repite
cada `WUNDERGROUND_DISCOVER_HOURS` (24hs por defecto), no en cada refresco.

**Importante — el limite gratuito es 1500 llamadas/dia y 30/min.** Si pedís
la condicion actual de `N` estaciones en cada ciclo de `REFRESH_SECONDS`,
gastas `N * (86400 / REFRESH_SECONDS)` llamadas por dia. El backend calcula
esto solo al arrancar y te deja un `WARNING` en el log si te pasaste, con el
valor de `REFRESH_SECONDS` que conviene usar. Como referencia:

| Estaciones (`MAX_STATIONS`) | Refresco minimo seguro |
|---|---|
| 50   | ~50 min |
| 120  | ~2 h |
| 300  | ~5 h |

Si necesitas refrescos mas frecuentes, la unica salida real es bajar
`WUNDERGROUND_MAX_STATIONS` (menos estaciones, pero mas al dia) o pedir un
plan pago de la API. No hay forma de esquivar el limite del lado del cliente.

Si preferís una lista fija vos mismo en lugar del descubrimiento automatico,
seteá `WUNDERGROUND_STATION_IDS` con los IDs separados por coma — ahí se
ignora `ARGENTINA_GRID` y se usan solo esos.

## 5. Completar las fuentes que faltan

- **Wunderground**: cubierto arriba.
- **SMN**: ya viene funcional de fábrica, sin pedirte nada. En vez de scrapear
  el portal de descarga manual, usa el dataset oficial **"Estado del Tiempo
  presente"** que el SMN publica en datos.gob.ar (portal de datos abiertos
  del Estado, corre sobre CKAN con API documentada), actualizado cada hora.

  Ese dataset da la **temperatura actual** de cada estación, no un
  máximo/mínimo del día ya calculado — por eso el backend arma su propio
  Tmax/Tmin: cada lectura que llega actualiza un registro corriendo por
  estación (sube el máximo, baja el mínimo), que se reinicia solo a
  medianoche hora Argentina. Es decir, cuanto más tiempo lleve corriendo el
  backend en el día, más preciso va a ser el rango — recién a la
  medianoche vas a tener el Tmax/Tmin real y completo del día anterior.

  No pude confirmar el separador/columnas exactas del archivo desde este
  entorno (sin salida de red hacia datos.gob.ar). Para ajustarlo:
  1. Corré el backend local con `SMN_DEBUG=true`.
  2. Mirá el log: vas a ver las primeras líneas crudas del archivo.
  3. Ajustá `SMN_SEPARATOR`, `SMN_COL_NAME` y `SMN_COL_TEMP` en el `.env`
     según lo que veas (por ejemplo, si la temperatura está en la tercera
     columna, `SMN_COL_TEMP=2`). No hace falta tocar código.

- **INTA / SIGA**: es una SPA; hay que inspeccionar que endpoint JSON llama
  internamente (F12 -> Network -> Fetch/XHR) y ponerlo en `INTA_API_URL`,
  ajustando `fetch_inta()` a la forma real de esa respuesta.
- **OHMC**: la URL que diste es el panel de administracion interno de
  Django, no pensado para scraping publico. Conviene escribirles y pedir
  un endpoint/API de lectura; una vez que te lo den, `OHMC_API_URL` (y
  `OHMC_API_KEY` si corresponde).

Cada fuente que dejes sin configurar simplemente no aporta estaciones ese
ciclo (no rompe nada); podes sumarlas de a una.
