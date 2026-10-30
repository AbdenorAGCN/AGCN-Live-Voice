"""AGCN Live Voice customer portal. Desktop runtime stays local on Windows."""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="AGCN Live Voice — Clientes", docs_url=None, redoc_url=None, openapi_url=None)
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("AGCN_DATA_DIR", str(ROOT / "data" / "runtime")))
PUBLIC = os.getenv("AGCN_PUBLIC_URL", "https://voice.agcndigital.com.br").rstrip("/")
COOKIE = "agcn_voice_customer"
INFINITE_API = "https://api.checkout.infinitepay.io"
DB_PATH = DATA / "customers.sqlite3"
ATTEMPTS: dict[str, list[int]] = {}


def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@app.on_event("startup")
def init_database():
    with db() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                checkout_url TEXT, transaction_nsu TEXT, slug TEXT,
                created_at INTEGER NOT NULL, paid_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS licenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, order_id TEXT NOT NULL UNIQUE,
                license_key TEXT NOT NULL UNIQUE, issued_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
            CREATE INDEX IF NOT EXISTS idx_license_user ON licenses(user_id);
        """)


def clean(value):
    return html.escape(str(value or ""), quote=True)


def money(cents: int) -> str:
    return "R$ " + f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def settings():
    def numeric(name, default=0):
        try:
            return max(0, int(os.getenv(name, str(default))))
        except ValueError:
            return 0

    return {
        "price": numeric("AGCN_LICENSE_PRICE_CENTS"),
        "days": numeric("AGCN_LICENSE_DAYS"),
        "tag": os.getenv("AGCN_INFINITEPAY_HANDLE", "").strip().lstrip("$"),
        "key": os.getenv("AGCN_INFINITEPAY_API_KEY", "").strip(),
        "installer": os.getenv("AGCN_INSTALLER_KEY", "").strip(),
    }


def ready_to_sell():
    s = settings()
    return s["price"] >= 100 and s["days"] > 0 and bool(s["tag"] and s["key"])


def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; "
        "form-action 'self' https://buy.infinitepay.io; base-uri 'none'; frame-ancestors 'none'"
    )
    return resp


def page(body, title="AGCN Live Voice", user=None):
    customer = bool(user)
    nav = (
        '<a href="/conta">Minha conta</a><form method="post" action="/sair"><button class="navlink" type="submit">Sair</button></form>'
        if customer else '<a href="/entrar">Entrar</a><a class="pill small" href="/cadastro">Criar conta</a>'
    )
    initials = '<span class="mark"><b>A</b></span>'
    markup = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#090e18">
<meta name="description" content="AGCN Live Voice — apresentador inteligente para transmissões ao vivo. Licenciamento e download para Windows.">
<title>{clean(title)}</title><link rel="stylesheet" href="/assets/portal.css"></head>
<body><header class="top"><div class="wrap topinner"><a class="brand" href="/">{initials}
<span><strong>AGCN</strong><small>LIVE VOICE</small></span></a><nav>{nav}</nav></div></header>
<main>{body}</main><footer class="foot"><div class="wrap footerinner">
<span>© AGCN Digital · AGCN Live Voice</span><span>Software para Windows 64 bits · Atendimento e condições comerciais disponíveis antes da compra.</span>
</div></footer></body></html>"""
    return no_cache(HTMLResponse(markup))


def get_user(request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        return None
    with db() as c:
        row = c.execute(
            "SELECT users.id, users.name, users.email FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE sessions.token_hash=? AND sessions.expires_at>?",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time())),
        ).fetchone()
    return row


def require_user(request):
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Faça login para continuar.")
    return user


def session_response(user_id, target="/conta"):
    token = secrets.token_urlsafe(40)
    now = int(time.time())
    with db() as c:
        c.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
        c.execute(
            "INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), user_id, now + 14 * 86400),
        )
    res = RedirectResponse(target, status_code=303)
    res.set_cookie(COOKIE, token, max_age=14 * 86400, secure=PUBLIC.startswith("https://"),
                   httponly=True, samesite="lax", path="/")
    return no_cache(res)


def pbkdf(password, salt=None):
    salt = salt or secrets.token_hex(16)
    result = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310_000)
    return salt + ":" + result.hex()


def valid_password(password, stored):
    try:
        salt, digest = stored.split(":", 1)
        return hmac.compare_digest(pbkdf(password, salt), stored)
    except (ValueError, TypeError):
        return False


async def form_data(request):
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Formulário inválido.")
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "Formulário muito grande.")
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}


def limit(request, key, count=12, period=900):
    ip = request.client.host if request.client else "unknown"
    bucket = f"{key}:{ip}"
    now = int(time.time())
    recent = [x for x in ATTEMPTS.get(bucket, []) if x + period > now]
    if len(recent) >= count:
        raise HTTPException(429, "Muitas tentativas. Tente novamente mais tarde.")
    recent.append(now)
    ATTEMPTS[bucket] = recent


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/webhook/infinitepay":
        origin = request.headers.get("origin") or ""
        if origin and urlparse(origin).netloc != urlparse(PUBLIC).netloc:
            return no_cache(JSONResponse({"erro": "Origem não autorizada"}, status_code=403))
        if request.cookies.get(COOKIE) and not origin:
            # Browser same-origin form POST sends Origin; disallow cookie-bearing blind POSTs.
            return no_cache(JSONResponse({"erro": "Origem obrigatória"}, status_code=403))
    resp = await call_next(request)
    return no_cache(resp)


@app.get("/health")
def health():
    return {"status": "ok", "application": "AGCN Live Voice customer portal"}


@app.get("/assets/portal.css")
def css():
    return FileResponse(ROOT / "web" / "portal.css", media_type="text/css")


@app.get("/")
def home(request: Request):
    user = get_user(request)
    if user:
        action = '<a class="pill" href="/conta">Acessar minha conta <span>↗</span></a>'
    else:
        action = '<a class="pill" href="/cadastro">Começar agora <span>↗</span></a><a class="quiet" href="/entrar">Já tenho uma conta</a>'
    s = settings()
    amount = money(s["price"]) if s["price"] else "Em breve"
    term = f"{s['days']} dias de acesso" if s["days"] else "Duração a definir"
    body = f"""<section class="hero wrap"><div class="eyebrow"><span class="dot"></span> SOFTWARE PARA WINDOWS</div>
<h1>Sua LIVE com mais<br><em>inteligência e presença.</em></h1>
<p class="lead">AGCN Live Voice: um apresentador inteligente para ajudar você a acompanhar comentários, organizar produtos e conduzir suas transmissões ao vivo.</p>
<div class="actions">{action}</div><div class="hero-rule"></div>
<div class="stats"><div><strong>Brain local</strong><span>Processamento no seu PC</span></div><div><strong>Voz neural</strong><span>Geração de áudio no Windows</span></div><div><strong>Windows 64 bits</strong><span>Aplicativo para computador</span></div></div>
</section><section class="details"><div class="wrap"><span class="eyebrow">COMO FUNCIONA</span>
<h2>Tudo em um só lugar.</h2><div class="featuregrid">
<div class="feature"><span class="number">01</span><h3>Crie seu cadastro</h3><p>Acesse uma área exclusiva para acompanhar sua licença.</p></div>
<div class="feature"><span class="number">02</span><h3>Adquira sua licença</h3><p>Pagamento seguro pelo checkout da InfinitePay, quando as vendas estiverem abertas.</p></div>
<div class="feature"><span class="number">03</span><h3>Instale no Windows</h3><p>Após aprovação do pagamento, o instalador é liberado na sua conta quando publicado.</p></div>
</div></div></section>
<section class="price wrap"><div><span class="eyebrow">LICENCIAMENTO</span><h2>Seu espaço de trabalho.<br>Seu assistente inteligente.</h2><p>O software executa seus modelos de IA no computador. O site é utilizado para cadastro, licenciamento e downloads.</p></div>
<div class="pricebox"><span>LICENÇA AGCN LIVE VOICE</span><strong>{amount}</strong><small>{term}</small>
<a class="pill full" href="/cadastro">Criar conta</a><p>Os valores e condições finais serão apresentados antes da contratação.</p></div></section>"""
    return page(body, user=user)


def auth_form(kind, error=""):
    signup = kind == "cadastro"
    title = "Criar sua conta" if signup else "Bem-vindo de volta"
    fields = ('<label>Nome completo<input required name="name" maxlength="100" autocomplete="name" placeholder="Seu nome"></label>'
              if signup else "")
    subtitle = "Cadastre-se para acompanhar sua licença e acessar o instalador." if signup else "Acesse sua licença e seus downloads."
    error_html = f'<div class="error" role="alert">{clean(error)}</div>' if error else ""
    footer = ('Já tem conta? <a href="/entrar">Entrar</a>' if signup
              else 'Ainda não tem conta? <a href="/cadastro">Criar conta</a>')
    return f"""<section class="auth wrap"><div class="authcard"><div class="eyebrow">AGCN LIVE VOICE</div><h1>{title}</h1>
<p>{subtitle}</p>{error_html}<form method="post" action="/{kind}">
{fields}<label>E-mail<input name="email" type="email" required maxlength="254" autocomplete="email" placeholder="voce@exemplo.com"></label>
<label>Senha<input name="password" type="password" minlength="10" maxlength="128" required autocomplete="{ 'new-password' if signup else 'current-password'}" placeholder="Mínimo de 10 caracteres"></label>
<button class="pill full" type="submit">{'Criar conta' if signup else 'Entrar'}</button></form>
<div class="formfoot">{footer}</div></div></section>"""


@app.get("/cadastro")
def signup_view(request: Request):
    if get_user(request):
        return RedirectResponse("/conta", 303)
    return page(auth_form("cadastro"), "Cadastro — AGCN Live Voice")


@app.post("/cadastro")
async def signup(request: Request):
    limit(request, "register", 8, 3600)
    data = await form_data(request)
    name = " ".join(data.get("name", "").strip().split())
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not (2 <= len(name) <= 100 and re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,180}\.[^@\s]+", email)
            and 10 <= len(password) <= 128):
        return page(auth_form("cadastro", "Confira o nome, e-mail e a senha (mínimo de 10 caracteres)."), "Cadastro")
    try:
        with db() as c:
            cur = c.execute(
                "INSERT INTO users(name,email,password,created_at) VALUES (?,?,?,?)",
                (name, email, pbkdf(password), int(time.time())),
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return page(auth_form("cadastro", "Este e-mail já possui cadastro. Faça login."), "Cadastro")
    return session_response(user_id)


@app.get("/entrar")
def login_view(request: Request):
    if get_user(request):
        return RedirectResponse("/conta", 303)
    return page(auth_form("entrar"), "Entrar — AGCN Live Voice")


@app.post("/entrar")
async def login(request: Request):
    limit(request, "login", 14)
    data = await form_data(request)
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    with db() as c:
        user = c.execute("SELECT id,password FROM users WHERE email=?", (email,)).fetchone()
    if not user or not valid_password(password, user["password"]):
        return page(auth_form("entrar", "E-mail ou senha inválidos."), "Entrar")
    return session_response(user["id"])


@app.post("/sair")
def logout(request: Request):
    token = request.cookies.get(COOKIE, "")
    if token:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    res = RedirectResponse("/", 303)
    res.delete_cookie(COOKIE, path="/")
    return no_cache(res)


def active_license(user_id):
    with db() as c:
        return c.execute(
            "SELECT * FROM licenses WHERE user_id=? AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
            (user_id, int(time.time())),
        ).fetchone()


def installer_configured():
    s = settings()
    return all([s["installer"], os.getenv("AGCN_S3_ENDPOINT"), os.getenv("AGCN_S3_BUCKET"),
                os.getenv("AGCN_S3_ACCESS_KEY_ID"), os.getenv("AGCN_S3_SECRET_ACCESS_KEY")])


@app.get("/conta")
def account(request: Request):
    user = get_user(request)
    if not user:
        return RedirectResponse("/entrar", 303)
    lic = active_license(user["id"])
    s = settings()
    with db() as c:
        orders = c.execute("SELECT id, amount, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 8",
                           (user["id"],)).fetchall()
    if lic:
        expiration = datetime.fromtimestamp(lic["expires_at"], timezone.utc).strftime("%d/%m/%Y")
        status = f'<span class="status good">Licença ativa até {expiration}</span>'
        download = (
            '<form method="post" action="/baixar"><button class="pill" type="submit">Baixar para Windows <span>↓</span></button></form>'
            if installer_configured() else
            '<p class="notice">Sua licença está ativa. O instalador completo ainda está sendo preparado para publicação.</p>'
        )
        pay = ""
    else:
        status = '<span class="status pending">Sem licença ativa</span>'
        download = '<p class="notice">O download fica disponível após pagamento aprovado e publicação do instalador.</p>'
        pay = (f'<form method="post" action="/comprar"><button class="pill" type="submit">Adquirir licença · {money(s["price"])}</button></form>'
               if ready_to_sell() else '<p class="notice">A contratação online ainda não foi habilitada. O cadastro já está disponível.</p>')
    details = "".join(f"<tr><td>{datetime.fromtimestamp(o['created_at'], timezone.utc).strftime('%d/%m/%Y')}</td>"
                      f"<td>{money(o['amount'])}</td><td>{clean({'paid':'Aprovado','pending':'Pendente'}.get(o['status'], o['status']))}</td></tr>"
                      for o in orders)
    history = f'<table><thead><tr><th>Data</th><th>Valor</th><th>Situação</th></tr></thead><tbody>{details}</tbody></table>' if orders else '<p>Nenhum pedido realizado.</p>'
    body = f"""<section class="dashboard wrap"><div class="eyebrow">ÁREA DO CLIENTE</div><h1>Olá, {clean(user['name'].split()[0])}.</h1>
<p>Gerencie sua licença e faça o download do aplicativo quando estiver disponível.</p>
<div class="dashboardgrid"><div class="panel"><span class="eyebrow">MINHA LICENÇA</span><h2>AGCN Live Voice</h2>{status}{pay}
<div class="accountline">{clean(user["email"])}</div></div>
<div class="panel"><span class="eyebrow">DOWNLOAD</span><h2>Windows 64 bits</h2>
<p>Versão para computador. O sistema de inteligência e a geração de voz funcionam localmente após a instalação do pacote completo.</p>
{download}</div></div><div class="orders panel"><span class="eyebrow">HISTÓRICO DE PEDIDOS</span><h2>Meus pagamentos</h2>{history}</div>
</section>"""
    return page(body, "Minha conta — AGCN Live Voice", user)


@app.post("/comprar")
async def buy(request: Request):
    user = require_user(request)
    if active_license(user["id"]):
        return RedirectResponse("/conta", 303)
    if not ready_to_sell():
        raise HTTPException(503, "Vendas ainda não habilitadas.")
    s = settings()
    order_id = "agcn-" + secrets.token_hex(14)
    with db() as c:
        c.execute("INSERT INTO orders(id,user_id,amount,status,created_at) VALUES(?,?,?,'pending',?)",
                  (order_id, user["id"], s["price"], int(time.time())))
    payload = {
        "handle": s["tag"],
        "order_nsu": order_id,
        "redirect_url": PUBLIC + "/pagamento/retorno",
        "webhook": {"url": PUBLIC + "/webhook/infinitepay", "events": ["paid"]},
        "items": [{"quantity": 1, "price": s["price"], "description": f"AGCN Live Voice — licença {s['days']} dias"}],
        "customer": {"name": user["name"], "email": user["email"]},
        "options": {"single_payment": True,
                    "valid_until": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()},
    }
    try:
        async with httpx.AsyncClient(timeout=18) as client:
            response = await client.post(INFINITE_API + "/links", json=payload, headers={"X-Api-Key": s["key"]})
            response.raise_for_status()
            answer = response.json()
        url = answer.get("url") or answer.get("checkout_url") or answer.get("link")
        parsed = urlparse(str(url))
        if parsed.scheme != "https" or not (parsed.hostname == "infinitepay.io" or
                                              (parsed.hostname or "").endswith(".infinitepay.io")):
            raise ValueError("URL do checkout não reconhecida.")
    except (httpx.HTTPError, ValueError, TypeError) as err:
        # Failed links never grant access or change order state.
        raise HTTPException(502, "Não foi possível iniciar o checkout. Tente novamente.") from err
    with db() as c:
        c.execute("UPDATE orders SET checkout_url=? WHERE id=?", (url, order_id))
    return RedirectResponse(url, 303)


async def confirm_paid(order_id, slug, transaction):
    if not order_id or not slug or not transaction:
        return False
    if any(len(x) > 200 for x in (order_id, slug, transaction)):
        return False
    with db() as c:
        order = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        return False
    if order["status"] == "paid":
        return True
    s = settings()
    if not s["tag"] or not s["key"]:
        return False
    payload = {"handle": s["tag"], "order_nsu": order_id, "transaction_nsu": transaction, "slug": slug}
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.post(INFINITE_API + "/payment_check", json=payload,
                                     headers={"X-Api-Key": s["key"]})
        response.raise_for_status()
        outcome = response.json()
    if outcome.get("success") is not True or outcome.get("paid") is not True:
        return False
    try:
        if int(outcome.get("amount", -1)) != order["amount"]:
            return False
    except (TypeError, ValueError):
        return False
    now = int(time.time())
    days = s["days"]
    if days <= 0:
        return False
    with db() as c:
        # payment_check proves the amount and order; transaction cannot be reused for another order.
        used = c.execute("SELECT id FROM orders WHERE transaction_nsu=? AND id<>?", (transaction, order_id)).fetchone()
        if used:
            return False
        c.execute("UPDATE orders SET status='paid', transaction_nsu=?, slug=?, paid_at=? "
                  "WHERE id=? AND status='pending'", (transaction, slug, now, order_id))
        if c.execute("SELECT changes()").fetchone()[0]:
            current = c.execute("SELECT MAX(expires_at) FROM licenses WHERE user_id=?", (order["user_id"],)).fetchone()[0]
            expiration = max(now, current or now) + days * 86400
            key = "AGCN-" + "-".join(secrets.token_hex(4).upper() for _ in range(4))
            c.execute("INSERT INTO licenses(user_id,order_id,license_key,issued_at,expires_at) VALUES(?,?,?,?,?)",
                      (order["user_id"], order_id, key, now, expiration))
    return True


@app.get("/pagamento/retorno")
async def payment_return(request: Request, order_nsu: str = "", transaction_nsu: str = "", slug: str = ""):
    user = get_user(request)
    if not user:
        return RedirectResponse("/entrar", 303)
    with db() as c:
        order = c.execute("SELECT id FROM orders WHERE id=? AND user_id=?", (order_nsu, user["id"])).fetchone()
    if order:
        try:
            await confirm_paid(order_nsu, slug, transaction_nsu)
        except (httpx.HTTPError, ValueError):
            pass
    return RedirectResponse("/conta", 303)


@app.post("/webhook/infinitepay")
async def webhook(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(415, "Tipo de conteúdo inválido")
    raw = await request.body()
    if len(raw) > 16_384:
        raise HTTPException(413, "Evento muito grande")
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Evento inválido")
        oid = str(payload.get("order_nsu") or "")
        slug = str(payload.get("invoice_slug") or payload.get("slug") or "")
        txn = str(payload.get("transaction_nsu") or "")
        # InfinitePay webhooks are NOT signed. Verify every candidate using payment_check.
        if oid and slug and txn:
            await confirm_paid(oid, slug, txn)
    except (ValueError, TypeError):
        raise HTTPException(400, "Evento inválido")
    except httpx.HTTPError:
        raise HTTPException(503, "Não foi possível verificar o pagamento")
    return {"ok": True}


@app.post("/baixar")
async def download(request: Request):
    user = require_user(request)
    if not active_license(user["id"]):
        raise HTTPException(403, "Licença inativa.")
    if not installer_configured():
        raise HTTPException(503, "Instalador ainda não publicado.")
    # Temporary S3 URL; never expose permanent public links or server-side access keys.
    import boto3
    endpoint = os.environ["AGCN_S3_ENDPOINT"]
    if urlparse(endpoint).scheme != "https":
        raise HTTPException(503, "Configuração do armazenamento inválida.")
    client = boto3.client(
        "s3", endpoint_url=endpoint, region_name=os.getenv("AGCN_S3_REGION", "sjc"),
        aws_access_key_id=os.environ["AGCN_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AGCN_S3_SECRET_ACCESS_KEY"],
        config=__import__("botocore.config", fromlist=["Config"]).Config(signature_version="s3v4"),
    )
    filename = os.getenv("AGCN_INSTALLER_FILENAME", "AGCN-Live-Voice-Completo.zip")
    safe_filename = re.sub(r"[^a-zA-Z0-9._-]", "_", filename)
    url = client.generate_presigned_url("get_object", Params={
        "Bucket": os.environ["AGCN_S3_BUCKET"], "Key": os.environ["AGCN_INSTALLER_KEY"],
        "ResponseContentDisposition": f'attachment; filename="{safe_filename}"',
    }, ExpiresIn=120)
    return no_cache(RedirectResponse(url, 303))
