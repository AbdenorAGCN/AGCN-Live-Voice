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


def test_navigation_plans_download_and_previews(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/planos"' in home.text
    assert 'href="/conta"' in home.text
    assert 'href="/download"' in home.text
    assert 'screengallery' in home.text
    assert client.get("/planos").status_code == 200
    assert "49,90" in client.get("/planos").text
    assert "Bem-vindo de volta" in client.get("/download").text
    assert client.get("/assets/logo.svg").status_code == 200
    for screen in ("dashboard", "produto", "voz-audio"):
        response = client.get(f"/assets/previews/{screen}.svg")
        assert response.status_code == 200
        assert "<svg" in response.text
    assert client.get("/assets/previews/nao-existe.svg").status_code == 404


def test_game_style_mobile_nav_and_screenshot_carousel(client):
    home = client.get("/")
    assert home.status_code == 200
    assert 'class="agcn-mobile-nav"' in home.text
    assert 'href="/planos" aria-current=' not in home.text
    assert 'href="/" aria-current="page"' in home.text
    assert 'class="agcn-showcase"' in home.text
    assert home.text.count('class="showcase-cover"') == 3
    assert 'src="/assets/screenshots/dashboard.html"' in home.text
    assert 'src="/assets/screenshots/produto.html"' in home.text
    assert 'src="/assets/screenshots/voz-audio.html"' in home.text
    assert 'sandbox=""' in home.text
    assert "frame-src 'self'" in home.headers["content-security-policy"]
    script = client.get("/assets/voice-carousel.js")
    assert script.status_code == 200
    assert "2000" in script.text
    assert 'data-flow-slot' not in script.text  # JS sets dataset.flowSlot dynamically
    for name in ("dashboard", "produto", "voz-audio"):
        preview = client.get(f"/assets/screenshots/{name}.html")
        assert preview.status_code == 200
        assert 'width:1600px' in preview.text
        assert "<script" not in preview.text.lower()
    assert client.get("/assets/screenshots/invalid.html").status_code == 404
    assert 'href="/planos" aria-current="page"' in client.get("/planos").text
    assert 'href="/conta" aria-current="page"' in client.get("/conta").text
