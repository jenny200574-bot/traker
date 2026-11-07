# traker

Aplicación de escritorio para consultar catálogo y stock en Dropi y guardarlos en una base local.

## Requisitos

- Python 3.10 o superior
- En Windows puedes usar los scripts `.bat`. En Linux o macOS sirve el entorno virtual de abajo.

## Instalación

```bash
python3 -m venv .venv
```

Windows:

```bat
.venv\Scripts\activate
pip install -r requirements.txt
```

Linux o macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

En Windows también puedes ejecutar `install.bat` y después `run.bat`.

## Configuración local

1. Copia `settings.example.json` a `settings.json` en esta misma carpeta.
2. Escribe tu correo y tu contraseña de Dropi en `email` y `password`. Deja `token` vacío: la aplicación lo pide al iniciar sesión.
3. `horarios` son las horas de la sincronización automática. Se pueden cambiar en la aplicación.
4. No compartas `settings.json`. Ya está en `.gitignore`.

Para abrir la interfaz, con el entorno activado:

```bash
python app.py
```

## Exclusiones de seguridad

Estos archivos no están en el repositorio y no deben subirse:

- `settings.json`: credenciales y token locales
- `test_login.py`
- `*.db`, `*.db-wal`, `*.db-shm` y demás `*.db-*`: base local
- `backups_historial/`: historial
- `venv/`, `.venv/`, `__pycache__/`, `*.pyc`
- `.env*` (se puede versionar `.env.example` si solo tiene placeholders)
- `*.zip`

La base y los respaldos se crean en esta carpeta al usar la aplicación. No los publiques.

## Aplicación web

La web convive con la de escritorio. No la sustituye y no modifica `dropi_tracker.db` ni `settings.json` por su cuenta. Usa su propia base en `data/`.

No está desplegada ni lista para producción. Por defecto solo escucha en `127.0.0.1` y exige una cuenta creada por ti. No hay usuario ni clave iniciales.

### Instalación

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-web.txt
cp .env.example .env
```

Edita `.env` y escribe un `DROP_SECRET_KEY` largo, inventado por ti. Ese valor firma la sesión y cifra el correo, la clave y el token de Dropi. No lo subas al repositorio.

### Arranque

En una terminal:

```bash
python -m webapp
```

En otra, el proceso que ejecuta descargas y horarios aunque cierres el navegador:

```bash
python -m webapp.worker
```

Abre `http://127.0.0.1:8000`. La primera pantalla pide crear el usuario y la clave de este equipo.

### Dropi

En Ajustes escribe el correo y la clave. Quedan cifrados en la base del servidor. La interfaz solo muestra el correo enmascarado y si hay clave o token. Probar conexión, descargar, sincronizar el directorio y traer métricas mensuales encolan un trabajo: no ocurre nada hasta que el worker está en marcha, y nunca al arrancar solo.

Los horarios usan la zona guardada. Al crear la base es `America/Bogota`, y se puede cambiar. Una hora ya pasada no se recupera. Si ya hay una descarga en curso, el horario no lanza otra.

### Base de la aplicación de escritorio

En Ajustes, elige el archivo `dropi_tracker.db`. La importación lo abre en solo lectura, guarda antes un respaldo de la base web y une por `supplier_id` lo manual con lo sincronizado. No inventa una bodega física si el registro solo tiene proveedor. El historial legado queda consultable, con cobertura desconocida: no se le asigna una confianza alta.

Los CSV de `backups_historial/` no se importan en esta versión. Si solo existen ahí, no aparecen.

### Demostración

En Ajustes puedes abrir «Datos de demostración». Es un archivo distinto, `data/demo.db`, con productos sintéticos. No se mezcla con la base real y no llama a Dropi. La pantalla lo marca en todo momento. Para ver la aplicación no hace falta una base de producción; si la base real está vacía, los conteos en cero se muestran como conteos reales.

### Qué hace esta versión

- Resumen, productos con búsqueda y ficha de evidencia, seguimiento (listas, etiquetas, notas, clasificación y shortlist), tendencias conservadoras, proveedores y bodegas físicas, fichas creativas y ajustes.
- Las bajas de stock se presentan como proxy de demanda. No son ventas confirmadas ni viralidad. Los pedidos mensuales del proveedor van aparte.
- Excel descargado por el navegador.

### Qué no hace

- No genera banners ni video. El estudio solo guarda fichas. El contrato de un generador futuro está en `webapp/creative_contracts.py` y el registro llega vacío.
- No publica anuncios, no gasta presupuesto y no llama a Dropi desde las pruebas.
- No es un despliegue de VPS. No abras el puerto a internet sin entender que, aunque la sesión es obligatoria, esta versión no está endurecida para producción.
- No hay varios equipos ni multiusuario. La cuenta es una sola y se puede cambiar la clave.
- Una ausencia en una descarga fallida o parcial no se guarda como stock cero. Un snapshot completo sí marca al producto como ausente, sin poner su stock en cero.

### Pruebas

```bash
pytest
```

La aplicación de escritorio se sigue abriendo con `python app.py`.

