import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")


def now() -> datetime:
    return datetime.now(UTC)


def database_url(url: str) -> str:
    if url.startswith(("postgres://", "postgresql://")):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    return url


class Settings:
    def __init__(self):
        self.hosted = os.getenv("VERCEL") == "1" or os.getenv("SECURE_COOKIES") == "true"
        self.mode = os.getenv("APP_MODE", "demo")
        self.secret = os.getenv("JWT_SECRET", "local-demo-only-change-before-hosting")
        self.database_url = database_url(os.getenv(
            "DATABASE_URL", "postgresql://dispatch:dispatch@localhost:5432/dispatch",
        ))
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.namespace = os.getenv("REDIS_NAMESPACE", "ambulance-local")
        hosts = ["http://localhost:3000", "http://127.0.0.1:3000"] if not self.hosted else []
        hosts.extend(os.getenv("ALLOWED_ORIGINS", "").split(","))
        hosts.extend("https://" + os.environ[k] for k in (
            "VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL",
        ) if os.getenv(k))
        self.origins = sorted({host.strip().rstrip("/") for host in hosts if host.strip()})
        self.tomtom_key = os.getenv("TOMTOM_API_KEY", "")
        self.demo_speed = max(1, int(os.getenv("DEMO_SPEED", "45")))
        self.capacity_ttl = int(os.getenv("CAPACITY_TTL_SECONDS", "1800"))
        self.gps_ttl = int(os.getenv("GPS_TTL_SECONDS", "120"))
        self.reroute_threshold = int(os.getenv("REROUTE_IMPROVEMENT_SECONDS", "45"))
        self.reroute_cooldown = int(os.getenv("REROUTE_COOLDOWN_SECONDS", "30"))
        self.pool_size = int(os.getenv("DATABASE_POOL_SIZE", "3"))
        self.access_seconds = 900
        self.refresh_seconds = 8 * 3600
        if self.mode not in {"demo", "connected"}:
            raise RuntimeError("APP_MODE must be demo or connected")
        if self.hosted:
            for name in ("DATABASE_URL", "REDIS_URL", "REDIS_NAMESPACE", "JWT_SECRET"):
                if not os.getenv(name):
                    raise RuntimeError(f"{name} is required for hosted operation")
            if len(self.secret) < 32 or self.secret.startswith("local-"):
                raise RuntimeError("Set a strong JWT_SECRET")
            if not self.redis_url.startswith("rediss://"):
                raise RuntimeError("Hosted operation requires a native rediss:// URL")


settings = Settings()
