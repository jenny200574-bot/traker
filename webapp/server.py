"""HTTP local. Sin sesión no hay páginas, API ni descargas."""
from __future__ import annotations

import secrets
from contextlib import closing
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from webapp.config import AppConfig
from webapp.creative_contracts import available as generators_available
from webapp.db import connect, init_db, put_setting
from webapp.demo_data import seed_demo
from webapp.exports import export_bodegas, export_observaciones, export_productos, export_tendencias
from webapp.jobs import enqueue, list_jobs, request_cancel
from webapp.legacy_import import import_legacy_db
from webapp.security import hash_password, new_csrf, verify_password
from webapp.secrets_store import dropi_public_status, save_dropi_credentials
from webapp.services import (
    CLASSIFICATIONS,
    WINDOWS,
    add_manual_supplier,
    add_note,
    add_tag,
    add_to_watchlist,
    bodegas,
    create_watchlist,
    delete_schedule,
    get_brief,
    list_briefs,
    list_products,
    product_detail,
    remove_from_watchlist,
    remove_tag,
    resumen,
    save_brief,
    save_schedule,
    save_thresholds,
    schedules_public,
    seguimiento,
    set_classification,
    set_supplier_follow,
    set_warehouse_follow,
    tendencias,
    thresholds_from,
    timezone_name,
)
from webapp.timeutil import zone_or_default

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
PUBLIC_PATHS = {"/login", "/setup", "/salud"}


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or AppConfig.from_env()
    config.ensure_dirs()
    init_db(config.db_path, config.timezone)
    app = FastAPI(title="Dropi Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.config = config
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if request.method == "GET" and "csrf" not in request.session:
            request.session["csrf"] = new_csrf()
        public = path in PUBLIC_PATHS or path.startswith("/static")
        if not public and not request.session.get("uid"):
            if path.startswith("/api") or path.startswith("/export"):
                return JSONResponse({"detail": "Se requiere sesión."}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.secret_key,
        session_cookie="drop_session",
        max_age=12 * 3600,
        same_site="lax",
        https_only=False,
    )

    def real_conn():
        return connect(config.db_path)

    def business_conn(request: Request):
        if request.session.get("demo"):
            with closing(connect(config.db_path)) as conn:
                zone = timezone_name(conn, config.timezone)
            seed_demo(config.demo_db_path, zone)
            return connect(config.demo_db_path)
        return connect(config.db_path)

    def zone_of() -> str:
        with closing(real_conn()) as conn:
            return timezone_name(conn, config.timezone)

    def rules_of():
        with closing(real_conn()) as conn:
            return thresholds_from(conn)

    def render(request: Request, name: str, **extra):
        flash = request.session.pop("flash", None)
        return templates.TemplateResponse(
            request,
            name,
            {
                "csrf": request.session.get("csrf", ""),
                "demo": bool(request.session.get("demo")),
                "flash": flash,
                "path": request.url.path,
                "clasificaciones": CLASSIFICATIONS,
                "ventanas": WINDOWS,
                **extra,
            },
        )

    def flash(request: Request, message: str) -> None:
        request.session["flash"] = message

    @app.get("/salud")
    def salud():
        return {"estado": "ok"}

    @app.get("/setup", response_class=HTMLResponse)
    def setup_form(request: Request):
        if _has_owner(config.db_path):
            return RedirectResponse("/login", status_code=303)
        return render(request, "setup.html", title="Crear acceso")

    @app.post("/setup")
    async def setup_post(request: Request):
        if _has_owner(config.db_path):
            return RedirectResponse("/login", status_code=303)
        form = await posted_form(request)
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")
        again = str(form.get("password2") or "")
        error = _validate_account(username, password, again)
        if error:
            return render(request, "setup.html", title="Crear acceso", error=error, username=username)
        with closing(real_conn()) as conn:
            conn.execute(
                "INSERT INTO owner(id, username, password_hash, created_at) VALUES(1,?,?,?)",
                (username, hash_password(password), _now()),
            )
        request.session["uid"] = 1
        request.session["csrf"] = new_csrf()
        flash(request, "Acceso creado en este equipo. No hay otros usuarios.")
        return RedirectResponse("/", status_code=303)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        if not _has_owner(config.db_path):
            return RedirectResponse("/setup", status_code=303)
        if request.session.get("uid"):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html", title="Entrar")

    @app.post("/login")
    async def login_post(request: Request):
        form = await posted_form(request)
        username = str(form.get("username") or "").strip()
        password = str(form.get("password") or "")
        with closing(real_conn()) as conn:
            row = conn.execute("SELECT password_hash FROM owner WHERE username=?", (username,)).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            return render(
                request,
                "login.html",
                title="Entrar",
                error="Usuario o clave incorrectos.",
                username=username,
            )
        request.session["uid"] = 1
        request.session["csrf"] = new_csrf()
        return RedirectResponse("/", status_code=303)

    @app.post("/salir")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        zone = zone_of()
        rules = rules_of()
        with closing(business_conn(request)) as conn:
            data = resumen(conn, rules, zone)
        return render(request, "resumen.html", title="Resumen", data=data)

    @app.get("/productos", response_class=HTMLResponse)
    def productos(request: Request):
        filters = dict(request.query_params)
        zone = zone_of()
        with closing(business_conn(request)) as conn:
            data = list_products(conn, filters, rules_of(), zone)
            proveedores = [
                dict(row) for row in conn.execute(
                    "SELECT supplier_id, store_name, manual_name FROM suppliers ORDER BY supplier_id"
                )
            ]
        params = dict(request.query_params)
        def page_href(number: int) -> str:
            params["pagina"] = str(number)
            from urllib.parse import urlencode
            return "/productos?" + urlencode(params)
        return render(
            request,
            "productos.html",
            title="Productos",
            data=data,
            filtros=filters,
            proveedores=proveedores,
            anterior=page_href(data["pagina"] - 1) if data["pagina"] > 1 else "",
            siguiente=page_href(data["pagina"] + 1) if data["pagina"] < data["paginas"] else "",
        )

    @app.get("/productos/{supplier_id}/{product_id}", response_class=HTMLResponse)
    def producto(request: Request, supplier_id: int, product_id: int, horas: int = 72):
        if horas not in WINDOWS:
            horas = 72
        with closing(business_conn(request)) as conn:
            data = product_detail(conn, supplier_id, product_id, horas, rules_of(), zone_of())
            listas = conn.execute("SELECT id, name FROM watchlists ORDER BY name").fetchall()
        if data is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        return render(request, "producto.html", title=data["nombre"] or "Producto", data=data, listas=listas)

    @app.post("/productos/{supplier_id}/{product_id}/clasificar")
    async def clasificar(request: Request, supplier_id: int, product_id: int):
        form = await posted_form(request)
        short = form.get("shortlist")
        with closing(business_conn(request)) as conn:
            set_classification(
                conn,
                supplier_id,
                product_id,
                str(form.get("classification") or "") or None,
                None if short is None or short == "" else int(short),
            )
        flash(request, "Clasificación guardada.")
        return _back(request, f"/productos/{supplier_id}/{product_id}")

    @app.post("/productos/{supplier_id}/{product_id}/nota")
    async def nota(request: Request, supplier_id: int, product_id: int):
        form = await posted_form(request)
        try:
            with closing(business_conn(request)) as conn:
                add_note(conn, supplier_id, product_id, str(form.get("body") or ""))
            flash(request, "Nota guardada.")
        except ValueError as exc:
            flash(request, str(exc))
        return _back(request, f"/productos/{supplier_id}/{product_id}")

    @app.post("/productos/{supplier_id}/{product_id}/etiqueta")
    async def etiqueta(request: Request, supplier_id: int, product_id: int):
        form = await posted_form(request)
        try:
            with closing(business_conn(request)) as conn:
                add_tag(conn, supplier_id, product_id, str(form.get("name") or ""))
            flash(request, "Etiqueta guardada.")
        except ValueError as exc:
            flash(request, str(exc))
        return _back(request, f"/productos/{supplier_id}/{product_id}")

    @app.post("/productos/{supplier_id}/{product_id}/etiqueta/{tag_id}/quitar")
    def quitar_etiqueta(request: Request, supplier_id: int, product_id: int, tag_id: int):
        with closing(business_conn(request)) as conn:
            remove_tag(conn, supplier_id, product_id, tag_id)
        return _back(request, f"/productos/{supplier_id}/{product_id}")

    @app.post("/productos/{supplier_id}/{product_id}/lista")
    async def meter_en_lista(request: Request, supplier_id: int, product_id: int):
        form = await posted_form(request)
        with closing(business_conn(request)) as conn:
            add_to_watchlist(conn, int(form.get("watchlist_id")), supplier_id, product_id)
        flash(request, "Producto añadido a la lista.")
        return _back(request, f"/productos/{supplier_id}/{product_id}")

    @app.get("/seguimiento", response_class=HTMLResponse)
    def pagina_seguimiento(request: Request):
        with closing(business_conn(request)) as conn:
            data = seguimiento(conn, zone_of())
        return render(request, "seguimiento.html", title="Seguimiento", data=data)

    @app.post("/seguimiento/listas")
    async def nueva_lista(request: Request):
        form = await posted_form(request)
        try:
            with closing(business_conn(request)) as conn:
                create_watchlist(conn, str(form.get("name") or ""))
            flash(request, "Lista creada.")
        except Exception as exc:
            flash(request, "No se pudo crear la lista. ¿El nombre ya existe?")
            _ = exc
        return RedirectResponse("/seguimiento", status_code=303)

    @app.post("/seguimiento/listas/{watchlist_id}/quitar")
    async def quitar_de_lista(request: Request, watchlist_id: int):
        form = await posted_form(request)
        with closing(business_conn(request)) as conn:
            remove_from_watchlist(
                conn,
                watchlist_id,
                int(form.get("supplier_id")),
                int(form.get("product_id")),
            )
        return RedirectResponse("/seguimiento", status_code=303)

    @app.get("/tendencias", response_class=HTMLResponse)
    def pagina_tendencias(request: Request, horas: int = 72):
        if horas not in WINDOWS:
            horas = 72
        with closing(business_conn(request)) as conn:
            data = tendencias(conn, horas, rules_of(), zone_of())
        return render(request, "tendencias.html", title="Tendencias", data=data)

    @app.get("/bodegas", response_class=HTMLResponse)
    def pagina_bodegas(request: Request, q: str = ""):
        with closing(business_conn(request)) as conn:
            data = bodegas(conn, rules_of(), zone_of())
        needle = q.strip().lower()
        if needle:
            data["items"] = [
                item for item in data["items"]
                if needle in item["nombre"].lower()
                or any(needle in wh["nombre"].lower() or needle in wh["ciudad"].lower() for wh in item["bodegas"])
            ]
        return render(request, "bodegas.html", title="Bodegas", data=data, q=q)

    @app.post("/bodegas/manual")
    async def bodega_manual(request: Request):
        form = await posted_form(request)
        try:
            sid_raw = str(form.get("supplier_id") or "").strip()
            with closing(business_conn(request)) as conn:
                add_manual_supplier(
                    conn,
                    str(form.get("nombre") or ""),
                    str(form.get("url") or ""),
                    int(sid_raw) if sid_raw else None,
                    int(form.get("esperados") or 0),
                )
            flash(request, "Proveedor guardado en seguimiento, sin crear una bodega física ficticia.")
        except (ValueError, TypeError) as exc:
            flash(request, str(exc) or "No se pudo guardar el proveedor.")
        return RedirectResponse("/bodegas", status_code=303)

    @app.post("/bodegas/{supplier_id}/seguir")
    async def seguir_proveedor(request: Request, supplier_id: int):
        form = await posted_form(request)
        with closing(business_conn(request)) as conn:
            set_supplier_follow(conn, supplier_id, str(form.get("seguir")) == "1")
        return RedirectResponse("/bodegas", status_code=303)

    @app.post("/bodegas/fisica/{warehouse_id}/seguir")
    async def seguir_bodega(request: Request, warehouse_id: int):
        form = await posted_form(request)
        try:
            with closing(business_conn(request)) as conn:
                set_warehouse_follow(conn, warehouse_id, str(form.get("seguir")) == "1")
        except ValueError as exc:
            flash(request, str(exc))
        return RedirectResponse("/bodegas", status_code=303)

    @app.get("/estudio", response_class=HTMLResponse)
    def estudio(request: Request):
        with closing(business_conn(request)) as conn:
            fichas = list_briefs(conn, zone_of())
        return render(
            request,
            "estudio.html",
            title="Estudio creativo",
            fichas=fichas,
            generadores=generators_available(),
        )

    @app.get("/estudio/{brief_id}", response_class=HTMLResponse)
    def estudio_ficha(request: Request, brief_id: int):
        with closing(business_conn(request)) as conn:
            ficha = get_brief(conn, brief_id, zone_of())
        if ficha is None:
            raise HTTPException(status_code=404, detail="Ficha no encontrada.")
        return render(request, "ficha.html", title=ficha["title"], ficha=ficha, generadores=generators_available())

    @app.post("/estudio")
    async def crear_ficha(request: Request):
        form = await posted_form(request)
        data = _brief_form(form)
        with closing(business_conn(request)) as conn:
            brief_id = save_brief(conn, data)
        flash(request, "Ficha guardada. No se generó ninguna pieza.")
        return RedirectResponse(f"/estudio/{brief_id}", status_code=303)

    @app.post("/estudio/{brief_id}")
    async def editar_ficha(request: Request, brief_id: int):
        form = await posted_form(request)
        with closing(business_conn(request)) as conn:
            save_brief(conn, _brief_form(form), brief_id)
        flash(request, "Ficha actualizada.")
        return RedirectResponse(f"/estudio/{brief_id}", status_code=303)

    @app.get("/ajustes", response_class=HTMLResponse)
    def ajustes(request: Request):
        zone = zone_of()
        with closing(real_conn()) as conn:
            status = dropi_public_status(conn, config.secret_key)
            horarios = schedules_public(conn)
            rules = thresholds_from(conn)
            owner = conn.execute("SELECT username FROM owner WHERE id=1").fetchone()
            trabajos = list_jobs(conn, limit=20)
        for job in trabajos:
            job["log_tail"] = "\n".join((job.get("log") or "").splitlines()[-12:])
        return render(
            request,
            "ajustes.html",
            title="Ajustes",
            dropi=status,
            horarios=horarios,
            umbrales=rules,
            zona=zone,
            usuario=owner["username"] if owner else "",
            trabajos=trabajos,
            demo_flag=bool(request.session.get("demo")),
        )

    @app.post("/ajustes/dropi")
    async def guardar_dropi(request: Request):
        form = await posted_form(request)
        email = str(form.get("email") or "").strip()
        password = str(form.get("password") or "")
        with closing(real_conn()) as conn:
            save_dropi_credentials(
                conn,
                config.secret_key,
                email=email or None,
                password=password or None,
            )
        flash(request, "Credenciales guardadas en el servidor. No se muestran de nuevo.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/trabajos")
    async def lanzar_trabajo(request: Request):
        if request.session.get("demo"):
            flash(request, "El modo demostración no consulta Dropi. Vuelve a la base real para encolar un trabajo.")
            return RedirectResponse("/ajustes", status_code=303)
        form = await posted_form(request)
        kind = str(form.get("tipo") or "")
        try:
            with closing(real_conn()) as conn:
                result = enqueue(conn, kind)
            if result["already"]:
                flash(request, f"Ya hay un trabajo «{kind}» en cola o en curso (#{result['id']}).")
            else:
                flash(request, f"Trabajo #{result['id']} en cola. El worker lo ejecutará aunque cierres el navegador.")
        except ValueError as exc:
            flash(request, str(exc))
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/trabajos/{job_id}/cancelar")
    def cancelar_trabajo(request: Request, job_id: int):
        with closing(real_conn()) as conn:
            request_cancel(conn, job_id)
        flash(request, "Cancelación registrada.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/horarios")
    async def guardar_horario(request: Request):
        form = await posted_form(request)
        try:
            with closing(real_conn()) as conn:
                save_schedule(
                    conn,
                    int(form.get("hour")),
                    int(form.get("minute")),
                    str(form.get("enabled") or "1") == "1",
                    str(form.get("job_type") or "descargar"),
                )
            flash(request, "Horario guardado en la zona configurada. No se recupera una hora ya pasada.")
        except (ValueError, TypeError) as exc:
            flash(request, str(exc) or "Horario inválido.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/horarios/{schedule_id}/borrar")
    def borrar_horario(request: Request, schedule_id: int):
        with closing(real_conn()) as conn:
            delete_schedule(conn, schedule_id)
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/zona")
    async def guardar_zona(request: Request):
        form = await posted_form(request)
        name = str(form.get("timezone") or "").strip()
        try:
            zone_or_default(name, name)
            from zoneinfo import ZoneInfo
            ZoneInfo(name)
        except Exception:
            flash(request, "Zona horaria desconocida.")
            return RedirectResponse("/ajustes", status_code=303)
        with closing(real_conn()) as conn:
            put_setting(conn, "timezone", name)
        flash(request, f"Los horarios usan {name}.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/umbrales")
    async def guardar_umbrales(request: Request):
        form = await posted_form(request)
        incoming = {}
        for key in ("suspicious_restore_ratio", "suspicious_drop_pct", "suspicious_drop_abs", "min_baseline_products", "stale_hours", "gap_hours"):
            if form.get(key) not in (None, ""):
                incoming[key] = form.get(key)
        with closing(real_conn()) as conn:
            save_thresholds(conn, incoming)
        flash(request, "Umbrales guardados. Siguen sin estar validados.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/demo")
    async def alternar_demo(request: Request):
        form = await posted_form(request)
        enabled = str(form.get("demo") or "") == "1"
        request.session["demo"] = enabled
        if enabled:
            with closing(real_conn()) as conn:
                zone = timezone_name(conn, config.timezone)
            seed_demo(config.demo_db_path, zone)
            flash(request, "Estás viendo datos de demostración, en un archivo aparte.")
        else:
            flash(request, "Volviste a la base real.")
        return RedirectResponse("/", status_code=303)

    @app.post("/ajustes/importar")
    async def importar(request: Request):
        if request.session.get("demo"):
            flash(request, "Sal de la demostración antes de importar a la base real.")
            return RedirectResponse("/ajustes", status_code=303)
        form = await posted_form(request)
        upload = form.get("archivo")
        if upload is None or not getattr(upload, "filename", ""):
            flash(request, "Elige el archivo dropi_tracker.db de la aplicación de escritorio.")
            return RedirectResponse("/ajustes", status_code=303)
        target = config.data_dir / "import_tmp.db"
        target.write_bytes(await upload.read())
        try:
            with closing(real_conn()) as conn:
                zone = timezone_name(conn, config.timezone)
            report = import_legacy_db(target, config.db_path, config.backup_dir, zone)
        except Exception as exc:
            flash(request, f"No se importó: {exc.__class__.__name__}")
            return RedirectResponse("/ajustes", status_code=303)
        finally:
            target.unlink(missing_ok=True)
        flash(
            request,
            "Importación hecha sin modificar el origen. "
            f"Productos leídos: {report['productos']}. Observaciones nuevas: {report['observaciones']}. "
            "Los CSV antiguos no se importaron.",
        )
        return RedirectResponse("/ajustes", status_code=303)

    @app.post("/ajustes/clave")
    async def cambiar_clave(request: Request):
        form = await posted_form(request)
        current = str(form.get("actual") or "")
        new = str(form.get("nueva") or "")
        again = str(form.get("nueva2") or "")
        with closing(real_conn()) as conn:
            row = conn.execute("SELECT username, password_hash FROM owner WHERE id=1").fetchone()
            if row is None or not verify_password(current, row["password_hash"]):
                flash(request, "La clave actual no coincide.")
                return RedirectResponse("/ajustes", status_code=303)
            error = _validate_account(row["username"], new, again)
            if error:
                flash(request, error)
                return RedirectResponse("/ajustes", status_code=303)
            conn.execute(
                "UPDATE owner SET password_hash=? WHERE id=1",
                (hash_password(new),),
            )
        flash(request, "Clave actualizada.")
        return RedirectResponse("/ajustes", status_code=303)

    @app.get("/export/{kind}")
    def exportar(request: Request, kind: str):
        zone = zone_of()
        rules = rules_of()
        with closing(business_conn(request)) as conn:
            if kind == "productos":
                blob = export_productos(conn)
                name = "productos.xlsx"
            elif kind == "observaciones":
                blob = export_observaciones(conn)
                name = "observaciones.xlsx"
            elif kind == "tendencias":
                blob = export_tendencias(conn, rules, zone)
                name = "proxy_demanda.xlsx"
            elif kind == "bodegas":
                blob = export_bodegas(conn, rules, zone)
                name = "bodegas.xlsx"
            else:
                raise HTTPException(status_code=404, detail="Exportación desconocida.")
        return Response(
            content=blob,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/api/resumen")
    def api_resumen(request: Request):
        with closing(business_conn(request)) as conn:
            return resumen(conn, rules_of(), zone_of())

    @app.get("/api/productos")
    def api_productos(request: Request):
        with closing(business_conn(request)) as conn:
            return list_products(conn, dict(request.query_params), rules_of(), zone_of())

    @app.get("/api/productos/{supplier_id}/{product_id}")
    def api_producto(request: Request, supplier_id: int, product_id: int, horas: int = 72):
        with closing(business_conn(request)) as conn:
            data = product_detail(conn, supplier_id, product_id, horas, rules_of(), zone_of())
        if data is None:
            raise HTTPException(status_code=404, detail="Producto no encontrado.")
        data.pop("svg", None)
        return data

    @app.post("/api/productos/{supplier_id}/{product_id}/clasificacion")
    async def api_clasificacion(request: Request, supplier_id: int, product_id: int):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            set_classification(
                conn,
                supplier_id,
                product_id,
                payload.get("classification"),
                payload.get("shortlist"),
            )
        return {"ok": True}

    @app.post("/api/productos/{supplier_id}/{product_id}/nota")
    async def api_nota(request: Request, supplier_id: int, product_id: int):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            add_note(conn, supplier_id, product_id, payload.get("body") or "")
        return {"ok": True}

    @app.post("/api/productos/{supplier_id}/{product_id}/etiqueta")
    async def api_etiqueta(request: Request, supplier_id: int, product_id: int):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            add_tag(conn, supplier_id, product_id, payload.get("name") or "")
        return {"ok": True}

    @app.post("/api/listas")
    async def api_lista(request: Request):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            list_id = create_watchlist(conn, payload.get("name") or "")
            if payload.get("supplier_id") and payload.get("product_id"):
                add_to_watchlist(conn, list_id, int(payload["supplier_id"]), int(payload["product_id"]))
        return {"ok": True, "id": list_id}

    @app.get("/api/seguimiento")
    def api_seguimiento(request: Request):
        with closing(business_conn(request)) as conn:
            return seguimiento(conn, zone_of())

    @app.get("/api/tendencias")
    def api_tendencias(request: Request, horas: int = 72):
        with closing(business_conn(request)) as conn:
            return tendencias(conn, horas, rules_of(), zone_of())

    @app.get("/api/bodegas")
    def api_bodegas(request: Request):
        with closing(business_conn(request)) as conn:
            return bodegas(conn, rules_of(), zone_of())

    @app.post("/api/proveedores")
    async def api_proveedor(request: Request):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            sid = add_manual_supplier(
                conn,
                payload.get("nombre") or "",
                payload.get("url") or "",
                payload.get("supplier_id"),
                int(payload.get("esperados") or 0),
            )
        return {"ok": True, "supplier_id": sid}

    @app.get("/api/trabajos")
    def api_trabajos(request: Request):
        with closing(real_conn()) as conn:
            from webapp.services import jobs_public
            return {"trabajos": jobs_public(conn, zone_of())}

    @app.post("/api/trabajos")
    async def api_encolar(request: Request):
        if request.session.get("demo"):
            return JSONResponse(
                {"detail": "El modo demostración no consulta Dropi."},
                status_code=409,
            )
        payload = await posted_json(request)
        with closing(real_conn()) as conn:
            result = enqueue(conn, payload.get("tipo") or "")
        return result

    @app.get("/api/ajustes")
    def api_ajustes(request: Request):
        with closing(real_conn()) as conn:
            return {
                "dropi": dropi_public_status(conn, config.secret_key),
                "zona": timezone_name(conn, config.timezone),
                "umbrales": thresholds_from(conn),
                "umbrales_nota": "Estos umbrales son configurables y no están validados.",
                "horarios": schedules_public(conn),
                "demo": bool(request.session.get("demo")),
            }

    @app.post("/api/modo")
    async def api_modo(request: Request):
        payload = await posted_json(request)
        enabled = bool(payload.get("demo"))
        request.session["demo"] = enabled
        if enabled:
            with closing(real_conn()) as conn:
                zone = timezone_name(conn, config.timezone)
            seed_demo(config.demo_db_path, zone)
        return {"demo": enabled, "etiqueta": "Datos de demostración" if enabled else "Base real"}

    @app.post("/api/estudio/generar")
    def api_generar():
        return JSONResponse(
            {
                "operativo": False,
                "detail": "No hay un módulo de generación registrado. Esta versión no crea banners ni video.",
                "generadores": generators_available(),
            },
            status_code=501,
        )

    @app.post("/api/fichas")
    async def api_ficha(request: Request):
        payload = await posted_json(request)
        with closing(business_conn(request)) as conn:
            brief_id = save_brief(conn, payload)
        return {"ok": True, "id": brief_id, "generacion": False}

    return app


async def posted_form(request: Request):
    form = await request.form()
    if not _same(request.session.get("csrf"), form.get("csrf")):
        raise HTTPException(status_code=403, detail="La sesión del formulario no coincide.")
    return form


async def posted_json(request: Request):
    if not _same(request.session.get("csrf"), request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="La sesión del formulario no coincide.")
    return await request.json()


def _same(expected, supplied) -> bool:
    if not expected or supplied is None:
        return False
    a = str(expected)
    b = str(supplied)
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


def _has_owner(path: Path) -> bool:
    if not path.exists():
        return False
    with closing(connect(path)) as conn:
        row = conn.execute("SELECT 1 FROM owner WHERE id=1").fetchone()
    return row is not None


def _validate_account(username: str, password: str, again: str) -> str | None:
    if len(username) < 3 or len(username) > 40:
        return "El usuario debe tener entre 3 y 40 caracteres."
    if any(ch.isspace() for ch in username):
        return "El usuario no lleva espacios."
    if len(password) < 10:
        return "La clave debe tener al menos 10 caracteres."
    if password != again:
        return "Las claves no coinciden."
    if password == username:
        return "La clave no puede ser igual al usuario."
    return None


def _now() -> str:
    from webapp.timeutil import iso, utcnow
    return iso(utcnow())


def _back(request: Request, default: str) -> RedirectResponse:
    target = str(request.query_params.get("next") or default)
    if not target.startswith("/"):
        target = default
    return RedirectResponse(target, status_code=303)


def _brief_form(form) -> dict:
    def optional_int(key: str):
        raw = str(form.get(key) or "").strip()
        return int(raw) if raw else None

    return {
        "title": form.get("title"),
        "audience": form.get("audience"),
        "promise": form.get("promise"),
        "objections": form.get("objections"),
        "notes": form.get("notes"),
        "status": form.get("status") or "borrador",
        "product_id_dropi": optional_int("product_id_dropi"),
        "supplier_id": optional_int("supplier_id"),
    }

