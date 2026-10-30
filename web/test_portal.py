from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend import portal_app as portal


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(portal, "DATA", Path(tmp_path))
    monkeypatch.setattr(portal, "DB_PATH", Path(tmp_path) / "customers.sqlite3")
    monkeypatch.setattr(portal, "PUBLIC", "https://voice.agcndigital.com.br")
    monkeypatch.delenv("AGCN_LICENSE_PRICE_CENTS", raising=False)
    monkeypatch.delenv("AGCN_LICENSE_DAYS", raising=False)
    monkeypatch.delenv("AGCN_INFINITEPAY_HANDLE", raising=False)
    monkeypatch.delenv("AGCN_INFINITEPAY_API_KEY", raising=False)
    with TestClient(portal.app, base_url=portal.PUBLIC, follow_redirects=True) as test_client:
        yield test_client


def test_portal_home_health(client):
    assert client.get("/health").json()["status"] == "ok"
    home = client.get("/")
    assert home.status_code == 200
    assert "WINDOWS" in home.text
    assert "AGCN Live Voice" in home.text


def test_customer_registration_account_and_signout(client):
    registration = client.post("/cadastro", headers={"Origin": portal.PUBLIC}, data={
        "name": "Teste Cliente", "email": "teste@exemplo.com", "password": "senha-segura-123"
    })
    assert registration.status_code == 200
    assert "Teste" in registration.text
    assert "Sem licença ativa" in registration.text
    assert client.get("/conta").status_code == 200
    unavailable = client.post("/comprar", headers={"Origin": portal.PUBLIC})
    assert unavailable.status_code == 503
    assert client.post("/baixar", headers={"Origin": portal.PUBLIC}).status_code == 403
    client.post("/sair", headers={"Origin": portal.PUBLIC})
    assert "Criar conta" in client.get("/").text
    assert "Bem-vindo de volta" in client.get("/conta").text


def test_bad_password_and_duplicate_registration(client):
    client.post("/cadastro", headers={"Origin": portal.PUBLIC}, data={
        "name": "Teste Nome", "email": "test@example.com", "password": "abcdefghij"
    })
    client.post("/sair", headers={"Origin": portal.PUBLIC})
    incorrect = client.post("/entrar", headers={"Origin": portal.PUBLIC}, data={
        "email": "test@example.com", "password": "senha-errada"
    })
    assert "senha inválidos" in incorrect.text
    duplicate = client.post("/cadastro", headers={"Origin": portal.PUBLIC}, data={
        "name": "Teste Nome", "email": "test@example.com", "password": "abcdefghij"
    })
    assert "já possui cadastro" in duplicate.text
