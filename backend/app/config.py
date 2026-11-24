"""Settings, read from the environment once at import. See .env.example for what each one does."""
import os
from dataclasses import dataclass, field


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    public_host: str = field(default_factory=lambda: os.environ.get("PUBLIC_HOST", "spending.munyau.space"))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "/data/spend.db"))
    # Which sheet the app writes to. "dev" uses SHEET_ID_DEV; "live" uses SHEET_ID and must be set on purpose.
    sheet_target: str = field(default_factory=lambda: os.environ.get("SHEET_TARGET", "dev"))
    sheet_id: str = field(default_factory=lambda: os.environ.get("SHEET_ID", ""))
    sheet_id_dev: str = field(default_factory=lambda: os.environ.get("SHEET_ID_DEV", ""))
    sheet_tab: str = field(default_factory=lambda: os.environ.get("SHEET_TAB", "Daily Spending"))
    google_sa_file: str = field(default_factory=lambda: os.environ.get("GOOGLE_SA_FILE", "/run/secrets/google-sa.json"))
    session_idle_days: int = field(default_factory=lambda: int(os.environ.get("SESSION_IDLE_DAYS", "30")))
    openai_model: str = field(default_factory=lambda: os.environ.get("OPENAI_MODEL", "gpt-6-luna"))
    # USD per million tokens, for the cost estimate on the settings page (gpt-6-luna list prices).
    openai_price_in: float = field(default_factory=lambda: float(os.environ.get("OPENAI_PRICE_IN", "0.10")))
    openai_price_out: float = field(default_factory=lambda: float(os.environ.get("OPENAI_PRICE_OUT", "0.50")))
    # Hosts allowed only for /api/health (the container healthcheck and deploy.sh call it on localhost).
    local_hosts: list[str] = field(default_factory=lambda: _list("LOCAL_HOSTS", "127.0.0.1,localhost"))
    # Extra origins accepted in local development, e.g. http://localhost:5184. Empty in production.
    dev_origins: list[str] = field(default_factory=lambda: _list("DEV_ORIGINS", ""))
    timezone: str = "Asia/Singapore"

    @property
    def origin(self) -> str:
        return f"https://{self.public_host}"

    @property
    def rp_id(self) -> str:
        return os.environ.get("RP_ID", self.public_host)

    @property
    def active_sheet_id(self) -> str:
        if self.sheet_target == "live":
            return self.sheet_id
        if self.sheet_target == "dev":
            return self.sheet_id_dev
        raise ValueError("SHEET_TARGET must be 'dev' or 'live'")


def settings() -> Settings:
    return Settings()
