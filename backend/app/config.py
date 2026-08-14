from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_dir: Path = Path("/data")
    app_master_key: str | None = None
    app_timezone: str = "UTC"
    log_level: str = "INFO"
    session_secure: bool = False
    session_timeout_minutes: int = 480
    sync_interval_minutes: int = 5
    global_concurrency: int = 10
    per_node_concurrency: int = 3
    network_retries: int = 3
    verify_retries: int = 3
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'app.db'}"


settings = Settings()

