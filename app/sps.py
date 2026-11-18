"""Adapter for endpoints observed in the official SPS portal (2026-11-17).

No write is retried automatically. Session cookies never leave this process.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Any

import httpx

SPS_ORIGIN = "https://ps.sps-sro.sk"
SPS_API = SPS_ORIGIN + "/psappserver/rest"


class SPSError(Exception):
    pass


class CourierCredentialsError(SPSError):
    pass


class OperationUncertainError(SPSError):
    """A write may have reached SPS; callers must reconcile before any retry."""


def normalize_code(value: str) -> str:
    return value.strip().upper()


def _parcel_rows(rows, *, returns=False, pickup=False):
    """Expose only fields used by our UI; never propagate PINs or phone numbers."""
    result = []
    seen = set()
    for row in rows:
        number = row.get("trackNumber")
        if not isinstance(number, str) or not number.strip():
            raise SPSError("SPS vrátilo zásielku bez platného čísla.")
        key = normalize_code(number)
        if key in seen:
            raise SPSError("SPS vrátilo duplicitné čísla zásielok. Skontrolujte portál SPS.")
        seen.add(key)
        status = row.get("status") or {}
        status_code = status.get("code") if isinstance(status, dict) else None
        if status_code is not None:
            if isinstance(status_code, bool) or not str(status_code).isdigit():
                raise SPSError("SPS vrátilo neplatný stav zásielky.")
            status_code = int(status_code)
        if returns and status_code is None:
            raise SPSError("SPS neposkytlo stav zásielky na vrátenie. Overte ju v portáli SPS.")
        cod = row.get("cod")
        if cod is not None:
            if isinstance(cod, bool) or not isinstance(cod, (int, float)) or not math.isfinite(cod) or cod < 0:
                raise SPSError("SPS vrátilo neplatnú sumu dobierky. Overte ju v portáli SPS.")
        elif pickup:
            raise SPSError("SPS neposkytlo sumu dobierky. Výdaj overte v portáli SPS.")
        expiry = row.get("expiredDate")
        if expiry is not None and not isinstance(expiry, str):
            raise SPSError("SPS vrátilo neplatný dátum expirácie.")
        parcel = {
            "trackNumber": key,
            "senderName": str(row.get("senderName") or ""),
            "recipientName": str(row.get("recipientName") or ""),
            "expiredDate": expiry,
            "statusCode": status_code,
            "statusLabel": str((status.get("description") or status.get("name") or "") if isinstance(status, dict) else ""),
            "cod": cod,
            "psRemark": str(row.get("psRemark") or ""),
        }
        if returns:
            # The portal disables handover for status 5555. An administrator
            # must first resolve that shipment in SPS; never bypass this gate.
            parcel["returnEligible"] = status_code != 5555
            courier = row.get("entryCourier")
            parcel["entryCourier"] = courier if isinstance(courier, (str, int)) and not isinstance(courier, bool) else None
        result.append(parcel)
    return result


@dataclass
class SPSClient:
    username: str
    password: str
    transport: Any = None

    capabilities = {"stored_shipments": True, "pickup_lookup": True, "delivery": True, "returns": True,
                    "receipts": True, "payment_request": False}

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
        self.payment_terminal = False
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
            parcelshop = user["parcelshop"]
            self.payment_terminal = isinstance(parcelshop, dict) and parcelshop.get("paymentTerminal") is True
        except (httpx.HTTPError, ValueError) as exc:
            raise SPSError("SPS prihlásenie nie je dostupné alebo vrátilo neočakávanú odpoveď.") from exc
        self.logged_in = True

    async def _read(self, method: str, path: str, body=None, *, params=None, single=False):
        async with self.lock:
            try:
                if not self.logged_in:
                    await self._login()
                kwargs = {"params": params}
                if body is not None:
                    kwargs["json"] = body
                r = await self.http.request(method, path, **kwargs)
                if r.status_code == 401:
                    self.logged_in = False
                    await self._login()
                    r = await self.http.request(method, path, **kwargs)
                if r.status_code != 200:
                    raise SPSError(f"Načítanie údajov zo SPS zlyhalo (HTTP {r.status_code}).")
                data = r.json()
                valid = data is None or isinstance(data, dict) if single else isinstance(data, list) and all(isinstance(x, dict) for x in data)
                if not valid:
                    raise SPSError("SPS vrátilo zmenený formát údajov. Operácia je pozastavená.")
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

    async def stored_shipments(self, number=""):
        rows = await self._read("GET", "getStoredShipments", params={"trackNumber": normalize_code(number)})
        return _parcel_rows(rows)

    async def lookup_pickup(self, pin):
        pin = str(pin).strip()
        if not pin or len(pin) > 128:
            raise SPSError("Zadajte platný výdajný PIN.")
        row = await self._read("GET", "getShipmentDetails", params={"smsKey": pin, "logInfo": "false"}, single=True)
        if row is None:
            return None
        parcel = _parcel_rows([row], pickup=True)[0]
        parcel["paymentTerminalAllowed"] = self.payment_terminal
        return parcel

    async def expired_shipments(self):
        return _parcel_rows(await self._read("GET", "shipment/expired"), returns=True)

    async def _write(self, method, path, body, *, courier=False, require_terminal=False):
        async with self.lock:
            # Login failure is known to happen before any mutation request.
            if not self.logged_in:
                await self._login()
            if require_terminal and not self.payment_terminal:
                raise SPSError("SPS nemá pre toto výdajné miesto povolený platobný terminál.")
            try:
                r = await self.http.request(method, path, json=body)
            except httpx.HTTPError as exc:
                raise OperationUncertainError("Spojenie so SPS sa prerušilo po odoslaní. Výsledok nie je známy. Pred opakovaním overte stav v SPS.") from exc
            if 200 <= r.status_code < 300:
                # Angular's portal expects JSON or an empty response. A login
                # page returned with HTTP 200 is not a confirmed parcel write.
                if r.status_code == 202 or "text/html" in r.headers.get("content-type", "").lower():
                    raise OperationUncertainError("SPS neposkytlo jednoznačné potvrdenie. Pred opakovaním overte stav v SPS.")
                if r.content.strip():
                    try:
                        response = r.json()
                    except ValueError as exc:
                        raise OperationUncertainError("SPS vrátilo neočakávané potvrdenie. Pred opakovaním overte stav v SPS.") from exc
                    if response is False or isinstance(response, dict) and (response.get("error") or response.get("success") is False):
                        raise OperationUncertainError("SPS vrátilo nejednoznačný výsledok. Pred opakovaním overte stav v SPS.")
                return
            if r.status_code == 401:
                self.logged_in = False
                raise SPSError("Prihlásenie do SPS vypršalo. Obnovte údaje a skontrolujte zostávajúce balíky.")
            if r.status_code == 404 and courier:
                raise CourierCredentialsError("SPS odmietlo potvrdenie. Vyberte kuriéra a zadajte jeho heslo.")
            delivery_errors = {
                461: "Zásielka nie je v SPS vedená na tomto výdajnom mieste.",
                462: "Dobierka nebola potvrdená. Najprv overte platbu.",
                463: "Dobierka už bola zaplatená. Obnovte údaje zásielky.",
                464: "SPS nedokázalo overiť platbu. Overte ju v portáli SPS.",
                465: "Stav zásielky sa v SPS medzičasom zmenil. Obnovte údaje.",
            }
            if r.status_code in delivery_errors:
                raise SPSError(delivery_errors[r.status_code])
            if 400 <= r.status_code < 500 and r.status_code != 408:
                raise SPSError(f"SPS odmietlo operáciu (HTTP {r.status_code}). Skontrolujte zásielku v SPS.")
            raise OperationUncertainError(f"SPS nepotvrdilo výsledok (HTTP {r.status_code}). Pred opakovaním overte stav v SPS.")

    async def accept(self, number, courier_id, courier_password):
        await self._write("POST", "shipment/accept", {
            "trackNumber": number, "courierId": courier_id, "password": courier_password,
        }, courier=True)

    async def deliver(self, pin, cod_confirmed=False, paid_with_terminal=False):
        if cod_confirmed and paid_with_terminal:
            raise SPSError("Vyberte iba jeden spôsob potvrdenia dobierky.")
        await self._write("PUT", "shipmentDelivery", {
            "smsKey": pin, "state": 40, "codConfirmed": bool(cod_confirmed),
            "paidWithPaymentTerminal": bool(paid_with_terminal),
        }, require_terminal=bool(paid_with_terminal))

    async def return_parcel(self, number, courier_id, courier_password):
        await self._write("PUT", "shipment/return", {
            "trackNumber": number, "courierId": courier_id, "password": courier_password,
        }, courier=True)

    async def receipt(self, number, update_number=True):
        """Download the carrier PDF, separately from an already completed delivery.

        update_number=True mirrors first issuance in the portal. Reprints and
        recovery after an uncertain receipt request must use False.
        """
        result = await self._read("POST", "hash", {"text": number}, single=True)
        receipt_hash = result.get("hash") if isinstance(result, dict) else None
        if not isinstance(receipt_hash, str) or not receipt_hash or len(receipt_hash) > 4096:
            raise SPSError("SPS neposkytlo platný odkaz na doklad. Výdaj tým nie je zrušený.")
        error = OperationUncertainError if update_number else SPSError
        async with self.lock:
            if not self.logged_in:
                await self._login()
            try:
                response = await self.http.get("getreceiptdocuments", params={
                    "trackNumber": number, "hash": receipt_hash,
                    "updateReceiptNumber": "true" if update_number else "false",
                })
            except httpx.HTTPError as exc:
                raise error("Doklad sa nepodarilo stiahnuť. Výdaj je dokončený; obnovte doklad bez opakovania výdaja.") from exc
            if response.status_code == 401:
                self.logged_in = False
                raise SPSError("Prihlásenie do SPS vypršalo. Obnovte doklad bez opakovania výdaja.")
            if response.status_code != 200 or not response.content.startswith(b"%PDF-") or len(response.content) > 20 * 1024 * 1024:
                raise error("SPS neposkytlo platný PDF doklad. Výdaj tým nie je zrušený; overte doklad v SPS.")
            return response.content


class DemoSPS:
    """Entirely local test data. It never makes a network request."""
    def __init__(self):
        self.received = set()
        self.completed = set()
        self.delivered = {}
        self.parcels = {
            "DEMOSTORED01": self._demo_parcel("DEMOSTORED01", cod=12.50),
            "DEMOEXPIRED01": self._demo_parcel("DEMOEXPIRED01", days=-1),
            "DEMOBLOCKED01": self._demo_parcel("DEMOBLOCKED01", days=-1, status=5555),
        }
        self.pins = {"111111": "DEMOSTORED01", "222222": "DEMOEXPIRED01", "333333": "DEMOBLOCKED01"}

    capabilities = SPSClient.capabilities.copy()

    @staticmethod
    def _demo_parcel(number, *, cod=0, days=7, status=441):
        return {"trackNumber": number, "senderName": "Skúšobný odosielateľ", "recipientName": "Demo zákazník",
                "expiredDate": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(),
                "statusCode": status, "statusLabel": "Vyžaduje vyriešenie v SPS" if status == 5555 else "Uložená na výdajnom mieste",
                "cod": cod, "psRemark": "", "entryCourier": 1, "returnEligible": status != 5555}

    async def close(self):
        pass

    async def shipments(self):
        return [{"trackNumber": f"DEMO{n:08d}", "senderName": f"Skúšobný odosielateľ {n}",
                 "tourDate": None, "tourCourier": 1} for n in range(1, 11) if f"DEMO{n:08d}" not in self.received]

    async def couriers(self, numbers):
        return [{"id": 1, "code": "DEMO", "name": "Skúšobný kuriér"},
                {"id": 2, "code": "DEMO2", "name": "Druhý skúšobný kuriér"}]

    async def accept(self, number, courier_id, courier_password):
        await asyncio.sleep(0.15)
        if courier_password != "demo":
            raise CourierCredentialsError("V demo režime použite heslo kuriéra: demo.")
        if str(courier_id) not in {"1", "2"}:
            raise CourierCredentialsError("Vyberte platného skúšobného kuriéra.")
        if number in self.received or number in self.completed:
            raise SPSError("Skúšobná zásielka už bola prijatá.")
        if number not in {f"DEMO{n:08d}" for n in range(1, 11)}:
            raise SPSError("Skúšobná zásielka nie je v zozname na príjem.")
        self.received.add(number)
        self.parcels[number] = self._demo_parcel(number)
        self.pins[number[-6:]] = number

    async def stored_shipments(self, number=""):
        key = normalize_code(number)
        return [dict(p) for p in self.parcels.values() if not key or p["trackNumber"] == key]

    async def lookup_pickup(self, pin):
        number = self.pins.get(str(pin).strip())
        return {**self.parcels[number], "paymentTerminalAllowed": True} if number in self.parcels else None

    async def expired_shipments(self):
        now = datetime.now(timezone.utc)
        return [dict(p) for p in self.parcels.values() if datetime.fromisoformat(p["expiredDate"]) < now]

    async def deliver(self, pin, cod_confirmed=False, paid_with_terminal=False):
        parcel = await self.lookup_pickup(pin)
        if not parcel:
            raise SPSError("Zásielka s týmto PIN-om nebola nájdená.")
        if cod_confirmed and paid_with_terminal:
            raise SPSError("Vyberte iba jeden spôsob potvrdenia dobierky.")
        if parcel["cod"] > 0 and not (cod_confirmed or paid_with_terminal):
            raise SPSError("Dobierka nebola potvrdená. Najprv overte platbu.")
        self.completed.add(parcel["trackNumber"])
        self.delivered[parcel["trackNumber"]] = parcel
        self.parcels.pop(parcel["trackNumber"])

    async def return_parcel(self, number, courier_id, courier_password):
        if courier_password != "demo" or str(courier_id) not in {"1", "2"}:
            raise CourierCredentialsError("V demo režime vyberte kuriéra a použite heslo: demo.")
        parcel = next((p for p in await self.expired_shipments() if p["trackNumber"] == number), None)
        if not parcel or not parcel["returnEligible"]:
            raise SPSError("Zásielka nie je pripravená na odovzdanie kuriérovi.")
        self.completed.add(number)
        self.parcels.pop(number)

    async def receipt(self, number, update_number=True):
        if number not in self.delivered:
            raise SPSError("Skúšobný doklad je dostupný až po výdaji zásielky.")
        # A small valid PDF needs no external service or PDF library. It is
        # explicitly marked DEMO and cannot be mistaken for a carrier receipt.
        label = number.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        amount = self.delivered[number]["cod"]
        stream = f"BT /F1 18 Tf 50 780 Td (DEMO - NOT A PAYMENT RECEIPT) Tj 0 -40 Td /F1 12 Tf (Parcel: {label}) Tj 0 -24 Td (COD: {amount:.2f} EUR) Tj ET".encode("ascii")
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                   b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
                   b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                   b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(pdf)
        pdf.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode())
        pdf.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        return bytes(pdf)
