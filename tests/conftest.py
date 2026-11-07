import re

import pytest
from fastapi.testclient import TestClient

from webapp.config import AppConfig
from webapp.server import create_app


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        secret_key="llave-de-prueba-no-es-un-usuario",
        data_dir=tmp_path,
        timezone="America/Bogota",
    )


@pytest.fixture
def client(config):
    return TestClient(create_app(config))


def csrf_from(response_text: str) -> str:
    match = re.search(r'name="csrf" content="([^"]+)"', response_text)
    assert match, response_text[:400]
    return match.group(1)


def entrar(client: TestClient) -> str:
    page = client.get("/setup")
    token = csrf_from(page.text)
    response = client.post(
        "/setup",
        data={
            "csrf": token,
            "username": "jenny",
            "password": "clave-larga-1",
            "password2": "clave-larga-1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Resumen" in response.text
    return csrf_from(response.text)
