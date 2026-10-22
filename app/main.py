"""Single-process, database-free mobile receiving app."""
import hashlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .config import Settings
from .sps import DemoSPS, SPSClient, SPSError, normalize_code

COOKIE = "sps_receiving_session"


class LoginBody(BaseModel):
    password: SecretStr = Field(max_length=1024)


class NumbersBody(BaseModel):
    numbers: list[str] = Field(min_length=1, max_length=200)


class AcceptBody(BaseModel):
    number: str = Field(min_length=1, max_length=100)
    courier_id: str | int
    password: SecretStr = Field(min_length=1, max_length=1024)


def create_app(settings=None, sps=None, static_dir=None):
    cfg = settings or Settings.from_env()
    client = sps or (DemoSPS() if cfg.demo else SPSClient(cfg.sps_username, cfg.sps_password))
    sessions, failures = {}, []
    expected_password = hashlib.sha256(cfg.app_password.encode()).digest()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.close()

    root = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    root.state.sps = client

    def session_for(request):
        key = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
        session = sessions.get(key)
        if not session or session["expires"] <= time.time():
            sessions.pop(key, None)
            raise HTTPException(401, "Prihláste sa do aplikácie.")
        return session

    @root.middleware("http")
    async def security(request, call_next):
        path = request.url.path.removeprefix(cfg.base_path)
        try:
            if path.startswith("/api/"):
                if request.method not in {"GET", "HEAD"}:
                    if request.headers.get("origin") != cfg.origin or request.headers.get("x-requested-with") != "sps-app":
                        raise HTTPException(403, "Požiadavka musí pochádzať z tejto aplikácie.")
                if path != "/api/login":
                    session = session_for(request)
                    if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
                        raise HTTPException(403, "Relácia sa zmenila. Obnovte stránku.")
            if int(request.headers.get("content-length", "0")) > 65536:
                raise HTTPException(413, "Požiadavka je príliš veľká.")
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except ValueError:
            response = JSONResponse({"detail": "Neplatná požiadavka."}, status_code=400)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        # Do not include Pydantic's original input, which can contain a password.
        return JSONResponse({"detail": "Skontrolujte vyplnené údaje."}, status_code=422)

    @app.exception_handler(SPSError)
    async def sps_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(body: LoginBody):
        now = time.time()
        failures[:] = [t for t in failures if t > now - 60]
        if len(failures) >= 10:
            raise HTTPException(429, "Príliš veľa pokusov. Počkajte minútu.")
        if not hmac.compare_digest(expected_password, hashlib.sha256(body.password.get_secret_value().encode()).digest()):
            failures.append(now)
            raise HTTPException(401, "Nesprávne heslo aplikácie.")
        for key in list(sessions):
            if sessions[key]["expires"] <= now:
                del sessions[key]
        if len(sessions) >= 100:
            raise HTTPException(429, "Príliš veľa aktívnych prihlásení.")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = {
            "csrf": csrf, "expires": now + cfg.session_hours * 3600,
            "shipments": {}, "allowed_numbers": set(), "couriers": {},
        }
        response = JSONResponse({"csrf": csrf, "demo": cfg.demo})
        response.set_cookie(COOKIE, token, secure=cfg.secure_cookie, httponly=True, samesite="strict",
                            path=cfg.base_path + "/", max_age=cfg.session_hours * 3600)
        return response

    @app.get("/api/session")
    async def session(request: Request):
        return {"csrf": session_for(request)["csrf"], "demo": cfg.demo}

    @app.post("/api/logout")
    async def logout(request: Request):
        sessions.pop(hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest(), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path=cfg.base_path + "/", secure=cfg.secure_cookie, httponly=True, samesite="strict")
        return response

    @app.get("/api/shipments")
    async def shipments(request: Request):
        rows = await client.shipments()
        session_for(request)["shipments"] = {normalize_code(s["trackNumber"]): s for s in rows}
        return {"shipments": rows}

    @app.post("/api/couriers")
    async def couriers(request: Request, body: NumbersBody):
        session = session_for(request)
        numbers = list(dict.fromkeys(normalize_code(n) for n in body.numbers))
        known = session["shipments"]
        if any(n not in known for n in numbers):
            raise HTTPException(409, "Niektorý balík nie je v načítanom zozname SPS. Obnovte zoznam.")
        choices = await client.couriers([known[n]["trackNumber"] for n in numbers])
        session["allowed_numbers"] = set(numbers)
        session["couriers"] = {str(c["id"]): c["id"] for c in choices}
        hints = {str(known[n].get("tourCourier")) for n in numbers}
        suggested = next((c["id"] for c in choices if len(hints) == 1 and str(c["id"]) in hints), None)
        return {"couriers": choices, "suggested": suggested}

    @app.post("/api/accept")
    async def accept(request: Request, body: AcceptBody):
        session = session_for(request)
        code = normalize_code(body.number)
        if code not in session["allowed_numbers"] or code not in session["shipments"]:
            raise HTTPException(409, "Zásielka nie je vo vybranej dávke. Vyberte balíky znova.")
        if str(body.courier_id) not in session["couriers"]:
            raise HTTPException(422, "Vyberte kuriéra zo zoznamu SPS.")
        number = session["shipments"][code]["trackNumber"]
        await client.accept(number, session["couriers"][str(body.courier_id)], body.password.get_secret_value())
        return {"number": number, "accepted": True}

    web = Path(static_dir) if static_dir else Path(__file__).parent.parent / "web" / "dist"
    if (web / "assets").exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/")
    async def index():
        if not (web / "index.html").exists():
            raise HTTPException(503, "Frontend ešte nie je zostavený. Spustite npm ci a npm run build v priečinku web.")
        return FileResponse(web / "index.html")

    @app.get("/favicon.svg")
    async def favicon():
        return FileResponse(web / "favicon.svg")

    if cfg.base_path:
        @root.get("/healthz")
        async def root_health():
            return {"status": "ok"}
        root.mount(cfg.base_path, app)
    else:
        root.mount("/", app)
    return root
