from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_ROOT = _BACKEND_ROOT.parent / "frontend"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ZO RFP API"
    port: int = 8001
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_path: Path = _DASHBOARD_ROOT / "data" / "rfps.db"
    pdf_storage_path: Path = _DASHBOARD_ROOT / "storage" / "pdfs"
    proposal_storage_path: Path = _DASHBOARD_ROOT / "storage" / "proposals"

    supermemory_api_key: str = ""
    supermemory_base_url: str = "https://api.supermemory.ai"
    # Single Supermemory container for zö verified knowledge base (not active intake RFPs)
    supermemory_container_tag: str = "zo-agency"
    # Legacy — if set, first tag wins when container_tag is default-only
    supermemory_container_tags: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # Legacy optional — prefer OAuth client id/secret above
    google_service_account_json: Path | None = None
    google_drive_shared_drive_name: str = "RFPs"

    app_url: str = "http://localhost:3000"

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    fireworks_api_key: str = ""
    fireworks_model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct"
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    # When true, skip OpenRouter (e.g. out of credits) and use Fireworks directly.
    llm_prefer_fireworks: bool = False

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_rfp_bucket: str = "rfp-pdfs"

    @field_validator("supermemory_container_tag")
    @classmethod
    def normalize_container_tag(cls, value: str) -> str:
        tag = value.strip()
        if not tag:
            raise ValueError("SUPERMEMORY_CONTAINER_TAG cannot be empty")
        return tag

    @property
    def resolved_container_tag(self) -> str:
        if self.supermemory_container_tag != "zo-agency" or not self.supermemory_container_tags:
            return self.supermemory_container_tag
        legacy = self.supermemory_container_tags.split(",")[0].strip()
        return legacy or self.supermemory_container_tag

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
