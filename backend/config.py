"""Application configuration via environment variables."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """GameVault application settings loaded from environment variables."""

    # Required
    secret_key: str = "change-me-to-a-random-string"

    # Public URL for share links
    base_url: str = "http://localhost:8080"

    # Data paths
    data_dir: Path = Path("/data")
    library_dir: Path = Path("/data/library")
    db_path: Path = Path("/data/gamevault.db")

    # Authentication
    disable_auth: bool = False
    token_expiry_days: int = 30

    # Optional API keys
    steam_api_key: str = ""
    steamgriddb_api_key: str = ""
    igdb_client_id: str = ""
    igdb_client_secret: str = ""

    # CORS (comma-separated extra origins, in addition to localhost defaults)
    cors_origins: str = ""

    # Import settings
    import_rate_limit_ms: int = 1000

    # Image processing
    thumbnail_quality: int = 85
    max_upload_size_mb: int = 50

    model_config = {
        "env_prefix": "GAMEVAULT_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024


# Singleton instance
settings = Settings()
