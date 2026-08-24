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
    cors_origins: str = (
        "http://localhost:3001,http://127.0.0.1:3001,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    database_path: Path = _DASHBOARD_ROOT / "data" / "rfps.db"
    pdf_storage_path: Path = _DASHBOARD_ROOT / "storage" / "pdfs"
    proposal_storage_path: Path = _DASHBOARD_ROOT / "storage" / "proposals"

    supermemory_api_key: str = ""
    supermemory_source_api_key: str = ""
    supermemory_base_url: str = "https://api.supermemory.ai"
    # Single Supermemory container for zö verified knowledge base (not active intake RFPs)
    supermemory_container_tag: str = "zo-agency"
    # Legacy — if set, first tag wins when container_tag is default-only
    supermemory_container_tags: str = ""

    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # QuickBooks Online — read-only. The refresh token rotates on use, so the
    # value here is only the seed; the live one lives in the token store
    # (see services/quickbooks_oauth.py).
    quickbooks_client_id: str = ""
    quickbooks_client_secret: str = ""
    quickbooks_refresh_token: str = ""
    quickbooks_realm_id: str = ""
    quickbooks_environment: str = "sandbox"
    quickbooks_minor_version: str = "75"
    quickbooks_cron_secret: str = ""
    # APScheduler worker (python -m app.scheduler). POSTs into the API; does not
    # hold QuickBooks or Supabase credentials of its own.
    scheduler_backend_url: str = "http://127.0.0.1:8001"
    scheduler_timezone: str = "America/Los_Angeles"
    scheduler_run_on_start: bool = True

    # Teamwork.com — read-only V3 API. Basic auth is API_KEY with an empty password.
    # Never expose these to the frontend.
    teamwork_base_url: str = ""
    teamwork_api_key: str = ""

    # Legacy optional — prefer OAuth client id/secret above
    google_service_account_json: Path | None = None
    google_drive_shared_drive_name: str = "RFPs"

    app_url: str = "http://localhost:3001"

    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Monid — server-side company enrichment only. Never expose this to the frontend.
    monid_api_key: str = ""
    monid_base_url: str = "https://api.monid.ai"
    # Role-tier router: heavy = Sonnet-class (writing/judgment); light = Haiku-class (plan/gate).
    # Empty heavy → fall back to openrouter_model. Empty light → fall back to heavy.
    llm_heavy_model: str = ""
    llm_light_model: str = "anthropic/claude-haiku-4.5"
    # Anthropic prompt caching (Claude models via OpenRouter only). Cuts the cost
    # of re-sent prompt prefixes to ~0.1x without changing what the model sees.
    # Kill switch first — if caching ever misbehaves, this restores prior behaviour.
    llm_disable_prompt_cache: bool = False
    # 1-hour TTL (write 2x base). Complete & clean / generate re-send the same RFP
    # prefix across many calls; 5-minute ephemeral expires between steps and
    # re-bills full input. Cached reads are ~0.1x — this is the 70% cost cut.
    llm_cache_ttl_1h: bool = True

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"
    
    fireworks_api_key: str = ""
    fireworks_model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct"
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    # When true, skip OpenRouter and Gemini - use Fireworks directly as primary.
    llm_prefer_fireworks: bool = False
    # Master kill switch: never call Fireworks (primary, prefer, or fallback).
    llm_disable_fireworks: bool = True
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
    # Complete & Scan review agent (three acts + four detectors).
    # QUALITY_GATE_ENABLED — the full reviewer: claim verification, RFP scoring, slop /
    # repetition / consistency detection and repair. This is the expensive stage; a
    # 24-section draft costs roughly 15-25 quality-tier calls. Leave it on for the pass
    # before submission; turn it off while iterating if you want Scan to stay quick.
    quality_gate_enabled: bool = True
    # Sections verified per claim-check call. Act 1 asked one call per section, which on
    # a 24-section draft was 24 calls to answer one question. Batching cuts that ~6x
    # with no loss of coverage — the model still sees every section and its evidence.
    quality_gate_claim_batch_chars: int = 24_000
    # Concurrency for the per-section KB retrievals behind Act 1. These are independent,
    # so running them sequentially only added latency.
    quality_gate_retrieval_concurrency: int = 10
    # Rounds of detect -> patch -> re-detect. Rounds 2+ only re-examine sections that
    # actually changed, so extra rounds stay cheap; 3 preserves accuracy.
    quality_gate_max_rounds: int = 3
    # Parallel claim-verifier batches (Act 1). Independent calls; every section still
    # reaches the model with its own evidence.
    quality_gate_verifier_batch_concurrency: int = 3
    # REPETITION_SWEEP_ENABLED — the stage-1 whole-manuscript pass. Default OFF: the
    # pipeline already dedupes at "Remove duplicate sections" (stage 6), "Compact
    # manuscript" (stage 16), and the gate's own repetition detector (stage 18). Its
    # only argument was deduping before prose is polished, but the stages before 6 are
    # structural, so it mostly pays for a fourth pass at the same job. Turn it on if
    # duplicates are surviving all three.
    repetition_sweep_enabled: bool = False

    # BUDGET_BEFORE_DRAFTING — Phase 3.5 before Phase 3 (T5.6); default off.
    budget_before_drafting: bool = False
    # MONEY_SLOTS_BLOCK — unresolved {{budget.*}} tokens block readiness.
    money_slots_block: bool = True

    # Fast / cost-optimized full proposal generation (default OFF — quality first).
    # Set FAST_PROPOSAL_GENERATION=true only for dev/staging or time-sensitive drafts.
    # When false (default): sequential Phase 3 with full prior-context chain, adversarial
    # repair, LLM structure reframe, and full contradiction coverage.
    fast_proposal_generation: bool = False
    phase3_llm_concurrency: int = 1
    designer_compact_in_generate: bool = True
    designer_compact_max_sections: int = 8
    senior_editor_max_tickets: int = 6
    # Lean generate skips duplicate blocker preflight. Ticket emit / per-tab
    # rewrites are off during generate (mechanical trim only).
    senior_editor_lean_in_generate: bool = True
    senior_editor_skip_llm_emit_in_generate: bool = True
    self_edit_repair_parallel: int = 1
    # Hard LLM run budgets (USD). 0 disables guard.
    generate_proposal_max_cost_usd: float = 3.0
    complete_scan_max_cost_usd: float = 3.0

    # Financial workspace chat budgets (USD). Enforced in qb_chat against
    # financial_llm_calls, not against the proposal run budget above — the two
    # domains share no ledger. 0 disables.
    financial_chat_max_cost_usd: float = 0.50
    financial_chat_turn_max_cost_usd: float = 0.15

    # LangSmith — process env is synced at startup (see langsmith_tracing.py).
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str = ""
    langsmith_project: str = "proposal generation"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_rfp_bucket: str = "rfp-pdfs"

    # Background job queue. Empty (local dev default) = proposal/Go-No-Go jobs
    # run in-process via asyncio.create_task, same as before Celery existed.
    # Set REDIS_URL (Railway managed Redis) to route jobs through Celery
    # workers instead — see app/celery_app.py and proposal_job_runner.py.
    redis_url: str = ""

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

    @field_validator("teamwork_base_url", "teamwork_api_key")
    @classmethod
    def strip_teamwork_str(cls, value: str) -> str:
        return (value or "").strip()

    @property
    def resolved_container_tag(self) -> str:
        if self.supermemory_container_tag != "zo-agency" or not self.supermemory_container_tags:
            return self.supermemory_container_tag
        legacy = self.supermemory_container_tags.split(",")[0].strip()
        return legacy or self.supermemory_container_tag

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def quickbooks_api_base(self) -> str:
        if self.quickbooks_environment.strip().lower() == "production":
            return "https://quickbooks.api.intuit.com"
        return "https://sandbox-quickbooks.api.intuit.com"

    @property
    def quickbooks_configured(self) -> bool:
        return bool(
            self.quickbooks_client_id
            and self.quickbooks_client_secret
            and self.quickbooks_refresh_token
            and self.quickbooks_realm_id
        )

    @property
    def teamwork_configured(self) -> bool:
        return bool(self.teamwork_base_url.strip() and self.teamwork_api_key.strip())

    @property
    def celery_enabled(self) -> bool:
        return bool(self.redis_url.strip())


settings = Settings()
