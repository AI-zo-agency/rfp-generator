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
    # Role-tier router: heavy = Sonnet-class (writing/judgment); light = Haiku-class (plan/gate).
    # Empty heavy → fall back to openrouter_model. Empty light → fall back to heavy.
    llm_heavy_model: str = ""
    llm_light_model: str = "anthropic/claude-haiku-4.5"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"
    
    fireworks_api_key: str = ""
    fireworks_model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct"
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    # When true, skip OpenRouter and Gemini - use Fireworks directly as primary.
    llm_prefer_fireworks: bool = False
    # When true, skip Gemini - use OpenRouter as primary (before Fireworks).
    llm_prefer_openrouter: bool = False
    # Master switch for OpenRouter usability. A key can be present but unfunded;
    # set this false to keep quality-critical stages on the economy provider
    # instead of paying a failed round-trip before every fallback. Flip to true
    # when credits are topped up and judgment stages move to the heavy model
    # with no code change.
    llm_openrouter_enabled: bool = True

    # Phase 1: decision-first Company Qualification Layer for Section 1.
    use_company_qualification_s1: bool = False

    # State-canonicalization gates (detection always available; blocking is flagged).
    # CONSISTENCY_CRITICALS_BLOCK — promote scan_manuscript_consistency criticals
    # into assert_manuscript_ready blockers (default off until FP review).
    consistency_criticals_block: bool = False
    # T1_GATES_BLOCK — promote note-leak / truncation T1 findings to blockers.
    t1_gates_block: bool = False

    # W6 — shared Phase 2 evidence corpus + duplication enforcement.
    # PERSIST_PHASE2_EVIDENCE_CORPUS — store retrieved writing assets at end of Phase 2.
    persist_phase2_evidence_corpus: bool = True
    # JIT_RETRIEVAL_ON_MISS — if section has no shared-corpus hits, fall back to JIT.
    jit_retrieval_on_miss: bool = True
    # OVERLAP_GATES_BLOCK — promote high Jaccard overlap findings to readiness blockers.
    overlap_gates_block: bool = False
    # ADVERSARIAL_AUDIT_BLOCK — promote persisted whole-manuscript audit criticals.
    adversarial_audit_block: bool = False
    # ADVERSARIAL_REPAIR_LOOP — bounded validate→repair→re-validate loop after Phase 4 autofix.
    adversarial_repair_loop: bool = True
    adversarial_repair_max_rounds: int = 3
    adversarial_repair_max_attempts_per_finding: int = 3
    adversarial_repair_time_budget_sec: int = 540
    # Cap LLM/KB work per round (criticals already preferred by collector).
    adversarial_repair_max_findings_per_round: int = 12
    # Full LLM residual audit only on first + final round; middle rounds deterministic.
    adversarial_repair_llm_audit_each_round: bool = False
    # BUDGET_BEFORE_DRAFTING — Phase 3.5 before Phase 3 (T5.6); default off.
    budget_before_drafting: bool = False
    # MONEY_SLOTS_BLOCK — unresolved {{budget.*}} tokens block readiness.
    money_slots_block: bool = True

    # LangSmith — process env is synced at startup (see langsmith_tracing.py).
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str = ""
    langsmith_project: str = "proposal generation"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_rfp_bucket: str = "rfp-pdfs"

    # JustWin Playwright sync (read from backend/.env or Railway env)
    justwin_email: str = ""
    justwin_password: str = ""
    justwin_base_url: str = "https://app.justwin.ai"
    justwin_api_root: str = "https://api.justwin.ai"
    justwin_session_path: str = ""
    justwin_headless: bool = True
    justwin_rfp_title_filter: str = "*"

    @field_validator("supermemory_container_tag")
    @classmethod
    def normalize_container_tag(cls, value: str) -> str:
        tag = value.strip()
        if not tag:
            raise ValueError("SUPERMEMORY_CONTAINER_TAG cannot be empty")
        return tag

    @field_validator("justwin_email", "justwin_password", "justwin_base_url", "justwin_api_root", "justwin_session_path")
    @classmethod
    def strip_justwin_str(cls, value: str) -> str:
        text = (value or "").strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
            text = text[1:-1].strip()
        return text

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
