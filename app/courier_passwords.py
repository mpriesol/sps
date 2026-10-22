"""Small server-only password store; metadata is safe to return to the UI."""
import json
import os
import tempfile
from pathlib import Path


class CourierPasswords:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}

    def password(self, courier_id):
        return self.entries.get(str(courier_id), {}).get("password")

    def couriers(self):
        return [{k: row[k] for k in ("id", "code", "name")} for row in self.entries.values()]

    def save(self, courier, password):
        row = {k: courier[k] for k in ("id", "code", "name")}
        row["password"] = password
        if self.entries.get(str(courier["id"])) != row:
            self._write({**self.entries, str(courier["id"]): row})

    def remove(self, courier_id):
        self._write({k: v for k, v in self.entries.items() if k != str(courier_id)})

    def _write(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".couriers-")
        try:
            # mkstemp creates a file readable/writable only by the app's user (0600).
            with os.fdopen(fd, "w") as file:
                json.dump(entries, file, ensure_ascii=False)
            os.replace(name, self.path)
            self.entries = entries
        finally:
            Path(name).unlink(missing_ok=True)
