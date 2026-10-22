"""Small adapter for the endpoints observed in the SPS portal on 2026-10-22.

No write is retried automatically. Session cookies never leave this process.
"""
import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

SPS_ORIGIN = "https://ps.sps-sro.sk"
SPS_API = SPS_ORIGIN + "/psappserver/rest"


class SPSError(Exception):
    pass


class CourierCredentialsError(SPSError):
    pass


def normalize_code(value: str) -> str:
    return value.strip().upper()


@dataclass
class SPSClient:
    username: str
    password: str
    transport: Any = None

    def __post_init__(self):
        self.http = httpx.AsyncClient(
            base_url=SPS_API + "/",
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=False,
            transport=self.transport,
            headers={"Accept": "application/json", "Origin": SPS_ORIGIN,
                     "Referer": SPS_ORIGIN + "/accept-shipment"},
        )
        self.logged_in = False
        self.lock = asyncio.Lock()

    async def close(self):
        await self.http.aclose()

    async def _login(self):
        try:
            r = await self.http.post("login", json={"username": self.username, "password": self.password})
            if r.status_code != 200:
                raise SPSError("Prihlásenie do SPS zlyhalo. Skontrolujte účet v konfigurácii servera.")
            user = r.json()
            if not isinstance(user, dict) or not user.get("parcelshop"):
                raise SPSError("SPS účet nemá priradené výdajné miesto. Overte ho v portáli SPS.")
        except (httpx.HTTPError, ValueError) as exc:
            raise SPSError("SPS prihlásenie nie je dostupné alebo vrátilo neočakávanú odpoveď.") from exc
        self.logged_in = True

    async def _read(self, method: str, path: str, body=None):
        async with self.lock:
            try:
                if not self.logged_in:
                    await self._login()
                r = await self.http.request(method, path, json=body) if body is not None else await self.http.request(method, path)
                if r.status_code == 401:
                    self.logged_in = False
                    await self._login()
                    r = await self.http.request(method, path, json=body) if body is not None else await self.http.request(method, path)
                if r.status_code != 200:
                    raise SPSError(f"Načítanie údajov zo SPS zlyhalo (HTTP {r.status_code}).")
                data = r.json()
                if not isinstance(data, list) or any(not isinstance(x, dict) for x in data):
                    raise SPSError("SPS vrátilo zmenený formát údajov. Príjem je pozastavený.")
                return data
            except (httpx.HTTPError, ValueError) as exc:
                raise SPSError("Nepodarilo sa načítať údaje zo SPS. Skúste obnoviť zoznam.") from exc

    async def shipments(self):
        rows = await self._read("GET", "shipment/tour")
        result = []
        seen = set()
        for row in rows:
            if not isinstance(row.get("trackNumber"), str) or not row["trackNumber"].strip():
                raise SPSError("SPS vrátilo zásielku bez platného čísla.")
            key = normalize_code(row["trackNumber"])
            if key in seen:
                raise SPSError("SPS vrátilo duplicitné čísla zásielok. Skontrolujte portál SPS.")
            seen.add(key)
            result.append({"trackNumber": row["trackNumber"], "senderName": row.get("senderName") or "",
                           "tourDate": row.get("tourDate"), "tourCourier": row.get("tourCourier")})
        return result

    async def couriers(self, numbers):
        rows = await self._read("POST", "shipment/couriers", {"trackNumbers": numbers})
        if not rows or any(x.get("id") is None for x in rows):
            raise SPSError("SPS neposkytlo platný zoznam kuriérov pre vybrané balíky.")
        return [{"id": x["id"], "code": str(x.get("code") or ""), "name": str(x.get("name") or "")} for x in rows]

    async def accept(self, number, courier_id, courier_password):
        async with self.lock:
            # Login failure is known to happen before any acceptance request.
            if not self.logged_in:
                await self._login()
            try:
                r = await self.http.post("shipment/accept", json={
                    "trackNumber": number, "courierId": courier_id, "password": courier_password,
                })
            except httpx.HTTPError as exc:
                raise SPSError("Spojenie so SPS sa prerušilo. Príjem môžete zopakovať.") from exc
            if 200 <= r.status_code < 300 and "text/html" not in r.headers.get("content-type", "").lower():
                return
            if r.status_code == 401:
                self.logged_in = False
                raise SPSError("Prihlásenie do SPS vypršalo. Obnovte zoznam a potvrďte zostávajúce balíky.")
            if r.status_code == 404:
                raise CourierCredentialsError("SPS odmietlo potvrdenie. Vyberte kuriéra a zadajte jeho heslo.")
            if r.status_code in {400, 403, 409, 422, 429}:
                raise SPSError(f"SPS odmietlo príjem (HTTP {r.status_code}). Skontrolujte zásielku v SPS.")
            raise SPSError(f"SPS nepotvrdilo príjem (HTTP {r.status_code}). Príjem môžete zopakovať.")


class DemoSPS:
    """Entirely local test data. It never makes a network request."""
    def __init__(self):
        self.received = set()

    async def close(self):
        pass

    async def shipments(self):
        return [{"trackNumber": f"DEMO{n:08d}", "senderName": f"Skúšobný odosielateľ {n}",
                 "tourDate": None, "tourCourier": 1} for n in range(1, 11) if f"DEMO{n:08d}" not in self.received]

    async def couriers(self, numbers):
        return [{"id": 1, "code": "DEMO", "name": "Skúšobný kuriér"}]

    async def accept(self, number, courier_id, courier_password):
        await asyncio.sleep(0.15)
        if courier_password != "demo":
            raise CourierCredentialsError("V demo režime použite heslo kuriéra: demo.")
        self.received.add(number)
