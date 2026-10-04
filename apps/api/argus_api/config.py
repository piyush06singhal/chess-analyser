"""Application configuration via environment variables (no secrets in code).

Every setting is prefixed with ``ARGUS_`` and can be overridden per
environment (shell, Docker Compose, .env).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

#: The environments Caissa recognises. Each has its own ``.env.<env>.example``
#: template and its own validation rules (see :func:`validate_settings`).
ENVIRONMENTS = ("development", "test", "staging", "production")


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(env_prefix="ARGUS_", env_file=".env", extra="ignore")

    # Application
    env: str = "development"
    log_level: str = "INFO"
    log_format: str = "text"  # "text" | "json"

    # Chess engine (empty path triggers auto-detection)
    stockfish_path: str = ""
    engine_depth: int = 12
    engine_multipv: int = 3
    engine_timeout_seconds: float = 30.0
    engine_threads: int = 1
    engine_hash_mb: int = 256
    # When > 0, searches are time-based (go movetime) instead of depth-based.
    engine_movetime_ms: int = 0
    # Analysis profile preset: fast | standard | deep (see argus.analysis.pipeline).
    analysis_profile: str = "standard"
    analysis_multipv: int = 3
    analysis_cache_entries: int = 2048

    # External game sources. Chess.com exposes a public, key-less read API; only
    # the base URL and the identifying User-Agent it asks consumers to send are
    # configurable. No credentials are involved.
    chesscom_base_url: str = "https://api.chess.com/pub"
    chesscom_user_agent: str = (
        "Caissa/1.0 (chess analysis; +https://github.com/argus-chess)"
    )
    chesscom_timeout_seconds: float = 20.0
    # How many recent monthly archives the connect screen walks by default.
    chesscom_archive_months: int = 3

    # Lichess exposes a public, key-less read API. Same rules as Chess.com: no
    # credentials, and only the identifying User-Agent is configurable. Lichess
    # publishes no archive index, so the window is a number of calendar months.
    lichess_base_url: str = "https://lichess.org"
    lichess_timeout_seconds: float = 20.0
    lichess_archive_months: int = 3

    # Database (empty URL disables persistence features; SQLite/PostgreSQL supported)
    database_url: str = ""

    # Cache (prepared for Phase 2; Phase 1 only reports configuration status)
    redis_url: str = ""

    # LLM coach (provider comes from configuration; the API key is never
    # hardcoded and never logged). Empty provider disables the coach and the
    # API answers with an honest 501. "echo" is a self-identifying
    # development provider that performs no external API calls. "groq" uses
    # Groq's OpenAI-compatible API (a different base URL and default model).
    llm_provider: str = ""  # "openai" | "anthropic" | "groq" | "echo" | ""
    llm_model: str = ""  # provider default when empty
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.3
    llm_max_output_tokens: int = 1200
    llm_timeout_seconds: float = 60.0

    # Player Intelligence (Phase 5). The sample-size thresholds are part of the
    # product's honesty contract, so they are configuration rather than
    # constants: raising them makes the product claim less, lowering them makes
    # it claim more from the same games. See argus.player_intelligence.policy.
    player_min_games_for_profile: int = 2
    player_min_games_for_tendency: int = 20
    player_min_games_for_strong_claim: int = 50
    #: Safety cap on how many analyzed games one profile build reads (newest
    #: first). Keeps a profile request bounded; it is stated in the response.
    player_profile_max_games: int = 500

    # ML model store (filesystem location for versioned model artifacts)
    models_dir: str = "data/models"

    # --- Authentication (Phase 15) -----------------------------------------
    # API keys as ``key:caller:role`` entries, comma-separated. Empty means the
    # deployment is *open* (single-user/local); every request is the 'local'
    # caller. A non-empty value turns authentication on for every request.
    api_keys: str = ""

    # --- Rate limits (Phase 15) --------------------------------------------
    # Per-caller fixed-window limits for expensive operations. 0 disables a
    # bucket. These are enforced per process; see docs/production for the
    # multi-process plan.
    rate_limit_window_seconds: float = 60.0
    rate_limit_analysis_per_minute: int = 30
    rate_limit_import_per_minute: int = 30
    rate_limit_coach_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10
    rate_limit_search_per_minute: int = 60

    # --- Request limits ----------------------------------------------------
    # Hard cap on an incoming request body (0 disables). The PGN upload route
    # has its own, smaller limit in services/uploads.py.
    max_request_bytes: int = 4_000_000

    # --- Engine resource management (Phase 15) -----------------------------
    # Maximum concurrent engine-using analyses in this process. A queue beyond
    # this waits rather than starting another Stockfish search, so a single
    # caller cannot exhaust the machine. 0 means unlimited (not recommended).
    engine_max_concurrency: int = 2
    # How many games may be *queued* for analysis before new requests are
    # refused with a busy signal.
    analysis_queue_limit: int = 64

    # --- Feature flags (Phase 15) ------------------------------------------
    # Server-controlled, environment-aware flags. Format: ``name=true`` entries
    # separated by commas. Unknown flags default to off.
    feature_flags: str = ""

    # --- CORS
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:3100,http://127.0.0.1:3100"
    )

    # --- Environment helpers ----------------------------------------------

    @property
    def is_production(self) -> bool:
        """Whether this deployment claims to be production.

        A measured property, not a vibe: the environment name is what
        :func:`validate_settings` and the safety checks key off.
        """
        return self.env.strip().lower() == "production"

    @property
    def feature_flag_map(self) -> dict[str, bool]:
        """Parse ``ARGUS_FEATURE_FLAGS`` into ``{name: enabled}``.

        Format: ``name=true,name2=false`` (case-insensitive). An entry without a
        value, or with an unparseable one, is treated as *on* (``name`` alone) or
        off respectively — but only for a name that is explicitly listed, so an
        unknown flag can never be accidentally enabled. Unknown flags default to
        off in :func:`argus_api.feature_flags.is_enabled`.
        """
        flags: dict[str, bool] = {}
        for entry in (self.feature_flags or "").split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" in entry:
                name, _, raw = entry.partition("=")
                flags[name.strip().lower()] = raw.strip().lower() in {"1", "true", "yes", "on"}
            else:
                flags[entry.lower()] = True
        return flags

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS origins from either a JSON list or a comma-separated string.

        Accepts both ``["http://a", "http://b"]`` (docker-compose env style)
        and ``http://a,http://b`` — quoting/brackets are stripped, which a
        naive comma-split silently leaves in place and breaks matching.
        """
        raw = (self.cors_origins or "").strip()
        if not raw:
            return []
        if raw.startswith("["):
            import json

            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return cached settings (one instance per process)."""
    return Settings()


def validate_settings(settings: Settings, *, environment: str | None = None) -> list[str]:
    """Return every configuration problem for a deployment, as human-readable strings.

    The list is empty when the configuration is valid. Problems are collected
    rather than raised one at a time, so an operator fixes the whole list in one
    pass. This is what ``python scripts/validate_config.py --environment <env>``
    runs before a deploy; it is also the check a CI/staging pipeline asserts on.

    The rules are deliberately *honesty* rules, matching Caissa's contract:

    * the environment name must be one of :data:`ENVIRONMENTS`;
    * a production deployment must not run in open mode (no API keys);
    * a production deployment must set a database URL and JSON logging;
    * a production deployment must not use the ``echo`` development provider;
    * the CORS origins must parse to a non-empty list;
    * rate limits and engine limits must be non-negative.
    """
    env = (environment or settings.env or "development").strip().lower()
    problems: list[str] = []

    if env not in ENVIRONMENTS:
        problems.append(
            f"Unknown environment {env!r}; expected one of {', '.join(ENVIRONMENTS)}."
        )

    if not settings.cors_origin_list:
        problems.append("ARGUS_CORS_ORIGINS is empty; the browser will be blocked by CORS.")

    for name in (
        "rate_limit_analysis_per_minute",
        "rate_limit_import_per_minute",
        "rate_limit_coach_per_minute",
        "rate_limit_upload_per_minute",
        "rate_limit_search_per_minute",
        "rate_limit_window_seconds",
        "engine_max_concurrency",
        "analysis_queue_limit",
        "max_request_bytes",
    ):
        value = getattr(settings, name)
        if isinstance(value, (int, float)) and value < 0:
            problems.append(f"{name} must not be negative (got {value}).")

    if env == "production":
        if not (settings.api_keys or "").strip():
            problems.append(
                "Production requires ARGUS_API_KEYS: an open deployment has no "
                "authentication and every request is the 'local' caller."
            )
        if not (settings.database_url or "").strip():
            problems.append("Production requires ARGUS_DATABASE_URL (PostgreSQL).")
        elif settings.database_url.strip().startswith("sqlite"):
            problems.append(
                "Production requires PostgreSQL; SQLite is for local development and tests."
            )
        if settings.log_format.strip().lower() != "json":
            problems.append(
                "Production should set ARGUS_LOG_FORMAT=json so logs are aggregatable."
            )
        if (settings.llm_provider or "").strip().lower() == "echo":
            problems.append(
                "The 'echo' LLM provider is a development stub and must not be used in production."
            )

    return problems
