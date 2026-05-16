from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, Field


class OnboardingStepResponse(BaseModel):
    step_id: str
    label: str
    lifecycle_state: str
    phase: str
    complete: bool = False
    current: bool = False


class ApiHealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class AssistantTurnRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    session_id: str | None = None


class AssistantTurnResponse(BaseModel):
    endpoint_id: str
    session_id: str
    heard_text: str
    reply_text: str
    spoken_text: str
    handled_locally: bool
    command: str | None = None
    device_state: Literal["idle", "listening", "thinking", "speaking"]
    provider_id: str = "local_echo"
    model: str | None = None
    error: str | None = None
    intent_latency_ms: float | None = None
    conversation_followup: dict[str, Any] | None = None


class VoiceIntentRegisterRequest(BaseModel):
    intent_id: str = Field(min_length=1, max_length=120)
    service_id: str = Field(default="voice.local_intents", min_length=1, max_length=160)
    intent_name: str | None = Field(default=None, max_length=160)
    owner_service: str | None = Field(default=None, max_length=160)
    owner_client_id: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=80)
    status: str = "active"
    privacy_class: str = "internal"
    access_scope: str = "service"
    definition: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceIntentUpdateRequest(BaseModel):
    service_id: str | None = Field(default=None, max_length=160)
    intent_name: str | None = Field(default=None, max_length=160)
    owner_service: str | None = Field(default=None, max_length=160)
    owner_client_id: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=80)
    privacy_class: str | None = None
    access_scope: str | None = None
    definition: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class VoiceIntentLifecycleRequest(BaseModel):
    status: str
    reason: str | None = None


class VoiceIntentReviewRequest(BaseModel):
    reviewed_by: str | None = None
    review_reason: str | None = None
    status: str | None = "active"


class VoiceIntentDispatchRequest(BaseModel):
    endpoint_id: str = Field(default="intent-test", min_length=1)
    text: str = Field(min_length=1)
    session_id: str | None = None


class VoiceIntentStateResponse(BaseModel):
    configured: bool
    schema_version: str
    registered_count: int
    active_count: int
    updated_at: str | None = None
    intents: list[dict[str, Any]] = Field(default_factory=list)


class VoiceIntentLookupResponse(BaseModel):
    configured: bool = True
    intent: dict[str, Any]


class VoiceIntentDispatchResponse(BaseModel):
    matched: bool
    intent_id: str | None = None
    command: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    reply_text: str | None = None
    provider_id: str | None = None


class VoiceIntentInvokeResponse(BaseModel):
    matched: bool
    endpoint_id: str
    session_id: str
    heard_text: str
    intent_id: str | None = None
    command: str | None = None
    slots: dict[str, Any] = Field(default_factory=dict)
    reply_text: str | None = None
    provider_id: str | None = None
    recognized_event_id: str | None = None
    recognition_event: dict[str, Any] | None = None
    dispatch_event: dict[str, Any] | None = None
    reply_audio: dict[str, Any] | None = None
    conversation_followup: dict[str, Any] | None = None
    latency_ms: float | None = None


class TtsSynthesizeTarget(BaseModel):
    device_id: str | None = None
    location: str | None = None
    client_ip: str | None = None
    playback: str | None = None


class TtsSynthesizeRequest(BaseModel):
    intent: Literal["tts.speak"] = "tts.speak"
    target: TtsSynthesizeTarget = Field(default_factory=TtsSynthesizeTarget)
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = Field(default=None, max_length=80)
    format: Literal["wav", "mp3"] = "wav"
    ttl_seconds: int = Field(default=3600, ge=5, le=3600)


class TtsSynthesizeResponse(BaseModel):
    status: Literal["ready", "failed"]
    audio_url: str | None = None
    endpoint_audio_url: str | None = None
    audio_urls: dict[str, str] = Field(default_factory=dict)
    content_type: str | None = None
    duration_ms: int | None = None
    expires_at: str | None = None
    stream_id: str | None = None
    provider_id: str | None = None
    error: str | None = None


class EndpointHeartbeatRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    device_state: Literal["idle", "listening", "thinking", "speaking", "offline"] = "idle"
    session_id: str | None = None
    firmware_version: str | None = None
    ip_address: str | None = None
    rssi_dbm: int | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class EndpointHeartbeatResponse(BaseModel):
    accepted: bool = True
    endpoint_id: str
    device_state: Literal["idle", "listening", "thinking", "speaking", "offline"]
    session_id: str | None = None
    server_time: str
    last_seen_at: str


class EndpointTimeResponse(BaseModel):
    server_time: str
    server_unix_ms: int
    timezone: str
    utc_offset_seconds: int
    sync_interval_ms: int = 300_000


class EndpointStatusResponse(BaseModel):
    endpoint_id: str
    display_name: str | None = None
    zone_id: str | None = None
    device_state: Literal["idle", "listening", "thinking", "speaking", "offline"]
    session_id: str | None = None
    firmware_version: str | None = None
    ip_address: str | None = None
    rssi_dbm: int | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: str | None = None
    last_seen_at: str
    connection_state: Literal["online", "stale", "offline"]
    stale: bool = False
    firmware_update: dict[str, Any] = Field(default_factory=dict)


class EndpointRegistryListResponse(BaseModel):
    endpoints: list[EndpointStatusResponse] = Field(default_factory=list)


class EndpointMetadataUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    zone_id: str | None = Field(default=None, max_length=80)


class EndpointVolumeCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    volume_percent: int = Field(ge=0, le=100)


class EndpointVolumeCommandResponse(BaseModel):
    accepted: bool
    endpoint_id: str
    volume_percent: int
    request_id: str | None = None
    status: str | None = None
    reason: str | None = None


class EndpointVolumeStatusResponse(BaseModel):
    endpoint_id: str
    volume_percent: int | None = None
    latest_command: dict[str, Any] | None = None


class EndpointMuteCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    muted: bool


class EndpointMicroVadCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    pause_ms: int = Field(ge=80, le=3000)


class EndpointCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)


class EndpointLedSimulateCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    pattern: str = Field(default="all", min_length=1, max_length=40)
    duration_ms: int = Field(default=1200, ge=300, le=5000)


class VoiceSessionReplayRequest(BaseModel):
    endpoint_id: str | None = Field(default=None, min_length=1)


class VoiceSessionHistoryListResponse(BaseModel):
    sessions: list[dict[str, Any]] = Field(default_factory=list)


class VoiceSessionHistoryDetailResponse(BaseModel):
    session: dict[str, Any]


class EndpointSpeakCommandRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, min_length=1, max_length=120)


class EndpointCommandResponse(BaseModel):
    accepted: bool
    endpoint_id: str
    command_type: str
    request_id: str | None = None
    status: str | None = None
    reason: str | None = None


class EndpointMediaUploadRequest(BaseModel):
    media_type: Literal["picture", "sprite", "sound"]
    filename: str = Field(min_length=1, max_length=120)
    content_base64: str = Field(min_length=1)
    asset_id: str | None = Field(default=None, max_length=80)
    content_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False
    rewrite: bool | None = None
    activate: bool = True


class EndpointMediaAssetResponse(BaseModel):
    asset_id: str
    media_type: Literal["picture", "sprite", "sound"]
    destination: str
    endpoint_path: str
    filename: str
    source_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    download_url: str | None = None


class EndpointMediaListResponse(BaseModel):
    assets: list[EndpointMediaAssetResponse] = Field(default_factory=list)


class EndpointMediaInventoryItem(BaseModel):
    filename: str
    size_bytes: int | None = None
    sha256: str | None = None
    content_type: str | None = None
    updated_at: str | None = None


class EndpointMediaInventoryResponse(BaseModel):
    endpoint_id: str
    pictures: list[EndpointMediaInventoryItem] = Field(default_factory=list)
    sprites: list[EndpointMediaInventoryItem] = Field(default_factory=list)
    sounds: list[EndpointMediaInventoryItem] = Field(default_factory=list)
    truncated: bool = False
    last_seen_at: str | None = None


class EndpointMediaDeliverRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    overwrite: bool = True
    rewrite: bool | None = None
    activate: bool = True


class EndpointMediaDeliverResponse(BaseModel):
    accepted: bool
    endpoint_id: str
    asset: EndpointMediaAssetResponse
    request_id: str | None = None
    status: str | None = None
    reason: str | None = None


class FirmwareOtaPushRequest(BaseModel):
    endpoint_id: str = Field(min_length=1)
    filename: str = "hexe_firmware.bin"
    version: str | None = None


class FirmwareOtaPushResponse(BaseModel):
    accepted: bool
    endpoint_id: str
    firmware_url: str
    version: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    reason: str | None = None


class NodeStatusResponse(BaseModel):
    node_name: str
    node_type: str
    node_id: str | None
    lifecycle_state: str
    current_step_id: str
    current_step_label: str
    trust_state: str
    operational_ready: bool
    capability_status: str = "missing"
    governance_sync_status: str = "pending_capability"
    active_governance_version: str | None = None
    governance_freshness_state: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)


class CapabilitySetupReadinessFlags(BaseModel):
    trust_state_valid: bool
    node_identity_valid: bool
    provider_selection_valid: bool
    task_capability_selection_valid: bool
    core_runtime_context_valid: bool


class CapabilitySetupProviderSelectionResponse(BaseModel):
    configured: bool
    enabled_count: int
    enabled: list[str] = Field(default_factory=list)
    supported: dict[str, list[str]] = Field(default_factory=dict)


class CapabilitySetupTaskSelectionResponse(BaseModel):
    configured: bool
    selected_count: int
    selected: list[str] = Field(default_factory=list)
    available: list[str] = Field(default_factory=list)


class CapabilitySetupResponse(BaseModel):
    readiness_flags: CapabilitySetupReadinessFlags
    provider_selection: CapabilitySetupProviderSelectionResponse
    task_capability_selection: CapabilitySetupTaskSelectionResponse
    blocking_reasons: list[str] = Field(default_factory=list)
    declaration_allowed: bool = False


class OnboardingStatusResponse(BaseModel):
    onboarding_state: str
    lifecycle_state: str
    trust_state: str
    current_step_id: str
    current_step_label: str
    next_action: str
    session_id: str | None = None
    approval_url: str | None = None
    expires_at: str | None = None
    finalize_url: str | None = None
    session_state: str | None = None
    last_polled_at: str | None = None
    last_terminal_outcome: str | None = None
    support_state: str | None = None
    trust_last_checked_at: str | None = None
    trust_message: str | None = None
    capability_status: str = "missing"
    governance_sync_status: str = "pending_capability"
    operational_ready: bool = False
    active_governance_version: str | None = None
    governance_freshness_state: str | None = None
    capability_setup: CapabilitySetupResponse | None = None
    last_error: str | None = None
    steps: list[OnboardingStepResponse] = Field(default_factory=list)


class NodeIdentitySetupRequest(BaseModel):
    node_name: str
    protocol_version: str
    node_nonce: str
    requested_node_id: str | None = None
    hostname: str | None = None
    ui_endpoint: AnyHttpUrl | None = None
    api_base_url: AnyHttpUrl | None = None


class NodeIdentitySetupResponse(BaseModel):
    configured: bool
    node_name: str | None = None
    protocol_version: str | None = None
    node_nonce: str | None = None
    requested_node_id: str | None = None
    hostname: str | None = None
    ui_endpoint: str | None = None
    api_base_url: str | None = None


class CoreConnectionSetupRequest(BaseModel):
    core_base_url: AnyHttpUrl


class CoreConnectionSetupResponse(BaseModel):
    configured: bool
    core_base_url: str | None = None


class LocalSetupStateResponse(BaseModel):
    node_identity: NodeIdentitySetupResponse
    core_connection: CoreConnectionSetupResponse


class NodeMigrationExportRequest(BaseModel):
    include_trust_secrets: bool = False


class NodeMigrationImportRequest(BaseModel):
    bundle: dict[str, Any]
    destination_core_base_url: AnyHttpUrl | None = None
    destination_api_base_url: AnyHttpUrl | None = None
    destination_ui_endpoint: AnyHttpUrl | None = None
    destination_hostname: str | None = Field(default=None, max_length=120)


class NodeMigrationImportResponse(BaseModel):
    imported: bool
    files_imported: list[str] = Field(default_factory=list)
    node_id: str | None = None
    core_base_url: str | None = None
    api_base_url: str | None = None
    ui_endpoint: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BootstrapAdvertisementRequest(BaseModel):
    topic: str
    api_base: str | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    onboarding_mode: str | None = None
    onboarding_contract: str | None = None
    onboarding_endpoints: dict[str, str] = Field(default_factory=dict)


class BootstrapDiscoveryResponse(BaseModel):
    configured: bool
    bootstrap_topic: str
    bootstrap_host: str | None = None
    bootstrap_port: int
    connection_status: str
    advertisement_valid: bool
    onboarding_mode: str | None = None
    onboarding_contract: str | None = None
    api_base: str | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    register_session_endpoint: str | None = None
    registrations_endpoint: str | None = None
    compatibility_register_endpoint: str | None = None
    compatibility_ai_node_register_endpoint: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None


class OnboardingSessionStartResponse(BaseModel):
    session_id: str
    approval_url: str
    expires_at: str | None = None
    finalize_url: str | None = None
    node_name: str | None = None
    node_type: str | None = None
    node_software_version: str | None = None


class OnboardingSessionPollResponse(BaseModel):
    session_id: str
    session_state: str
    last_polled_at: str | None = None
    last_terminal_outcome: str | None = None
    activation_received: bool = False


class TrustActivationFinalizeResponse(BaseModel):
    node_id: str
    paired_core_id: str | None = None
    trust_state: str
    baseline_policy_version: str | None = None
    operational_mqtt_identity: str | None = None
    operational_mqtt_host: str | None = None
    operational_mqtt_port: int | None = None
    activation_applied_at: str | None = None


class TrustStatusRefreshResponse(BaseModel):
    node_id: str
    trust_state: str
    supported: bool | None = None
    support_state: str | None = None
    registry_present: bool | None = None
    registry_state: str | None = None
    revoked_at: str | None = None
    revocation_reason: str | None = None
    revocation_action: str | None = None
    trust_message: str | None = None
    trust_last_checked_at: str | None = None


class ProviderSetupRequest(BaseModel):
    enabled_providers: list[str] = Field(default_factory=list)
    default_provider: str | None = None


class ProviderConfigRequest(BaseModel):
    enabled: bool = True
    default: bool = False
    model: str | None = None
    device: str | None = None
    compute_type: str | None = None
    warm_model: bool | None = None
    warm_models: list[str] = Field(default_factory=list)
    default_voice: str | None = None
    default_wakeword: str | None = None


class ProviderSetupResponse(BaseModel):
    configured: bool
    supported_providers: list[str] = Field(default_factory=list)
    enabled_providers: list[str] = Field(default_factory=list)
    default_provider: str | None = None
    provider_configs: dict[str, dict[str, object]] = Field(default_factory=dict)
    declaration_allowed: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)


class CapabilitySummaryResponse(BaseModel):
    configured: list[str] = Field(default_factory=list)
    available: list[str] = Field(default_factory=list)
    selected: list[str] = Field(default_factory=list)
    declared: list[str] = Field(default_factory=list)
    capability_status: str = "missing"
    capability_profile_id: str | None = None
    accepted_at: str | None = None
    governance_version: str | None = None


class CapabilitySelectionRequest(BaseModel):
    selected_capabilities: list[str] = Field(default_factory=list)


class CapabilityDeclarationResponse(BaseModel):
    capability_status: str
    node_id: str
    manifest_version: str
    accepted_at: str | None = None
    declared_capabilities: list[str] = Field(default_factory=list)
    enabled_providers: list[str] = Field(default_factory=list)
    capability_profile_id: str | None = None
    governance_version: str | None = None
    governance_issued_at: str | None = None


class GovernanceBundleResponse(BaseModel):
    node_id: str
    capability_profile_id: str | None = None
    governance_version: str | None = None
    issued_timestamp: str | None = None
    refresh_interval_s: int | None = None
    governance_bundle: dict = Field(default_factory=dict)


class GovernanceRefreshResponse(BaseModel):
    updated: bool
    governance_version: str | None = None
    refresh_interval_s: int | None = None
    governance_bundle: dict | None = None


class OperationalStatusResponse(BaseModel):
    node_id: str
    lifecycle_state: str
    trust_status: str
    capability_status: str
    governance_status: str
    operational_ready: bool
    active_governance_version: str | None = None
    last_governance_issued_at: str | None = None
    last_governance_refresh_request_at: str | None = None
    governance_freshness_state: str | None = None
    governance_freshness_changed_at: str | None = None
    governance_stale_for_s: int | None = None
    governance_outdated: bool = False
    last_telemetry_timestamp: str | None = None
    updated_at: str | None = None


class GovernanceReadinessResponse(BaseModel):
    operational_ready: bool
    degraded: bool
    blocking_reasons: list[str] = Field(default_factory=list)


class ProviderStatusResponse(BaseModel):
    provider_id: str
    configured: bool
    healthy: bool
    status: str


class ServiceStatusResponse(BaseModel):
    backend: str
    frontend: str
    scheduler: str
    openwakeword: str = "unknown"
    piper_tts: str = "unknown"
    components: list[dict[str, Any]] = Field(default_factory=list)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    supervisor: dict[str, Any] = Field(default_factory=dict)


class ServiceActionRequest(BaseModel):
    target: str = Field(min_length=1)


class ServiceActionResponse(BaseModel):
    target: str
    action: str
    accepted: bool
    status: str
    detail: str | None = None
