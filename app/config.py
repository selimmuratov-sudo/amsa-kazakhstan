from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AMSA_", env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite:///./data/platform.sqlite3"
    session_days: int = 7
    cookie_secure: bool = False
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    auth_rate_limit_pepper: str = ""
    backup_encryption_key: str = ""
    private_files_encryption_key: str = ""
    backup_directory: str = "data/backups"
    google_reports_form_url: str = ""
    google_reports_spreadsheet_id: str = ""
    google_service_account_file: str = ""
    google_reports_sheet_name: str = "Committee Reports"

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if not self.is_production:
            return self
        errors = []
        if not self.cookie_secure:
            errors.append("AMSA_COOKIE_SECURE must be true")
        if len(self.auth_rate_limit_pepper.strip()) < 32:
            errors.append("AMSA_AUTH_RATE_LIMIT_PEPPER must contain at least 32 characters")
        if not self.database_url.lower().startswith(("postgresql://", "postgresql+")):
            errors.append("AMSA_DATABASE_URL must use PostgreSQL in production")
        hosts = self.host_list
        if not hosts or any(host == "*" or host.lower() in {"localhost", "testserver", "127.0.0.1", "::1"} for host in hosts):
            errors.append("AMSA_ALLOWED_HOSTS must list production hostnames and cannot contain wildcards or local test hosts")
        try:
            Fernet(self.backup_encryption_key.strip().encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            errors.append("AMSA_BACKUP_ENCRYPTION_KEY must be a valid Fernet key")
        try:
            Fernet(self.private_files_encryption_key.strip().encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            errors.append("AMSA_PRIVATE_FILES_ENCRYPTION_KEY must be a valid Fernet key")
        if errors:
            raise ValueError("Invalid production configuration: " + "; ".join(errors))
        return self

    @property
    def host_list(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def google_reports_embed_url(self) -> str | None:
        """Accept only a normal Google Forms view URL; never embed arbitrary hosts."""
        raw = self.google_reports_form_url.strip()
        if not raw:
            return None
        parts = urlsplit(raw)
        if parts.scheme != "https" or parts.hostname != "docs.google.com" or not parts.path.startswith("/forms/d/e/") or not parts.path.endswith("/viewform"):
            return None
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["embedded"] = "true"
        return urlunsplit(("https", "docs.google.com", parts.path, urlencode(query), ""))

    @property
    def google_sheets_sync_configured(self) -> bool:
        return bool(self.google_reports_spreadsheet_id.strip() and self.google_service_account_file.strip()
                    and Path(self.google_service_account_file).expanduser().is_file())


@lru_cache
def get_settings() -> Settings:
    return Settings()
