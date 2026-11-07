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
