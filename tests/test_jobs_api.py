from datetime import datetime, timezone

import pytest

from tests.conftest import csrf_from, entrar
from webapp.db import connect, init_db
from webapp.jobs import claim_next, enqueue
from webapp.runner import _upsert_directory, execute
from webapp.schedule import fire_due_schedules
from webapp.secrets_store import save_dropi_credentials
from webapp.services import save_schedule


def test_api_exige_sesion_y_no_filtra_secretos(client, config):
    assert client.get("/api/productos").status_code == 401
    assert client.get("/api/resumen").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
    token = entrar(client)
    secret = "clave-dropi-que-no-debe-verse"
    email = "persona@example.com"
    with connect(config.db_path) as conn:
        save_dropi_credentials(conn, config.secret_key, email=email, password=secret, token="tok-oculto")
    ajustes = client.get("/api/ajustes")
    assert ajustes.status_code == 200
    body = ajustes.text
    assert secret not in body
    assert "tok-oculto" not in body
    assert email not in body
    assert "persona" in body or "***" in body
    queued = client.post(
        "/api/trabajos",
        json={"tipo": "descargar"},
        headers={"x-csrf-token": token},
    )
    assert queued.status_code == 200
    again = client.post(
        "/api/trabajos",
        json={"tipo": "descargar"},
        headers={"x-csrf-token": token},
    )
    assert again.json()["already"] is True
    assert again.json()["id"] == queued.json()["id"]


def test_seguimiento_persiste_y_demo_no_mezcla(client, config):
    token = entrar(client)
    created = client.post(
        "/api/proveedores",
        json={"nombre": "Casa", "url": "https://app.dropi.co/provider/77"},
        headers={"x-csrf-token": token},
    )
    assert created.status_code == 200
    assert created.json()["supplier_id"] == 77
    with connect(config.db_path) as conn:
        conn.execute(
            """INSERT INTO products(product_id_dropi, supplier_id, nombre, stock, categoria, catalog_state)
               VALUES(5,77,'Lámpara real',4,'Hogar','visto')"""
        )
    client.post(
        "/api/productos/77/5/clasificacion",
        json={"classification": "prioridad", "shortlist": 1},
        headers={"x-csrf-token": token},
    )
    client.post(
        "/api/productos/77/5/nota",
        json={"body": "Revisar la serie antes de la ficha."},
        headers={"x-csrf-token": token},
    )
    client.post(
        "/api/productos/77/5/etiqueta",
        json={"name": "mesa"},
        headers={"x-csrf-token": token},
    )
    listed = client.post(
        "/api/listas",
        json={"name": "Para hoy", "supplier_id": 77, "product_id": 5},
        headers={"x-csrf-token": token},
    )
    assert listed.status_code == 200
    reopened = connect(config.db_path)
    row = reopened.execute(
        "SELECT classification, shortlist FROM product_classification WHERE product_id_dropi=5"
    ).fetchone()
    assert row["classification"] == "prioridad"
    assert row["shortlist"] == 1
    assert reopened.execute("SELECT COUNT(*) n FROM product_notes").fetchone()["n"] == 1
    assert reopened.execute("SELECT COUNT(*) n FROM watchlist_items").fetchone()["n"] == 1
    real_products = reopened.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
    reopened.close()

    mode = client.post("/api/modo", json={"demo": True}, headers={"x-csrf-token": token})
    assert mode.json()["etiqueta"] == "Datos de demostración"
    home = client.get("/")
    assert "Datos de demostración" in home.text
    assert "Generar banner" not in home.text
    demo = client.get("/api/resumen").json()
    assert demo["vacio"] is False
    assert demo["productos"] != real_products
    still = connect(config.db_path)
    assert still.execute("SELECT COUNT(*) n FROM products").fetchone()["n"] == real_products
    assert still.execute("SELECT nombre FROM products WHERE product_id_dropi=5").fetchone()["nombre"] == "Lámpara real"
    still.close()
    studio = client.get("/estudio")
    assert "no está incluido" in studio.text.lower() or "no está incluido" in studio.text
    assert "Generar video" not in studio.text
    denied = client.post("/api/estudio/generar", headers={"x-csrf-token": csrf_from(studio.text)})
    assert denied.status_code == 501
    assert denied.json()["operativo"] is False
    assert "video_url" not in denied.json()

    back = client.post("/api/modo", json={"demo": False}, headers={"x-csrf-token": csrf_from(studio.text)})
    assert back.json()["demo"] is False
    real = client.get("/api/resumen").json()
    assert real["productos"] == real_products
    assert real["vacio"] is False


def test_resumen_real_vacio_no_inventa_proxy(client):
    entrar(client)
    data = client.get("/api/resumen").json()
    assert data["vacio"] is True
    assert data["productos"] == 0
    assert data["proxies"] == []
    page = client.get("/")
    assert "conteo real" in page.text
    assert "viral" not in page.text.lower()


def test_exportar_excel(client, config):
    token = entrar(client)
    with connect(config.db_path) as conn:
        conn.execute(
            """INSERT INTO suppliers(supplier_id, store_name, created_at, updated_at)
               VALUES(3,'Casa','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO products(product_id_dropi, supplier_id, nombre, stock, catalog_state)
               VALUES(1,3,'Vaso',2,'visto')"""
        )
    response = client.get("/export/productos")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert response.content[:2] == b"PK"
    assert token


def test_dos_workers_no_reclaman_el_mismo_trabajo(tmp_path):
    path = tmp_path / "tracker.db"
    init_db(path)
    conn = connect(path)
    enqueue(conn, "descargar")
    conn.close()
    results = []

    def claim():
        local = connect(path)
        try:
            results.append(claim_next(local) is not None)
        finally:
            local.close()

    from threading import Thread
    first = Thread(target=claim)
    second = Thread(target=claim)
    first.start()
    second.start()
    first.join()
    second.join()
    assert results.count(True) == 1
    check = connect(path)
    running = check.execute("SELECT COUNT(*) n FROM jobs WHERE status='running'").fetchone()["n"]
    queued = check.execute("SELECT COUNT(*) n FROM jobs WHERE status='queued'").fetchone()["n"]
    assert running == 1
    assert queued == 0
    check.close()


def test_horario_usa_bogota_y_no_repite_ni_recupera(tmp_path):
    path = tmp_path / "tracker.db"
    init_db(path, "America/Bogota")
    conn = connect(path)
    save_schedule(conn, 8, 0)
    morning = datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)
    created = fire_due_schedules(conn, morning, "America/Bogota")
    assert created
    status = conn.execute("SELECT status FROM jobs WHERE id=?", (created[0],)).fetchone()["status"]
    assert status == "queued"
    assert fire_due_schedules(conn, morning.replace(minute=1), "America/Bogota") == []
    conn.close()

    other = tmp_path / "otro.db"
    init_db(other, "America/Bogota")
    conn = connect(other)
    save_schedule(conn, 8, 0)
    too_soon = datetime(2026, 11, 7, 12, 59, tzinfo=timezone.utc)
    assert fire_due_schedules(conn, too_soon, "America/Bogota") == []
    late = datetime(2026, 11, 7, 13, 5, tzinfo=timezone.utc)
    assert fire_due_schedules(conn, late, "America/Bogota") == []
    conn.close()


def test_horario_no_encola_otro_si_ya_hay_uno(tmp_path):
    path = tmp_path / "tracker.db"
    init_db(path)
    conn = connect(path)
    enqueue(conn, "descargar")
    save_schedule(conn, 8, 0)
    created = fire_due_schedules(
        conn,
        datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc),
        "America/Bogota",
    )
    statuses = [row["status"] for row in conn.execute("SELECT status FROM jobs ORDER BY id")]
    assert statuses.count("queued") == 1
    assert "omitido" in statuses
    assert created
    conn.close()


def test_directorio_no_borra_el_seguimiento_manual(tmp_path):
    path = tmp_path / "tracker.db"
    init_db(path)
    conn = connect(path)
    conn.execute(
        """INSERT INTO suppliers(supplier_id, manual_name, follow_catalog, source_manual, created_at, updated_at)
           VALUES(15,'Manual',1,1,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"""
    )
    _upsert_directory(conn, [{
        "id": 15,
        "name": "Ana",
        "store_name": "Tienda",
        "phone": "1",
        "warehouses": [{"id": 8, "name": "Norte", "city": {"id": 1, "name": "Bogotá"}}],
    }])
    row = conn.execute("SELECT * FROM suppliers WHERE supplier_id=15").fetchone()
    assert row["follow_catalog"] == 1
    assert row["source_manual"] == 1
    assert row["source_synced"] == 1
    assert row["store_name"] == "Tienda"
    assert conn.execute("SELECT COUNT(*) n FROM warehouses").fetchone()["n"] == 1
    conn.close()


def test_trabajo_sin_credenciales_no_sale_a_la_red(tmp_path, monkeypatch):
    path = tmp_path / "tracker.db"
    init_db(path)
    conn = connect(path)
    conn.execute(
        """INSERT INTO suppliers(supplier_id, store_name, follow_catalog, created_at, updated_at)
           VALUES(1,'Casa',1,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')"""
    )
    queued = enqueue(conn, "descargar")
    job = claim_next(conn)

    def boom(*args, **kwargs):
        raise AssertionError("no debía haber red")

    monkeypatch.setattr("requests.sessions.Session.request", boom)
    execute(conn, job, "llave-de-prueba-no-es-un-usuario")
    status = conn.execute("SELECT status, error FROM jobs WHERE id=?", (queued["id"],)).fetchone()
    assert status["status"] == "failed"
    assert "clave" in (status["error"] or "").lower() or status["error"]
    conn.close()


def test_segundo_alta_no_crea_otro_dueno(client):
    entrar(client)
    followed = client.get("/setup", follow_redirects=False)
    assert followed.status_code == 303
    assert followed.headers["location"] == "/login"
