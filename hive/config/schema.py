"""Validated configuration for the foundation and later modules."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CoreConfig(StrictModel):
    mode: str = "operator"
    language: str = "auto"
    local_first: bool = True


class PathConfig(StrictModel):
    data_dir: Path | None = None
    state_dir: Path | None = None
    config_dir: Path | None = None


class LocalModelConfig(StrictModel):
    provider: str = "ollama"
    base_url: HttpUrl = "http://127.0.0.1:11434"  # type: ignore[assignment]
    model: str = "qwen2.5:7b-instruct-q4_K_M"


class RouterModelConfig(StrictModel):
    model: str = "qwen2.5:3b-instruct"


class CloudFallbackConfig(StrictModel):
    enabled: bool = False
    provider: str = "openai_compatible"
    api_key_env: str = "HIVE_CLOUD_API_KEY"
    base_url: HttpUrl | None = None
    model: str | None = None


class ModelsConfig(StrictModel):
    local: LocalModelConfig = Field(default_factory=LocalModelConfig)
    router: RouterModelConfig = Field(default_factory=RouterModelConfig)
    cloud_fallback: CloudFallbackConfig = Field(default_factory=CloudFallbackConfig)


class MultiAgentConfig(StrictModel):
    enabled: bool = True
    max_active_agents: int = Field(default=3, ge=1)
    max_executing_agents: int = Field(default=2, ge=1)
    max_local_llm_requests: int = Field(default=1, ge=1)
    max_cloud_llm_requests: int = Field(default=2, ge=1)


class RetryConfig(StrictModel):
    max_retries: int = Field(default=2, ge=0, le=2)
    initial_delay_seconds: float = Field(default=2, ge=0)
    backoff_multiplier: float = Field(default=2, ge=1)
    jitter: bool = True


class ResourceManagementConfig(StrictModel):
    enforce_cleanup: bool = True
    cleanup_timeout_seconds: int = Field(default=10, gt=0)
    watchdog_interval_seconds: int = Field(default=15, gt=0)
    retain_failed_artifacts: bool = True


class ApprovalConfig(StrictModel):
    require_plan_approval: bool = True
    require_high_risk_approval: bool = True
    approval_expiry_minutes: int = Field(default=30, gt=0)


class PerformanceConfig(StrictModel):
    cache_enabled: bool = True
    stream_llm_output: bool = True
    lazy_load_modules: bool = True
    max_parallel_web_requests: int = Field(default=4, ge=1)


class ObservabilityConfig(StrictModel):
    structured_logs: bool = True
    metrics_enabled: bool = True
    audit_log: bool = True


class SecurityConfig(StrictModel):
    block_forbidden_by_default: bool = True
    redact_secrets: bool = True
    sandbox_experiments: bool = True


class BrowserConfig(StrictModel):
    enabled: bool = False
    isolated_profile: bool = True
    visible: bool = True
    max_tabs_per_agent: int = Field(default=5, ge=1)


class VoiceConfig(StrictModel):
    enabled: bool = False
    stt_engine: str = "faster_whisper"
    tts_engine: str = "piper"
    language_mode: str = "auto"


class TimeoutConfig(StrictModel):
    llm_request_seconds: int = Field(default=120, gt=0)
    web_request_seconds: int = Field(default=30, gt=0)
    command_default_seconds: int = Field(default=300, gt=0)
    sudo_prompt_seconds: int = Field(default=120, gt=0)
    approval_expiry_minutes: int = Field(default=30, gt=0)


class HiveConfig(StrictModel):
    core: CoreConfig = Field(default_factory=CoreConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    multi_agent: MultiAgentConfig = Field(default_factory=MultiAgentConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    resource_management: ResourceManagementConfig = Field(default_factory=ResourceManagementConfig)
    approval: ApprovalConfig = Field(default_factory=ApprovalConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
