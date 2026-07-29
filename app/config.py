from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ALiver"
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8765
    database_url: str = "sqlite:///./data/aliver.db"
    secret_key: str = "change-me-local-only"
    admin_token: str | None = None
    log_level: str = "INFO"
    # VTube Studio's first AuthenticationTokenRequest waits for the user to click
    # Allow inside VTube Studio, so the control-plane command must outlive that dialog.
    bridge_command_timeout: float = 150.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ALIVER_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
