from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/jurismind"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    google_application_credentials: str = ""
    gcp_project_id: str = "jurismind-2910"
    gcp_location: str = "us-central1"
    gcs_bucket_name: str = "jurismind-storage"
    google_client_id: str = ""

    beto_model_path: str = str(Path(__file__).parent.parent.parent / "training" / "model_output" / "best_model")
    frontend_url: str = "http://localhost:5173"

    max_upload_size_mb: int = 50
    local_storage_path: str = str(Path(__file__).parent.parent / "storage")
    allowed_extensions: list[str] = [".pdf"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
