"""Verified carrier lifecycle actions, kept separate from physical shelving.

PIN lookup never releases a parcel. A short-lived lookup is tied to the current
session and rechecked against SPS; physical scan verification is configurable.
"""
import asyncio
import hashlib
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .sps import CourierCredentialsError, OperationUncertainError, SPSError, normalize_code
from .warehouse import parcel_number


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lookup(Body):
    pin: SecretStr = Field(min_length=1, max_length=128)


class ConfirmPickup(Body):
    lookup_id: str = Field(min_length=1, max_length=100)
    scanned_number: str | None = Field(default=None, min_length=1, max_length=100)
    cod_confirmed: bool = False
    paid_with_terminal: bool = False
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_:-]+$")


class RefreshPickup(Body):
    lookup_id: str = Field(min_length=1, max_length=100)


class ConfirmDropoff(Body):
    # Intent guard for a new, uncatalogued full code. This is not a claim about
    # SPS's tracking-number format: SPS validates the actual label itself.
    number: str = Field(min_length=6, max_length=100)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_:-]+$")
    scanned: bool = False


class ReturnNumbers(Body):
    numbers: list[str] = Field(min_length=1, max_length=200)


class ConfirmReturn(Body):
    number: str = Field(min_length=1, max_length=100)
    courier_id: str | int
    password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    operation_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_:-]+$")


class Receipt(Body):
    number: str = Field(min_length=1, max_length=100)


class ResolveOperation(Body):
    number: str = Field(min_length=1, max_length=100)
    kind: Literal["accept", "dropoff", "deliver", "return"]
    outcome: Literal["confirmed", "failed"]
    note: str = Field(min_length=5, max_length=500)
    verified_in_sps: bool


def install_lifecycle_routes(app, client, warehouse, session_for, passwords, courier_payload, active_operations):
    receipt_dir = warehouse.path.parent / (warehouse.path.stem + "-receipts")

    def require(capability):
        if not getattr(client, "capabilities", {}).get(capability):
            raise HTTPException(501, "Táto operácia zatiaľ nie je podporovaná pripojením dopravcu.")

    async def perform(number, kind, operation_id, actor, write, status, details=None):
        operation = warehouse.begin_carrier_operation(number, kind, operation_id)
        if operation.get("replayed"):
            if operation["outcome"] == "confirmed":
                return {"local_saved": True, "replayed": True}
            raise HTTPException(409, "Tento pokus už skončil. Opravte údaje a potvrďte nový pokus.")
        active_operations.add((number, kind))
        try:
            await write()
        except (OperationUncertainError, asyncio.CancelledError):
            uncertain = {"accept": "accept_uncertain", "dropoff": "dropoff_uncertain", "deliver": "delivery_uncertain", "return": "return_uncertain"}[kind]
            warehouse.finish_carrier_operation(operation_id, "uncertain", carrier_status=uncertain, actor=actor)
            raise
        except SPSError:
            warehouse.finish_carrier_operation(operation_id, "failed")
            raise
        finally:
            active_operations.discard((number, kind))
        try:
            warehouse.finish_carrier_operation(operation_id, "confirmed", carrier_status=status, actor=actor, details=details or {})
            return {"local_saved": True, "replayed": False}
        except (OSError, sqlite3.Error):
            return {"local_saved": False, "replayed": False}

    def receipt_path(number):
        return receipt_dir / (hashlib.sha256(number.encode()).hexdigest() + ".pdf")

    async def prepare_receipt(number, initial=False):
        path = receipt_path(number)
        if path.exists():
            return f"./api/pickup/receipts/{number}"
        data = await client.receipt(number, update_number=initial)
        receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=receipt_dir, prefix=".receipt-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return f"./api/pickup/receipts/{number}"

    @app.get("/api/warehouse/sync")
    async def sync():
        require("stored_shipments")
        rows = await client.stored_shipments()
        warehouse.sync_carrier_parcels(rows)
        return {**warehouse.snapshot(), "synced_at": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/dropoff/confirm")
    async def confirm_dropoff(request: Request, body: ConfirmDropoff):
        require("dropoff")
        session = session_for(request)
        number = parcel_number(body.number)
        if len(number) < 6:
            raise HTTPException(422, "Zadajte celé číslo podania, nie iba jeho koncovku.")
        local = warehouse.get_parcel(number)
        if local and (local["pending_operation"] or local["carrier_status"] not in {"accepted", "unconfirmed"}):
            raise HTTPException(409, "Balík už bol odovzdaný alebo čaká na overenie predchádzajúcej operácie v SPS.")
        if local and local["dropoff_confirmed"]:
            # Validate operation-id ownership even on a successful replay.
            warehouse.begin_carrier_operation(number, "dropoff", body.operation_id)
            return {"accepted": True, "number": number, "already_accepted": True,
                    "local_saved": True, "replayed": True, "parcel": local}
        if local and local["carrier_status"] == "accepted":
            raise HTTPException(409, "Balík už je uložený v SPS. Nové zákaznícke podanie sa nesmie potvrdiť druhýkrát; overte ho v SPS.")
        # Catch parcels already taken in through the official portal. Presence
        # is evidence of stored stock, not proof of our own customer intake.
        stored = await client.stored_shipments(number)
        current = next((p for p in stored if normalize_code(p["trackNumber"]) == number), None)
        if current is not None:
            warehouse.sync_carrier_parcels([current])
            raise HTTPException(409, "SPS už eviduje tento balík na výdajnom mieste. Skontrolujte ho v SPS; príjem neopakujte.")
        warehouse.register_parcel(number, actor=session["actor"], scanned=body.scanned)
        result = await perform(number, "dropoff", body.operation_id, session["actor"],
                               lambda: client.dropoff(number), "accepted", {"intake_kind": "dropoff"})
        return {**result, "accepted": True, "number": number, "parcel": warehouse.get_parcel(number)}

    @app.post("/api/pickup/lookup")
    async def lookup(request: Request, body: Lookup):
        require("pickup_lookup")
        session = session_for(request)
        now = time.time()
        lookups = session["pickups"]
        for key in list(lookups):
            if lookups[key]["expires"] < now:
                del lookups[key]
        if len(lookups) >= 20:
            del lookups[next(iter(lookups))]
        pin = body.pin.get_secret_value().strip()
        parcel = await client.lookup_pickup(pin)
        if parcel is None:
            raise HTTPException(404, "Pre tento verifikačný kód sa v SPS nenašiel balík. Skontrolujte kód zákazníka.")
        number = parcel_number(parcel["trackNumber"])
        stored = await client.stored_shipments(number)
        current = next((p for p in stored if normalize_code(p["trackNumber"]) == number), None)
        if current is None:
            raise HTTPException(409, "Balík nie je v SPS vedený medzi uloženými zásielkami tohto miesta. Overte ho v portáli SPS.")
        warehouse.sync_carrier_parcels([{**current, "cod": parcel["cod"]}])
        local = warehouse.get_parcel(number)
        if local["carrier_status"] != "accepted" or local["pending_operation"]:
            raise HTTPException(409, "Balík už bol vydaný alebo čaká na overenie predchádzajúcej operácie v SPS.")
        lookup_id = secrets.token_urlsafe(24)
        lookups[lookup_id] = {"pin": pin, "number": number, "cod": parcel["cod"], "expires": now + 600}
        return {"parcel": parcel, "local": local, "lookup_id": lookup_id,
                "scan_required": warehouse.get_settings()["pickup_scan_required"]}

    @app.post("/api/pickup/refresh")
    async def refresh_pickup(request: Request, body: RefreshPickup):
        require("pickup_refresh")
        session = session_for(request)
        item = session["pickups"].get(body.lookup_id)
        if item is None or item["expires"] < time.time():
            raise HTTPException(409, "Vyhľadanie vypršalo. Zadajte PIN zákazníka znova.")
        previous = dict(item)
        number = previous["number"]
        local = warehouse.get_parcel(number)
        if local is None or local["carrier_status"] != "accepted" or local["pending_operation"]:
            raise HTTPException(409, "Stav balíka vyžaduje overenie. Najskôr skontrolujte výsledok v SPS.")
        await client.refresh_cod(number)
        parcel = await client.lookup_pickup(previous["pin"])
        if parcel is None:
            raise HTTPException(409, "SPS už pre tento verifikačný kód nenašlo balík. Zadajte kód zákazníka znova.")
        if normalize_code(parcel["trackNumber"]) != number:
            raise HTTPException(409, "Verifikačný kód už nepatrí k vybranému balíku. Vyhľadajte ho znova.")
        stored = await client.stored_shipments(number)
        current = next((p for p in stored if normalize_code(p["trackNumber"]) == number), None)
        if current is None:
            raise HTTPException(409, "Balík už nie je v SPS vedený medzi uloženými zásielkami. Overte ho v portáli SPS.")
        warehouse.sync_carrier_parcels([{**current, "cod": parcel["cod"]}])
        local = warehouse.get_parcel(number)
        if local["carrier_status"] != "accepted" or local["pending_operation"]:
            raise HTTPException(409, "Medzičasom sa zmenil stav balíka. Najskôr overte výsledok v SPS.")
        # Replace the baseline only after a complete, successful authoritative
        # refresh. A timeout or malformed amount must never turn COD into zero.
        session["pickups"][body.lookup_id] = {**previous, "cod": parcel["cod"], "expires": time.time() + 600}
        return {"lookup_id": body.lookup_id, "parcel": parcel, "local": local,
                "amount_changed": previous["cod"] != parcel["cod"],
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "scan_required": warehouse.get_settings()["pickup_scan_required"]}

    @app.post("/api/pickup/confirm")
    async def confirm_pickup(request: Request, body: ConfirmPickup):
        require("delivery")
        session = session_for(request)
        item = session["pickups"].get(body.lookup_id)
        if item is None or item["expires"] < time.time():
            raise HTTPException(409, "Vyhľadanie vypršalo. Zadajte PIN zákazníka znova.")
        item = dict(item)
        number = item["number"]
        if warehouse.get_settings()["pickup_scan_required"] and body.scanned_number is None:
            raise HTTPException(409, "Pred výdajom naskenujte celý kód balíka podľa nastavenia prevádzky.")
        if body.scanned_number is not None and normalize_code(body.scanned_number) != number:
            raise HTTPException(409, "Naskenovaný balík nepatrí k tomuto PIN-u. Skontrolujte celé číslo zásielky.")
        local = warehouse.get_parcel(number)
        if local and local["carrier_status"] == "delivered":
            result = {"delivered": True, "number": number, "already_delivered": True}
            if receipt_path(number).exists():
                result["receipt_url"] = f"./api/pickup/receipts/{number}"
            elif item["cod"] > 0:
                result["receipt_pending"] = True
            return result
        if local is None or local["carrier_status"] != "accepted" or local["pending_operation"]:
            raise HTTPException(409, "Stav balíka vyžaduje overenie. Výdaj nemožno zopakovať bez kontroly SPS.")
        # Never trust a stale lookup: a PIN or amount may have changed in SPS.
        fresh = await client.lookup_pickup(item["pin"])
        if fresh is None:
            raise HTTPException(409, "SPS už pre tento verifikačný kód nenašlo balík. Zadajte kód zákazníka znova.")
        if normalize_code(fresh["trackNumber"]) != number:
            raise HTTPException(409, "Verifikačný kód už nepatrí k vybranému balíku. Vyhľadajte ho znova.")
        if fresh["cod"] != item["cod"]:
            raise HTTPException(409, "Suma dobierky sa zmenila. Obnovte platbu alebo vyhľadajte PIN znova a skontrolujte dobierku.")
        if body.cod_confirmed and body.paid_with_terminal:
            raise HTTPException(422, "Vyberte jeden spôsob úhrady dobierky.")
        if fresh["cod"] > 0 and not (body.cod_confirmed or body.paid_with_terminal):
            raise HTTPException(422, "Pred výdajom potvrďte skutočné prijatie dobierky.")
        if fresh["cod"] == 0 and (body.cod_confirmed or body.paid_with_terminal):
            raise HTTPException(422, "SPS už neeviduje dobierku na úhradu. Výdaj potvrďte bez hotovosti alebo terminálovej platby.")
        if body.paid_with_terminal and not fresh.get("paymentTerminalAllowed", False):
            raise HTTPException(422, "Tento SPS účet nemá povolený platobný terminál.")
        result = await perform(number, "deliver", body.operation_id, session["actor"],
                               lambda: client.deliver(item["pin"], cod_confirmed=body.cod_confirmed,
                                                      paid_with_terminal=body.paid_with_terminal),
                               "delivered", {"cod": fresh["cod"], "payment": "terminal" if body.paid_with_terminal else "cash" if body.cod_confirmed else "none"})
        result.update({"delivered": True, "number": number})
        if fresh["cod"] > 0:
            try:
                result["receipt_url"] = await prepare_receipt(number, initial=not result["replayed"])
            except (SPSError, OSError):
                result["receipt_pending"] = True
                result["message"] = "Balík bol vydaný. Doklad sa nepodarilo načítať; obnovte iba doklad, výdaj neopakujte."
        return result

    @app.post("/api/pickup/receipt")
    async def retry_receipt(body: Receipt):
        require("receipts")
        number = parcel_number(body.number)
        parcel = warehouse.get_parcel(number)
        if not parcel or parcel["carrier_status"] != "delivered":
            raise HTTPException(409, "Doklad sa dá načítať až po potvrdenom výdaji balíka.")
        return {"receipt_url": await prepare_receipt(number, initial=False)}

    @app.get("/api/pickup/receipts/{number}")
    async def download_receipt(number: str):
        number = parcel_number(number)
        path = receipt_path(number)
        if not path.exists():
            raise HTTPException(404, "Doklad ešte nie je načítaný. Použite obnoviť doklad.")
        return FileResponse(path, media_type="application/pdf", filename=f"SPS-{number}.pdf", content_disposition_type="inline")

    @app.get("/api/returns")
    async def returns():
        require("returns")
        # SPS calls this endpoint "expired", but the official courier-handover
        # screen uses it for customer drop-offs as well as expired/other returns.
        rows = await client.expired_shipments()
        warehouse.sync_carrier_parcels(rows)
        result = []
        for row in rows:
            local = warehouse.get_parcel(row["trackNumber"])
            kind = row.get("handoverKind", "unknown")
            if kind not in {"outgoing", "return"} and local["dropoff_confirmed"]:
                kind = "outgoing"
            result.append({**row, "handoverKind": kind, "local": local})
        return {"parcels": result, "synced_at": datetime.now(timezone.utc).isoformat(),
                "outgoing": [p for p in result if p.get("handoverKind") == "outgoing"],
                "returns": [p for p in result if p.get("handoverKind") == "return"],
                "unclassified": [p for p in result if p.get("handoverKind") not in {"outgoing", "return"}]}

    @app.post("/api/returns/couriers")
    async def return_couriers(request: Request, body: ReturnNumbers):
        require("returns")
        session = session_for(request)
        numbers = list(dict.fromkeys(parcel_number(n) for n in body.numbers))
        eligible = {p["trackNumber"] for p in await client.expired_shipments() if p["returnEligible"]}
        if any(n not in eligible for n in numbers):
            raise HTTPException(409, "Niektorý balík už SPS nepovoľuje odovzdať kuriérovi. Obnovte zoznam na výdaj kuriérovi.")
        choices = await client.couriers(numbers)
        session["return_numbers"] = set(numbers)
        session["return_couriers"] = {str(c["id"]): c for c in choices}
        return courier_payload(choices)

    @app.post("/api/returns/confirm")
    async def confirm_return(request: Request, body: ConfirmReturn):
        require("returns")
        session = session_for(request)
        number = parcel_number(body.number)
        courier = session["return_couriers"].get(str(body.courier_id))
        if number not in session["return_numbers"] or courier is None:
            raise HTTPException(409, "Vyberte kuriéra a naskenované balíky znova.")
        local = warehouse.get_parcel(number)
        if local and local["carrier_status"] == "returned":
            return {"returned": True, "number": number, "already_returned": True}
        if local and local["carrier_status"] != "accepted":
            raise HTTPException(409, "Balík nie je pripravený na odovzdanie alebo čaká na overenie výsledku v SPS.")
        current = next((p for p in await client.expired_shipments() if p["trackNumber"] == number), None)
        if current is None or not current["returnEligible"]:
            raise HTTPException(409, "SPS už tento balík nepovoľuje odovzdať kuriérovi. Obnovte zoznam.")
        warehouse.sync_carrier_parcels([current])
        password = body.password.get_secret_value() if body.password is not None else passwords.password(courier["id"])
        if password is None:
            raise CourierCredentialsError("Pre tohto kuriéra ešte nie je uložené heslo. Zadajte ho.")
        result = await perform(number, "return", body.operation_id, session["actor"],
                               lambda: client.return_parcel(number, courier["id"], password), "returned",
                               {"courier_id": str(courier["id"]), "courier_name": courier["name"],
                                "handover_kind": current.get("handoverKind", "unknown")})
        result.update({"returned": True, "number": number, "password_saved": True,
                       "handover_kind": current.get("handoverKind", "unknown")})
        if body.password is not None:
            try:
                passwords.save(courier, password)
            except OSError:
                result["password_saved"] = False
        return result

    @app.post("/api/operations/resolve")
    async def resolve(request: Request, body: ResolveOperation):
        number = parcel_number(body.number)
        if not body.verified_in_sps:
            raise HTTPException(422, "Najprv overte výsledok operácie v portáli SPS.")
        if any(active_number == number for active_number, _ in active_operations):
            raise HTTPException(409, "Požiadavka na SPS stále prebieha. Počkajte na jej výsledok.")
        result = warehouse.resolve_carrier_operation(number, body.kind, body.outcome, session_for(request)["actor"], body.note)
        return {"operation": result, "parcel": warehouse.get_parcel(number)}
