"""Account-local parcel locations and an append-only movement journal.

Carrier state and physical location are deliberately independent. SQLite transactions
protect both a whole-shelf move and competing scans from different devices.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


DEFAULT_SETTINGS = {
    "courier_warning": True, "photos": False, "return_staging": False,
    "receipt_place": False, "last_courier_id": None,
}
TERMINAL = {"delivered", "returned"}
CARRIER_STATES = {"unconfirmed", "accepted", "accept_uncertain", "delivery_uncertain", "return_uncertain", *TERMINAL}
PHOTO_MAX_EDGE = 640
PHOTO_MAX_BYTES = 40 * 1024
PHOTO_QUALITIES = (70, 58, 46)


def compact_photo(original):
    """Return a small, upright JPEG; neither metadata nor input size are trusted."""
    from PIL import Image, ImageOps
    clean = ImageOps.exif_transpose(original).convert("RGB")
    clean.thumbnail((PHOTO_MAX_EDGE, PHOTO_MAX_EDGE), Image.Resampling.LANCZOS)
    clean.info.clear()
    while True:
        for quality in PHOTO_QUALITIES:
            encoded = io.BytesIO()
            clean.save(encoded, "JPEG", quality=quality, optimize=True, subsampling=2)
            if encoded.tell() <= PHOTO_MAX_BYTES:
                return encoded.getvalue()
        # Prefer keeping recognizable detail at the normal size. Extremely
        # noisy photos get a smaller image instead of an unbounded payload.
        longest = max(clean.size)
        if longest <= 128:
            raise WarehouseError(422, "Fotografiu sa nepodarilo zmenšiť. Skúste ju odfotiť znova.")
        edge = max(128, int(longest * 0.8))
        clean.thumbnail((edge, edge), Image.Resampling.LANCZOS)


class WarehouseError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parcel_number(value):
    if not isinstance(value, str):
        raise WarehouseError(422, "Zadajte celé číslo balíka.")
    value = value.strip().upper()
    if value.startswith("LOC-"):
        raise WarehouseError(422, "Toto je kód miesta. Vyberte platnú policu, nie registráciu balíka.")
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,99}", value):
        raise WarehouseError(422, "Neplatné číslo balíka.")
    return value


def name_value(value, label="Názov"):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or any(ord(c) < 32 for c in value):
        raise WarehouseError(422, f"{label} musí mať 1 až 80 znakov.")
    return value.strip()


class Warehouse:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.photo_dir = self.path.parent / (self.path.stem + "-photos")
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS locations (
                    id TEXT PRIMARY KEY, barcode TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    rack TEXT NOT NULL, shelf INTEGER NOT NULL, position INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'shelf', version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS parcels (
                    number TEXT PRIMARY KEY, sender_name TEXT NOT NULL DEFAULT '',
                    carrier_status TEXT NOT NULL DEFAULT 'unconfirmed',
                    location_id TEXT REFERENCES locations(id), version INTEGER NOT NULL DEFAULT 1,
                    photo_file TEXT, photo_updated_at TEXT, expires_at TEXT,
                    expiry_source TEXT, expiry_synced_at TEXT, staged INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    carrier_data TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS parcels_location ON parcels(location_id);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL REFERENCES parcels(number),
                    type TEXT NOT NULL, from_location_id TEXT, to_location_id TEXT,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}',
                    version_after INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_parcel ON events(number, id DESC);
                CREATE TABLE IF NOT EXISTS local_operations (
                    operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    result TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS carrier_operations (
                    operation_id TEXT PRIMARY KEY, number TEXT NOT NULL REFERENCES parcels(number),
                    kind TEXT NOT NULL, outcome TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS carrier_operation_parcel ON carrier_operations(number, kind, created_at DESC);
            """)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def _parcel(self, row, db):
        if row is None:
            return None
        p = dict(row)
        photo = p.pop("photo_file", None)
        p["photo_url"] = f"./api/parcels/{quote(p['number'], safe='')}/photo?v={p.get('photo_updated_at') or ''}" if photo else None
        p["suffix"] = p["number"][-2:]
        p["staged"] = bool(p["staged"])
        p["carrier_data"] = json.loads(p["carrier_data"])
        pending = db.execute("SELECT operation_id,kind,outcome,created_at,updated_at FROM carrier_operations WHERE number=? AND outcome IN ('pending','uncertain') ORDER BY created_at DESC LIMIT 1", (p["number"],)).fetchone()
        p["pending_operation"] = dict(pending) if pending else None
        return p

    def _event(self, row):
        if row is None:
            return None
        value = dict(row)
        value["details"] = json.loads(value["details"])
        return value

    def _find(self, db, number, required=True):
        row = db.execute("SELECT * FROM parcels WHERE number=?", (number,)).fetchone()
        if row is None and required:
            raise WarehouseError(404, "Balík ešte nie je v miestnej evidencii.")
        return row

    def _location(self, db, location_id, required=True):
        if location_id is None:
            return None
        row = db.execute("SELECT l.*, (SELECT COUNT(*) FROM parcels p WHERE p.location_id=l.id) AS count FROM locations l WHERE id=?", (location_id,)).fetchone()
        if row is None and required:
            raise WarehouseError(404, "Miesto už neexistuje. Obnovte zoznam políc.")
        return dict(row) if row else None

    def _add_event(self, db, number, kind, actor, before, after, version, details=None):
        details = dict(details or {})
        for key, location_id in (("from_location_name", before), ("to_location_name", after)):
            location = self._location(db, location_id, required=False)
            details[key] = location["name"] if location else None
        cursor = db.execute(
            "INSERT INTO events(number,type,from_location_id,to_location_id,actor,created_at,details,version_after) VALUES(?,?,?,?,?,?,?,?)",
            (number, kind, before, after, str(actor or "Obsluha")[:80], now(), json.dumps(details, ensure_ascii=False), version),
        )
        return self._event(db.execute("SELECT * FROM events WHERE id=?", (cursor.lastrowid,)).fetchone())

    def _operation(self, db, operation_id, kind, payload):
        if not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 128:
            raise WarehouseError(422, "Chýba identifikátor operácie. Zopakujte skenovanie.")
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        old = db.execute("SELECT * FROM local_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if old:
            if old["kind"] != kind or old["fingerprint"] != fingerprint:
                raise WarehouseError(409, "Identifikátor už patrí inej operácii. Obnovte údaje.")
            return json.loads(old["result"]), fingerprint
        return None, fingerprint

    def _save_operation(self, db, operation_id, kind, fingerprint, result):
        db.execute("INSERT INTO local_operations VALUES(?,?,?,?,?)", (operation_id, kind, fingerprint, json.dumps(result, ensure_ascii=False), now()))

    def get_settings(self):
        with self._db() as db:
            return {**DEFAULT_SETTINGS, **{row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}}

    def update_settings(self, values):
        if any(key not in DEFAULT_SETTINGS for key in values):
            raise WarehouseError(422, "Neznáme nastavenie.")
        for key, value in values.items():
            if key == "last_courier_id":
                if value is not None and (not isinstance(value, (str, int)) or len(str(value)) > 100):
                    raise WarehouseError(422, "Neplatný kuriér.")
            elif not isinstance(value, bool):
                raise WarehouseError(422, "Nastavenie musí byť zapnuté alebo vypnuté.")
        with self._db(write=True) as db:
            for key, value in values.items():
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        return self.get_settings()

    def snapshot(self):
        with self._db() as db:
            locations = [dict(row) for row in db.execute("SELECT l.*, COUNT(p.number) AS count FROM locations l LEFT JOIN parcels p ON p.location_id=l.id GROUP BY l.id ORDER BY l.rack COLLATE NOCASE,l.shelf,l.position,l.name")]
            parcels = [self._parcel(row, db) for row in db.execute("SELECT * FROM parcels ORDER BY updated_at DESC,number")]
        return {"locations": locations, "parcels": parcels, "settings": self.get_settings()}

    def create_locations(self, rack_name, shelf_count=1, places_per_shelf=0, kind="shelf", name=None):
        rack_name = name_value(rack_name, "Názov regálu")
        if type(shelf_count) is not int or type(places_per_shelf) is not int or not 1 <= shelf_count <= 100 or not 0 <= places_per_shelf <= 100 or shelf_count * max(1, places_per_shelf) > 500:
            raise WarehouseError(422, "Vytvorte najviac 500 miest naraz; regál môže mať 1 až 100 políc.")
        if kind not in {"shelf", "oversized", "counter", "returns", "custom"}:
            raise WarehouseError(422, "Neplatný typ miesta.")
        if name is not None:
            name = name_value(name)
            if shelf_count != 1 or places_per_shelf > 1:
                raise WarehouseError(422, "Vlastný názov použite pre jedno miesto.")
        result = []
        with self._db(write=True) as db:
            if db.execute("SELECT 1 FROM locations WHERE lower(rack)=lower(?)", (rack_name,)).fetchone():
                raise WarehouseError(409, "Regál s týmto názvom už existuje. Použite iný názov.")
            for shelf in range(1, shelf_count + 1):
                for position in range(1, max(1, places_per_shelf) + 1):
                    location_id = uuid.uuid4().hex
                    barcode = "LOC-" + uuid.uuid4().hex[:16].upper()
                    label = name or (f"{rack_name}{shelf}" + (f"-{position}" if places_per_shelf else ""))
                    if db.execute("SELECT 1 FROM locations WHERE lower(name)=lower(?)", (label,)).fetchone():
                        raise WarehouseError(409, "Niektorý názov miesta už existuje. Použite iný názov regálu alebo miesta.")
                    db.execute("INSERT INTO locations(id,barcode,name,rack,shelf,position,kind) VALUES(?,?,?,?,?,?,?)", (location_id, barcode, label, rack_name, shelf, position if places_per_shelf else 0, kind))
                    result.append(self._location(db, location_id))
        return result

    def update_location(self, location_id, name, rack=None, expected_version=None):
        name = name_value(name)
        if rack is not None:
            rack = name_value(rack, "Názov regálu")
        with self._db(write=True) as db:
            current = self._location(db, location_id)
            if expected_version is not None and current["version"] != expected_version:
                raise WarehouseError(409, "Miesto medzičasom zmenilo iné zariadenie. Obnovte údaje.")
            if db.execute("SELECT 1 FROM locations WHERE lower(name)=lower(?) AND id<>?", (name, location_id)).fetchone():
                raise WarehouseError(409, "Miesto s týmto názvom už existuje.")
            db.execute("UPDATE locations SET name=?,rack=?,version=version+1 WHERE id=?", (name, rack if rack is not None else current["rack"], location_id))
            return self._location(db, location_id)

    def rename_rack(self, rack_name, new_name):
        rack_name = name_value(rack_name, "Názov regálu")
        new_name = name_value(new_name, "Nový názov regálu")
        with self._db(write=True) as db:
            if not db.execute("SELECT 1 FROM locations WHERE rack=?", (rack_name,)).fetchone():
                raise WarehouseError(404, "Regál už neexistuje. Obnovte zoznam.")
            if db.execute("SELECT 1 FROM locations WHERE lower(rack)=lower(?) AND rack<>?", (new_name, rack_name)).fetchone():
                raise WarehouseError(409, "Regál s novým názvom už existuje.")
            # Custom location names and every printed barcode stay valid.
            db.execute("UPDATE locations SET rack=?,version=version+1 WHERE rack=?", (new_name, rack_name))
            return [dict(row) for row in db.execute("SELECT l.*, (SELECT COUNT(*) FROM parcels p WHERE p.location_id=l.id) AS count FROM locations l WHERE rack=? ORDER BY shelf,position,name", (new_name,))]

    def delete_location(self, location_id):
        with self._db(write=True) as db:
            location = self._location(db, location_id)
            if location["count"]:
                raise WarehouseError(409, "Na tomto mieste sú balíky. Najskôr ich presuňte.")
            db.execute("DELETE FROM locations WHERE id=?", (location_id,))
        return {"ok": True}

    def get_parcel(self, number):
        number = parcel_number(number)
        with self._db() as db:
            return self._parcel(self._find(db, number, required=False), db)

    def register_parcel(self, number, sender_name="", actor="Obsluha"):
        number = parcel_number(number)
        if not isinstance(sender_name, str) or len(sender_name) > 500:
            raise WarehouseError(422, "Neplatný odosielateľ.")
        with self._db(write=True) as db:
            row = self._find(db, number, required=False)
            if row:
                return self._parcel(row, db)
            timestamp = now()
            db.execute("INSERT INTO parcels(number,sender_name,created_at,updated_at) VALUES(?,?,?,?)", (number, sender_name.strip(), timestamp, timestamp))
            self._add_event(db, number, "registered", actor, None, None, 1)
            return self._parcel(self._find(db, number), db)

    def _assert_movable(self, parcel, version, db=None):
        if parcel["carrier_status"] in TERMINAL:
            raise WarehouseError(409, "Tento balík už bol vydaný. Sken ho nemôže vrátiť do skladu.")
        if type(version) is not int or parcel["version"] != version:
            raise WarehouseError(409, "Balík medzičasom zmenilo iné zariadenie. Obnovte jeho umiestnenie.")
        if db is not None and db.execute("SELECT 1 FROM carrier_operations WHERE number=? AND kind IN ('deliver','return') AND outcome='pending'", (parcel["number"],)).fetchone():
            raise WarehouseError(409, "Práve prebieha odovzdanie balíka. Počkajte na výsledok v SPS.")

    def _move_one(self, db, parcel, location_id, actor, kind="move", details=None):
        if parcel["location_id"] == location_id:
            return {"parcel": self._parcel(parcel, db), "event": None, "unchanged": True}
        version = parcel["version"] + 1
        db.execute("UPDATE parcels SET location_id=?,version=?,updated_at=? WHERE number=?", (location_id, version, now(), parcel["number"]))
        event = self._add_event(db, parcel["number"], kind, actor, parcel["location_id"], location_id, version, details)
        return {"parcel": self._parcel(self._find(db, parcel["number"]), db), "event": event}

    def move_parcel(self, number, location_id, expected_version, operation_id, actor="Obsluha"):
        number = parcel_number(number)
        payload = [number, location_id, expected_version]
        with self._db(write=True) as db:
            cached, fingerprint = self._operation(db, operation_id, "move", payload)
            if cached is not None:
                return cached
            self._location(db, location_id)
            parcel = self._find(db, number)
            self._assert_movable(parcel, expected_version, db)
            result = self._move_one(db, parcel, location_id, actor)
            self._save_operation(db, operation_id, "move", fingerprint, result)
            return result

    def move_location(self, source_id, target_id, parcels, operation_id, actor="Obsluha"):
        if source_id == target_id or not source_id or not target_id:
            raise WarehouseError(422, "Vyberte dve rôzne miesta.")
        if not parcels or len(parcels) > 2000:
            raise WarehouseError(422, "Vyberte 1 až 2 000 balíkov.")
        items = [{"number": parcel_number(p["number"]), "version": p["version"]} for p in parcels]
        if len({p["number"] for p in items}) != len(items):
            raise WarehouseError(422, "V zozname je ten istý balík viackrát.")
        with self._db(write=True) as db:
            cached, fingerprint = self._operation(db, operation_id, "bulk_move", [source_id, target_id, items])
            if cached is not None:
                return cached
            self._location(db, source_id)
            self._location(db, target_id)
            rows = []
            for item in items:
                row = self._find(db, item["number"])
                self._assert_movable(row, item["version"], db)
                if row["location_id"] != source_id:
                    raise WarehouseError(409, "Niektorý balík už je na inom mieste. Obnovte obsah police.")
                rows.append(row)
            results = [self._move_one(db, row, target_id, actor, "bulk_move", {"operation_id": operation_id}) for row in rows]
            result = {"parcels": [r["parcel"] for r in results], "events": [r["event"] for r in results]}
            self._save_operation(db, operation_id, "bulk_move", fingerprint, result)
            return result

    def history(self, number):
        number = parcel_number(number)
        with self._db() as db:
            self._find(db, number)
            return [self._event(row) for row in db.execute("SELECT * FROM events WHERE number=? ORDER BY id DESC LIMIT 500", (number,))]

    def undo_move(self, event_id, expected_version, operation_id, actor="Obsluha"):
        with self._db(write=True) as db:
            cached, fingerprint = self._operation(db, operation_id, "undo", [event_id, expected_version])
            if cached is not None:
                return cached
            event = db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not event or event["type"] not in {"move", "bulk_move"}:
                raise WarehouseError(422, "Vrátiť možno iba posledný presun balíka.")
            parcel = self._find(db, event["number"])
            self._assert_movable(parcel, expected_version, db)
            if event["version_after"] != expected_version or parcel["location_id"] != event["to_location_id"]:
                raise WarehouseError(409, "Po tomto presune sa balík už zmenil. Obnovte údaje.")
            self._location(db, event["from_location_id"])
            result = self._move_one(db, parcel, event["from_location_id"], actor, "undo", {"event_id": event_id})
            self._save_operation(db, operation_id, "undo", fingerprint, result)
            return result

    def check_inventory(self, location_id, numbers):
        scanned = list(dict.fromkeys(parcel_number(n) for n in numbers))
        with self._db() as db:
            location = self._location(db, location_id)
            expected = [self._parcel(row, db) for row in db.execute("SELECT * FROM parcels WHERE location_id=? ORDER BY number", (location_id,))]
            expected_numbers = {p["number"] for p in expected}
            actual = set(scanned)
            unexpected = []
            for number in scanned:
                if number not in expected_numbers:
                    unexpected.append(self._parcel(self._find(db, number, required=False), db) or {"number": number, "suffix": number[-2:], "unknown": True, "location_id": None})
            return {"location": location, "expected": expected,
                    "matched": [p for p in expected if p["number"] in actual],
                    "missing": [p for p in expected if p["number"] not in actual], "unexpected": unexpected}

    def mark_carrier(self, number, status, actor="SPS", details=None):
        number = parcel_number(number)
        if status not in CARRIER_STATES:
            raise WarehouseError(422, "Neplatný stav dopravcu.")
        with self._db(write=True) as db:
            return self._mark_carrier(db, number, status, actor, details)

    def _mark_carrier(self, db, number, status, actor, details=None):
        if status not in CARRIER_STATES:
            raise WarehouseError(422, "Neplatný stav dopravcu.")
        parcel = self._find(db, number)
        if parcel["carrier_status"] in TERMINAL and parcel["carrier_status"] != status:
            raise WarehouseError(409, "Ukončenú zásielku nemožno znovu prijať.")
        if parcel["carrier_status"] == status:
            return self._parcel(parcel, db)
        location_id = None if status in TERMINAL else parcel["location_id"]
        version = parcel["version"] + 1
        db.execute("UPDATE parcels SET carrier_status=?,location_id=?,staged=?,version=?,updated_at=? WHERE number=?", (status, location_id, 0 if status in TERMINAL else parcel["staged"], version, now(), number))
        self._add_event(db, number, "carrier_" + status, actor, parcel["location_id"], location_id, version, details)
        return self._parcel(self._find(db, number), db)

    def sync_carrier_parcels(self, rows, source="SPS", actor="SPS"):
        """Merge an authoritative read; absence never proves delivery or expiry."""
        result = []
        with self._db(write=True) as db:
            for item in rows:
                number = parcel_number(item.get("trackNumber") or item.get("number"))
                row = self._find(db, number, required=False)
                timestamp = now()
                sender = str(item.get("senderName") or item.get("sender_name") or "")[:500]
                # Missing/empty expiry clears a stale date only because this is an
                # explicit authoritative parcel row, never an inferred duration.
                expiry = item.get("expiredDate", item.get("expires_at"))
                if expiry is not None:
                    expiry = str(expiry)[:100]
                data = {key: item[key] for key in ("recipientName", "statusCode", "statusLabel", "statusDate", "cod", "psRemark", "returnEligible", "entryCourier", "handoverKind") if key in item}
                if row is None:
                    db.execute("INSERT INTO parcels(number,sender_name,carrier_status,expires_at,expiry_source,expiry_synced_at,created_at,updated_at,carrier_data) VALUES(?,?,?,?,?,?,?,?,?)", (number, sender, "accepted", expiry, source, timestamp, timestamp, timestamp, json.dumps(data, ensure_ascii=False)))
                    self._add_event(db, number, "carrier_sync", actor, None, None, 1, {"source": source})
                elif row["carrier_status"] not in TERMINAL:
                    next_status = "accepted" if row["carrier_status"] in {"unconfirmed", "accept_uncertain"} else row["carrier_status"]
                    changes = sender != row["sender_name"] or expiry != row["expires_at"] or next_status != row["carrier_status"] or json.dumps(data, ensure_ascii=False, sort_keys=True) != json.dumps(json.loads(row["carrier_data"]), ensure_ascii=False, sort_keys=True)
                    db.execute("UPDATE parcels SET sender_name=?,carrier_status=?,expires_at=?,expiry_source=?,expiry_synced_at=?,carrier_data=?,version=version+?,updated_at=CASE WHEN ? THEN ? ELSE updated_at END WHERE number=?", (sender, next_status, expiry, source, timestamp, json.dumps(data, ensure_ascii=False), int(changes), int(changes), timestamp, number))
                    if changes:
                        self._add_event(db, number, "carrier_sync", actor, row["location_id"], row["location_id"], row["version"] + 1, {"source": source})
                    if next_status == "accepted":
                        db.execute("UPDATE carrier_operations SET outcome='confirmed',updated_at=? WHERE number=? AND kind='accept' AND outcome IN ('pending','uncertain')", (timestamp, number))
                result.append(self._parcel(self._find(db, number), db))
        return result

    def set_staged(self, parcels, staged=True, actor="Obsluha"):
        if not self.get_settings()["return_staging"]:
            raise WarehouseError(409, "Príprava vratiek je vypnutá v nastaveniach.")
        if not parcels or len(parcels) > 2000:
            raise WarehouseError(422, "Vyberte 1 až 2 000 balíkov.")
        with self._db(write=True) as db:
            rows = []
            seen = set()
            for item in parcels:
                number = parcel_number(item["number"])
                if number in seen:
                    raise WarehouseError(422, "V zozname je ten istý balík viackrát.")
                seen.add(number)
                row = self._find(db, number)
                self._assert_movable(row, item["version"], db)
                rows.append(row)
            result = []
            for row in rows:
                if bool(row["staged"]) != bool(staged):
                    db.execute("UPDATE parcels SET staged=?,version=version+1,updated_at=? WHERE number=?", (int(staged), now(), row["number"]))
                    self._add_event(db, row["number"], "staged" if staged else "unstaged", actor, row["location_id"], row["location_id"], row["version"] + 1)
                result.append(self._parcel(self._find(db, row["number"]), db))
            return result

    def begin_carrier_operation(self, number, kind, operation_id):
        number = parcel_number(number)
        if kind not in {"accept", "deliver", "return"} or not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 128:
            raise WarehouseError(422, "Neplatná operácia dopravcu.")
        with self._db(write=True) as db:
            parcel = self._find(db, number)
            same = db.execute("SELECT * FROM carrier_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if same:
                if same["number"] != number or same["kind"] != kind:
                    raise WarehouseError(409, "Identifikátor už patrí inej operácii.")
                if same["outcome"] in {"pending", "uncertain"}:
                    raise WarehouseError(409, "Výsledok operácie v SPS ešte nie je potvrdený. Najskôr ho overte v SPS.")
                return {**dict(same), "replayed": True}
            previous = db.execute("SELECT * FROM carrier_operations WHERE number=? AND outcome IN ('pending','uncertain') ORDER BY created_at DESC LIMIT 1", (number,)).fetchone()
            if previous:
                raise WarehouseError(409, "Pri balíku čakáme na overenie predchádzajúcej operácie v SPS.")
            completed = db.execute("SELECT * FROM carrier_operations WHERE number=? AND kind=? AND outcome='confirmed' ORDER BY created_at DESC LIMIT 1", (number, kind)).fetchone()
            if completed:
                return {**dict(completed), "replayed": True}
            if parcel["carrier_status"] in TERMINAL:
                raise WarehouseError(409, "Balík už bol odovzdaný; ďalšia operácia je zablokovaná.")
            timestamp = now()
            db.execute("INSERT INTO carrier_operations VALUES(?,?,?,'pending','',?,?)", (operation_id, number, kind, timestamp, timestamp))
            return {"operation_id": operation_id, "number": number, "kind": kind, "outcome": "pending", "replayed": False}

    def finish_carrier_operation(self, operation_id, outcome, error="", carrier_status=None, actor="Obsluha", details=None):
        if outcome not in {"confirmed", "failed", "uncertain"}:
            raise WarehouseError(422, "Neplatný výsledok operácie.")
        with self._db(write=True) as db:
            row = db.execute("SELECT * FROM carrier_operations WHERE operation_id=?", (operation_id,)).fetchone()
            if not row:
                raise WarehouseError(404, "Operácia neexistuje.")
            if row["outcome"] == "confirmed" and outcome != "confirmed":
                raise WarehouseError(409, "Potvrdenú operáciu nemožno prepísať.")
            if carrier_status is not None:
                self._mark_carrier(db, row["number"], carrier_status, actor, details)
            db.execute("UPDATE carrier_operations SET outcome=?,error=?,updated_at=? WHERE operation_id=?", (outcome, str(error)[:500], now(), operation_id))
            parcel = self._parcel(self._find(db, row["number"]), db)
            return {**dict(db.execute("SELECT * FROM carrier_operations WHERE operation_id=?", (operation_id,)).fetchone()), "parcel": parcel}

    def resolve_carrier_operation(self, number, kind, outcome, actor="Obsluha", note=""):
        """Only call after the operator explicitly checked the carrier portal.

        A missing parcel in an SPS list is not enough evidence for this operation.
        The API layer must require an explicit confirmation and audit note.
        """
        number = parcel_number(number)
        if kind not in {"accept", "deliver", "return"} or outcome not in {"confirmed", "failed"}:
            raise WarehouseError(422, "Vyberte overený výsledok operácie v SPS.")
        if not isinstance(note, str) or not 5 <= len(note.strip()) <= 500:
            raise WarehouseError(422, "Doplňte poznámku k overeniu v SPS.")
        with self._db(write=True) as db:
            operation = db.execute("SELECT * FROM carrier_operations WHERE number=? AND kind=? AND outcome IN ('pending','uncertain') ORDER BY created_at DESC LIMIT 1", (number, kind)).fetchone()
            if not operation:
                raise WarehouseError(409, "Balík už nemá túto neoverenú operáciu. Obnovte údaje.")
            status = {"accept": "accepted", "deliver": "delivered", "return": "returned"}[kind] if outcome == "confirmed" else ("unconfirmed" if kind == "accept" else "accepted")
            details = {"operation_id": operation["operation_id"], "verified_manually": True, "note": note.strip(), "outcome": outcome}
            parcel = self._mark_carrier(db, number, status, actor, details)
            db.execute("UPDATE carrier_operations SET outcome=?,error='',updated_at=? WHERE operation_id=?", (outcome, now(), operation["operation_id"]))
            self._add_event(db, number, "carrier_verified", actor, parcel["location_id"], parcel["location_id"], parcel["version"], details)
            parcel = self._parcel(self._find(db, number), db)
            return {"parcel": parcel, "operation_id": operation["operation_id"], "outcome": outcome}

    def store_photo(self, number, image, actor="Obsluha"):
        number = parcel_number(number)
        if not self.get_settings()["photos"]:
            raise WarehouseError(409, "Fotografie sú vypnuté v nastaveniach.")
        if not isinstance(image, str) or len(image) > 2_100_000 or not image.startswith("data:image/jpeg;base64,"):
            raise WarehouseError(422, "Použite fotografiu JPEG do 1,5 MB.")
        try:
            data = base64.b64decode(image.split(",", 1)[1], validate=True)
        except (ValueError, TypeError) as exc:
            raise WarehouseError(422, "Fotografia sa nepodarila načítať.") from exc
        if len(data) > 1_500_000:
            raise WarehouseError(413, "Fotografia je príliš veľká.")
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data)) as original:
                if original.format != "JPEG" or original.width * original.height > 20_000_000:
                    raise WarehouseError(422, "Použite bežnú fotografiu JPEG.")
                original.load()
                encoded = compact_photo(original)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise WarehouseError(422, "Fotografia je poškodená alebo má neplatný formát.") from exc
        self.photo_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = uuid.uuid4().hex + ".jpg"
        target = self.photo_dir / filename
        old = None
        try:
            with target.open("xb") as file:
                file.write(encoded)
            os.chmod(target, 0o600)
            with self._db(write=True) as db:
                row = self._find(db, number)
                old = row["photo_file"]
                db.execute("UPDATE parcels SET photo_file=?,photo_updated_at=?,version=version+1,updated_at=? WHERE number=?", (filename, now(), now(), number))
                self._add_event(db, number, "photo", actor, row["location_id"], row["location_id"], row["version"] + 1)
                result = self._parcel(self._find(db, number), db)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if old:
            try:
                (self.photo_dir / old).unlink(missing_ok=True)
            except OSError:
                pass
        return result

    def photo_path(self, number):
        number = parcel_number(number)
        with self._db() as db:
            row = self._find(db, number)
            if not row["photo_file"]:
                raise WarehouseError(404, "Balík nemá fotografiu.")
            path = self.photo_dir / row["photo_file"]
            if not path.is_file():
                raise WarehouseError(404, "Fotografia už nie je dostupná.")
            return path
