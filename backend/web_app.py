"""Portal seguro do AGCN Live Voice, reaproveitando o backend oficial.

Executar: uvicorn backend.web_app:app --host 0.0.0.0 --port $PORT
Não inicializa TTS/driver Windows no servidor: a saída local é opcional no navegador.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from backend.app_v054 import app as backend_app

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "agcn_voice_session"
TTL_SECONDS = 60 * 60 * 12
_attempts = defaultdict(lambda: deque(maxlen=12))


def _secret() -> str:
    return os.getenv("AGCN_WEB_SESSION_SECRET", "")


def _password() -> str:
    return os.getenv("AGCN_WEB_PASSWORD", "")


def _signature(value: str) -> str:
    return hmac.new(_secret().encode(), value.encode(), hashlib.sha256).hexdigest()


def _valid_token(token: str | None) -> bool:
    if not token or not _secret() or not _password():
        return False
    try:
        expiry_s, signature = token.split(".", 1)
        expiry = int(expiry_s)
        return time.time() < expiry <= time.time() + TTL_SECONDS and hmac.compare_digest(
            _signature(expiry_s), signature
        )
    except (TypeError, ValueError):
        return False


def _cookie_from_headers(headers: list[tuple[bytes, bytes]]) -> str | None:
    try:
        values = [v.decode("latin-1") for k, v in headers if k.lower() == b"cookie"]
        cookies = SimpleCookie()
        cookies.load("; ".join(values))
        return cookies[COOKIE].value if COOKIE in cookies else None
    except (ValueError, KeyError):
        return None


def _valid_request(request: Request) -> bool:
    return _valid_token(request.cookies.get(COOKIE))


def _same_origin(origin: str | None, host: str) -> bool:
    if not origin:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"https", "http"} and parsed.netloc.lower() == host.lower()


def _page() -> str:
    source = (ROOT / "test_web" / "index_v054.html").read_text(encoding="utf-8")
    source = source.replace(
        "<title>AGCN Live Voice — Painel de Teste</title>",
        "<title>AGCN Live Voice | AGCN Digital</title>",
    )
    source = source.replace("</head>", '<link rel="stylesheet" href="/assets/voice.css"></head>')
    source = source.replace(
        "</body>", '<script src="/assets/voice.js" defer></script></body>'
    )
    return source


async def portal_home(request: Request):
    if not _valid_request(request):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse(_page(), headers={"Cache-Control": "no-store"})


async def login_page(request: Request):
    if _valid_request(request):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>Entrar | AGCN Live Voice</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;
place-items:center;background:radial-gradient(ellipse at 20% 10%,#123260,#070a0e 58%);
color:#f6f8fd;font:15px system-ui,-apple-system,Segoe UI,sans-serif}
main{width:min(420px,92vw);padding:38px;background:#111925;border:1px solid #30425b;
border-radius:24px;box-shadow:0 25px 80px #0008}.mark{width:52px;height:52px;
display:grid;place-items:center;border-radius:15px;background:#207cf5;font-size:27px;
font-weight:900;margin-bottom:24px}h1{margin:0;font-size:27px;letter-spacing:-.7px}
p{color:#a9b8c9;line-height:1.5}label{display:block;margin:27px 0 8px;font-weight:600}
input{width:100%;padding:15px;background:#070d16;border:1px solid #455870;
border-radius:12px;color:#fff;font-size:16px}button{width:100%;margin-top:14px;padding:15px;
background:#227cf5;color:white;border:0;border-radius:12px;font-weight:700;
font-size:16px;cursor:pointer}small{display:block;margin-top:19px;color:#889bb3}
#error{min-height:22px;color:#ffa2a2;margin-top:12px}
</style></head><body><main><div class="mark">A</div>
<h1>AGCN Live Voice</h1><p>Painel privado da AGCN Digital. Acesse seu sistema
de apresentação inteligente para lives.</p><form id="form">
<label for="password">Senha de acesso</label><input id="password" type="password"
autocomplete="current-password" required autofocus placeholder="Sua senha">
<button type="submit">Entrar no painel</button><div id="error" role="alert"></div></form>
<small>Conexão protegida • AGCN Digital</small></main>
<script>
document.getElementById('form').onsubmit=async e=>{
e.preventDefault();const button=e.target.querySelector('button');
const error=document.getElementById('error');error.textContent='';button.disabled=true;
try{const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:document.getElementById('password').value})});
if(!r.ok){const d=await r.json().catch(()=>({}));throw new Error(d.detail||'Falha no acesso');}
location.replace('/');}catch(err){error.textContent=err.message;button.disabled=false;}
};
</script></body></html>""",
        headers={"Cache-Control": "no-store"},
    )


async def auth_login(request: Request):
    if not _secret() or not _password():
        return JSONResponse({"detail": "Acesso privado ainda não configurado."}, status_code=503)
    if not _same_origin(request.headers.get("origin"), request.headers.get("host", "")):
        return JSONResponse({"detail": "Origem inválida."}, status_code=403)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    recent = _attempts[ip]
    while recent and now - recent[0] > 300:
        recent.popleft()
    if len(recent) >= 8:
        return JSONResponse({"detail": "Muitas tentativas. Tente novamente em alguns minutos."}, status_code=429)
    try:
        payload = await request.json()
        candidate = str(payload.get("password", ""))[:256]
    except (ValueError, AttributeError):
        candidate = ""
    if not hmac.compare_digest(candidate, _password()):
        recent.append(now)
        return JSONResponse({"detail": "Senha incorreta."}, status_code=401)
    recent.clear()
    expiry = str(int(now) + TTL_SECONDS)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        COOKIE, expiry + "." + _signature(expiry),
        max_age=TTL_SECONDS, httponly=True, secure=True, samesite="strict", path="/",
    )
    return response


async def auth_logout(request: Request):
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response


async def voice_info(request: Request):
    return JSONResponse({
        "ok": True,
        "server_tts": False,
        "browser_tts": True,
        "browser_audio_requires_activation": True,
        "windows_virtual_microphone": False,
        "message": "O navegador pode falar o texto da IA localmente. Para enviar som ao TikTok LIVE Studio, configure a saída de áudio do computador/VB-CABLE ou use o aplicativo Windows."
    })


async def web_css(request: Request):
    return FileResponse(ROOT / "web" / "voice.css", media_type="text/css")


async def web_js(request: Request):
    return FileResponse(ROOT / "web" / "voice.js", media_type="text/javascript")


# Rotas adicionadas sem alterar a aplicação Windows nem o núcleo do Presenter.
backend_app.router.routes.insert(0, Route("/", portal_home, methods=["GET"]))
backend_app.add_route("/login", login_page, methods=["GET"])
backend_app.add_route("/auth/login", auth_login, methods=["POST"])
backend_app.add_route("/auth/logout", auth_logout, methods=["POST"])
backend_app.add_route("/api/voice/info", voice_info, methods=["GET"])
backend_app.add_route("/assets/voice.css", web_css, methods=["GET"])
backend_app.add_route("/assets/voice.js", web_js, methods=["GET"])


@backend_app.middleware("http")
async def protect_http(request: Request, call_next):
    path = request.url.path
    public = path in {"/health", "/login", "/auth/login"} or path.startswith("/assets/")
    if not public and not _valid_request(request):
        if path.startswith("/api/") or path == "/auth/logout":
            return JSONResponse({"detail": "Sessão encerrada. Faça login novamente."}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(
        request.headers.get("origin"), request.headers.get("host", "")
    ):
        return JSONResponse({"detail": "Origem inválida."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


class ProtectedASGI:
    """Protege também a rota WebSocket /ws, ignorada pelo middleware HTTP."""

    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            headers = scope.get("headers", [])
            host = next((v.decode("latin-1") for k, v in headers if k.lower() == b"host"), "")
            origin = next((v.decode("latin-1") for k, v in headers if k.lower() == b"origin"), None)
            if not _valid_token(_cookie_from_headers(headers)) or not _same_origin(origin, host):
                await send({"type": "websocket.close", "code": 4401})
                return
        await self.wrapped(scope, receive, send)


app = ProtectedASGI(backend_app)
