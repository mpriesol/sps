"""Runtime configuration. Secrets are supplied only by the server operator."""
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    public_url: str
    app_password: str
    sps_username: str = ""
    sps_password: str = ""
    demo: bool = True
    session_hours: int = 12

    def __post_init__(self):
        parsed = urlsplit(self.public_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("PUBLIC_URL musí byť úplná URL aplikácie.")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError("PUBLIC_URL nesmie obsahovať heslo, query ani fragment.")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Použite HTTPS. HTTP je povolené iba pre lokálny vývoj.")
        if len(self.app_password) < 16 or self.app_password.startswith("CHANGE_"):
            raise ValueError("APP_PASSWORD musí mať aspoň 16 znakov a nesmie byť ukážková hodnota.")
        if not self.demo and (not self.sps_username or not self.sps_password):
            raise ValueError("Pre ostrý režim vyplňte SPS_USERNAME a SPS_PASSWORD.")
        if not 1 <= self.session_hours <= 168:
            raise ValueError("SESSION_HOURS musí byť 1 až 168.")
        path = parsed.path.rstrip("/")
        if path and any(c not in "/abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in path):
            raise ValueError("Neplatná cesta v PUBLIC_URL.")

    @property
    def origin(self):
        p = urlsplit(self.public_url)
        return f"{p.scheme}://{p.netloc}"

    @property
    def base_path(self):
        return urlsplit(self.public_url).path.rstrip("/")

    @property
    def secure_cookie(self):
        return self.public_url.startswith("https://")

    @classmethod
    def from_env(cls):
        return cls(
            public_url=os.getenv("PUBLIC_URL", "http://localhost:8080"),
            app_password=os.getenv("APP_PASSWORD", ""),
            sps_username=os.getenv("SPS_USERNAME", ""),
            sps_password=os.getenv("SPS_PASSWORD", ""),
            demo=os.getenv("SPS_DEMO", "true").lower() == "true",
            session_hours=int(os.getenv("SESSION_HOURS", "12")),
        )
