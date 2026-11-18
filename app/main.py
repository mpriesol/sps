"""Authenticated mobile receiving and parcel-location application."""
import asyncio
import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from .config import Settings
from .courier_passwords import CourierPasswords
from .sps import CourierCredentialsError, DemoSPS, SPSClient, SPSError, OperationUncertainError, normalize_code
from .warehouse import Warehouse, WarehouseError
from .warehouse_api import register_warehouse_routes
from .lifecycle import install_lifecycle_routes

COOKIE = "sps_receiving_session"


class LoginBody(BaseModel):
    password: SecretStr = Field(max_length=1024)


class NumbersBody(BaseModel):
    numbers: list[str] = Field(min_length=1, max_length=200)


class AcceptBody(BaseModel):
    number: str = Field(min_length=1, max_length=100)
    courier_id: str | int
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    operation_id: str | None = Field(default=None, min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_:-]+$")


class CourierBody(BaseModel):
    courier_id: str | int


class CourierPasswordBody(CourierBody):
    password: SecretStr = Field(min_length=1, max_length=1024)


def create_app(settings=None, sps=None, static_dir=None):
    cfg = settings or Settings.from_env()
    client = sps or (DemoSPS() if cfg.demo else SPSClient(cfg.sps_username, cfg.sps_password))
    sessions, failures = {}, []
    active_operations = set()
    expected_password = hashlib.sha256(cfg.app_password.encode()).digest()
    account = "demo" if cfg.demo else hashlib.sha256(cfg.sps_username.encode()).hexdigest()[:16]
    passwords = CourierPasswords(Path(cfg.data_dir) / f"couriers-{account}.json")
    warehouse = Warehouse(Path(cfg.data_dir) / f"warehouse-{account}.sqlite3")

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.close()

    root = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    root.state.sps = client
    root.state.warehouse = warehouse

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
            max_body = 2_500_000 if path == "/api/parcels/photo" else (500_000 if path in {"/api/locations/move", "/api/inventory/check", "/api/returns/stage"} else 65536)
            if int(request.headers.get("content-length", "0")) > max_body:
                raise HTTPException(413, "Požiadavka je príliš veľká.")
            response = await call_next(request)
        except HTTPException as exc:
            response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        except ValueError:
            response = JSONResponse({"detail": "Neplatná požiadavka."}, status_code=400)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(self), microphone=(self), geolocation=()",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'",
        })
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        # Do not include Pydantic's original input, which can contain a password.
        return JSONResponse({"detail": "Skontrolujte vyplnené údaje."}, status_code=422)

    @app.exception_handler(SPSError)
    async def sps_error(request, exc):
        return JSONResponse({"detail": str(exc), "needs_credentials": isinstance(exc, CourierCredentialsError),
                             "outcome": "uncertain" if isinstance(exc, OperationUncertainError) else "rejected"}, status_code=502)

    @app.exception_handler(OSError)
    async def storage_error(request, exc):
        return JSONResponse({"detail": "Údaje sa nepodarilo uložiť. Skontrolujte dátový volume aplikácie."}, status_code=500)

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse({"detail": "Databáza je momentálne nedostupná. Ak prebiehalo potvrdenie SPS, najprv overte jeho výsledok."}, status_code=503)

    def session_payload(session):
        return {"csrf": session["csrf"], "demo": cfg.demo, "account": account,
                "actor": session["actor"], "capabilities": getattr(client, "capabilities", {})}

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
            "shipments": {}, "allowed_numbers": set(), "couriers": {}, "return_couriers": {},
            "return_numbers": set(), "pickups": {}, "actor": "Obsluha " + hashlib.sha256(token.encode()).hexdigest()[:6],
        }
        response = JSONResponse(session_payload(sessions[hashlib.sha256(token.encode()).hexdigest()]))
        response.set_cookie(COOKIE, token, secure=cfg.secure_cookie, httponly=True, samesite="strict",
                            path=cfg.base_path + "/", max_age=cfg.session_hours * 3600)
        return response

    @app.get("/api/session")
    async def session(request: Request):
        return session_payload(session_for(request))

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
        session["couriers"] = {str(c["id"]): c for c in choices}
        return courier_payload(choices)

    def courier_payload(choices):
        preferences = warehouse.get_settings()
        last = preferences.get("last_courier_id")
        last = str(last) if last is not None else None
        match = next((c for c in choices if str(c["id"]) == last), None)
        first = choices[0]["id"] if choices else None
        return {"couriers": [{**c, "has_password": passwords.password(c["id"]) is not None} for c in choices],
                "suggested": match["id"] if match else (first if last is None else None),
                "first_id": first, "last_courier_id": last,
                "requires_selection": last is not None and match is None,
                "warning_enabled": preferences.get("courier_warning", True)}

    @app.get("/api/courier-passwords")
    async def saved_couriers():
        return {"couriers": passwords.couriers()}

    @app.post("/api/courier-passwords/reveal")
    async def reveal_courier(body: CourierBody):
        # Reveal only the selected saved entry after an explicit authenticated,
        # same-origin, CSRF-protected request. Lists never include credentials.
        password = passwords.password(body.courier_id)
        if password is None:
            raise HTTPException(404, "Pre tohto kuriéra nie je uložené heslo.")
        return {"password": password}

    @app.post("/api/courier-passwords/save")
    async def save_courier(request: Request, body: CourierPasswordBody):
        choices = {str(c["id"]): c for c in passwords.couriers()}
        choices.update(session_for(request)["couriers"])
        choices.update(session_for(request)["return_couriers"])
        courier = choices.get(str(body.courier_id))
        if not courier:
            raise HTTPException(422, "Vyberte kuriéra zo zoznamu SPS.")
        passwords.save(courier, body.password.get_secret_value())
        return {"ok": True}

    @app.post("/api/courier-passwords/remove")
    async def remove_courier(body: CourierBody):
        passwords.remove(body.courier_id)
        return {"ok": True}

    @app.post("/api/accept")
    async def accept(request: Request, body: AcceptBody):
        session = session_for(request)
        code = normalize_code(body.number)
        if code not in session["allowed_numbers"] or code not in session["shipments"]:
            raise HTTPException(409, "Zásielka nie je vo vybranej dávke. Vyberte balíky znova.")
        if str(body.courier_id) not in session["couriers"]:
            raise HTTPException(422, "Vyberte kuriéra zo zoznamu SPS.")
        number = session["shipments"][code]["trackNumber"]
        courier = session["couriers"][str(body.courier_id)]
        parcel = warehouse.get_parcel(code)
        if parcel and parcel["carrier_status"] == "accepted":
            # A browser retry or another phone must never repeat an acknowledged write.
            return {"number": number, "accepted": True, "already_accepted": True, "password_saved": True}
        if parcel and parcel["carrier_status"] in {"delivered", "returned", "delivery_uncertain", "return_uncertain"}:
            raise HTTPException(409, "Balík už bol vydaný alebo čaká na overenie výdaja. Nemožno ho znovu prijať.")
        password = body.password.get_secret_value() if body.password is not None else passwords.password(courier["id"])
        if password is None:
            raise CourierCredentialsError("Pre tohto kuriéra ešte nie je uložené heslo. Zadajte ho.")
        warehouse.register_parcel(number, session["shipments"][code].get("senderName") or "")
        operation_id = body.operation_id or secrets.token_hex(16)
        operation = warehouse.begin_carrier_operation(number, "accept", operation_id)
        if operation.get("replayed"):
            if operation["outcome"] == "confirmed":
                return {"number": number, "accepted": True, "already_accepted": True, "password_saved": True}
            raise HTTPException(409, "Tento pokus už skončil chybou. Opravte údaje a potvrďte nový pokus.")
        try:
            active_operations.add((code, "accept"))
            await client.accept(number, courier["id"], password)
        except (OperationUncertainError, asyncio.CancelledError):
            warehouse.finish_carrier_operation(operation_id, "uncertain", carrier_status="accept_uncertain", actor=session["actor"])
            raise
        except SPSError:
            warehouse.finish_carrier_operation(operation_id, "failed")
            raise
        finally:
            active_operations.discard((code, "accept"))
        local_saved = True
        try:
            warehouse.finish_carrier_operation(operation_id, "confirmed", carrier_status="accepted", actor=session["actor"],
                                               details={"courier_id": str(courier["id"]), "courier_name": courier["name"]})
            warehouse.update_settings({"last_courier_id": str(courier["id"])})
        except (OSError, sqlite3.Error):
            # The remote write succeeded. Report that truth even if local bookkeeping failed.
            local_saved = False
        password_saved = True
        if body.password is not None:
            try:
                passwords.save(courier, password)
            except OSError:
                # The parcel is already accepted; do not report it as failed.
                password_saved = False
        return {"number": number, "accepted": True, "password_saved": password_saved, "local_saved": local_saved}

    register_warehouse_routes(app, warehouse, session_for)
    install_lifecycle_routes(app, client, warehouse, session_for, passwords, courier_payload, active_operations)

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
