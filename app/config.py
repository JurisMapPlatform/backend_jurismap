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

    # Gemini 2.5 Flash funciona en us-central1 con el SDK actual (vertexai clásico).
    # La migración a 3.x exige la región "global" + el SDK nuevo google-genai (pendiente).
    gemini_model: str = "gemini-2.5-flash"
    gemini_location: str = "us-central1"

    # Correo transaccional (Brevo). Si brevo_api_key está vacío, el envío se omite sin romper nada.
    brevo_api_key: str = ""
    brevo_sender_email: str = ""
    brevo_sender_name: str = "JurisMap"
    # Cuando es True, el login exige el correo verificado (HU-02). Requiere Brevo configurado.
    require_email_verification: bool = False

    beto_model_path: str = str(Path(__file__).parent.parent.parent / "training" / "model_output" / "best_model")
    beto_model_gcs_prefix: str = "models/best_model"
    frontend_url: str = "http://localhost:5173"

    max_upload_size_mb: int = 50
    local_storage_path: str = str(Path(__file__).parent.parent / "storage")
    allowed_extensions: list[str] = [".pdf"]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
