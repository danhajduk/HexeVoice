from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import re
from threading import Lock
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from hexevoice.config.settings import Settings
from hexevoice.persistence.onboarding_state import OnboardingStateStore


log = logging.getLogger(__name__)

WEATHER_EVENT_TYPE = "weather.snapshot.updated"
WEATHER_SCHEMA_VERSION = "weather.snapshot.v1"
WEATHER_TOPIC_RE = re.compile(r"^hexe/nodes/([^/]+)/events/weather/snapshot/updated$")
WEATHER_PROMOTED_TOPIC = "hexe/events/weather/snapshot/updated"
WEATHER_RESULT_TYPES = {"weather.current_succeeded", "weather.current_failed"}
WEATHER_RESULT_TOPIC_RE = re.compile(r"^hexe/nodes/([^/]+)/events/weather/current_(succeeded|failed)$")
WEATHER_PROMOTED_RESULT_TOPIC_RE = re.compile(r"^hexe/events/weather/current_(succeeded|failed)$")
WEATHER_RESULT_TOPICS = (
    "hexe/events/weather/current_succeeded",
    "hexe/events/weather/current_failed",
)
COMPONENT_STATES = {"pending", "ready", "stale", "expired", "absent", "failed", "queued"}
FRESH_WEATHER_STATES = {"fresh", "stale"}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class WeatherSnapshotError(ValueError):
    pass


class WeatherSnapshotStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()

    def load(self) -> dict[str, Any] | None:
        with self._lock:
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return payload if isinstance(payload, dict) else None

    def save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        serialized = json.dumps(payload, indent=2, sort_keys=True)
        with self._lock:
            temporary.write_text(serialized, encoding="utf-8")
            temporary.replace(self._path)


class WeatherSnapshotService:
    def __init__(
        self,
        *,
        settings: Settings,
        onboarding_state_store: OnboardingStateStore | None = None,
        snapshot_store: WeatherSnapshotStore | None = None,
    ) -> None:
        self._settings = settings
        self._onboarding_store = onboarding_state_store or OnboardingStateStore(
            path=settings.resolved_onboarding_state_path()
        )
        self._store = snapshot_store or WeatherSnapshotStore(
            settings.resolved_onboarding_state_path().parent / "weather_snapshot.json"
        )
        self._client: Any = None
        self._running = False
        self._status = "stopped"
        self._reason: str | None = None
        self._last_rejected: dict[str, Any] | None = None
        self._accepted = self._store.load()
        self._listeners: list[Any] = []
        self._result_listeners: list[Any] = []
        self._last_result: dict[str, Any] | None = None
        self._seen_result_ids: list[str] = []

    def add_listener(self, listener: Any) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: Any) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def add_result_listener(self, listener: Any) -> None:
        if listener not in self._result_listeners:
            self._result_listeners.append(listener)

    def remove_result_listener(self, listener: Any) -> None:
        if listener in self._result_listeners:
            self._result_listeners.remove(listener)

    def start(self) -> dict[str, Any]:
        if self._running:
            return self.status()
        state = self._onboarding_store.load()
        trust = state.trust_activation
        if state.operational_status.operational_ready is not True or trust.trust_status != "trusted":
            self._status = "skipped"
            self._reason = "trusted_operational_node_required"
            return self.status()
        if not trust.operational_mqtt_identity or not trust.operational_mqtt_token:
            self._status = "skipped"
            self._reason = "missing_operational_mqtt_credentials"
            return self.status()
        if not trust.operational_mqtt_host or not trust.operational_mqtt_port:
            self._status = "skipped"
            self._reason = "missing_operational_mqtt_endpoint"
            return self.status()
        try:
            import paho.mqtt.client as mqtt

            client_id = f"{trust.operational_mqtt_identity}-hexevoice-weather"
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            except AttributeError:
                client = mqtt.Client(client_id=client_id)
            client.username_pw_set(trust.operational_mqtt_identity, trust.operational_mqtt_token)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect_async(trust.operational_mqtt_host, int(trust.operational_mqtt_port), keepalive=30)
            client.loop_start()
        except ModuleNotFoundError:
            self._status = "failed"
            self._reason = "missing_paho_mqtt_dependency"
            return self.status()
        except Exception as exc:
            self._status = "failed"
            self._reason = "mqtt_subscribe_failed"
            log.warning("Weather snapshot MQTT subscriber failed to start: error=%s", exc)
            return self.status()
        self._client = client
        self._running = True
        self._status = "starting"
        self._reason = None
        return self.status()

    def stop(self) -> None:
        client = self._client
        self._client = None
        self._running = False
        self._status = "stopped"
        if client is not None:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception as exc:
                log.warning("Weather snapshot MQTT subscriber failed to stop: error=%s", exc)

    def accept(self, topic: str, event: dict[str, Any], *, received_at: datetime | None = None) -> bool:
        now = received_at or datetime.now(UTC)
        try:
            normalized = validate_weather_snapshot(topic, event, now=now)
            current = self._accepted
            if current is not None:
                current_event = current.get("event") if isinstance(current.get("event"), dict) else {}
                current_data = current_event.get("data") if isinstance(current_event.get("data"), dict) else {}
                new_data = normalized["data"]
                if current_data.get("location_id") == new_data.get("location_id"):
                    current_fetched = parse_timestamp(current_data.get("fetched_at"), "fetched_at")
                    new_fetched = parse_timestamp(new_data.get("fetched_at"), "fetched_at")
                    if new_fetched < current_fetched:
                        raise WeatherSnapshotError("stale_snapshot")
                    if (
                        new_fetched == current_fetched
                        and current_event.get("event_id") == normalized.get("event_id")
                    ):
                        self._reason = "duplicate_snapshot_ignored"
                        return False
            record = {
                "accepted_at": now.isoformat(),
                "topic": topic,
                "event": normalized,
            }
            self._store.save(record)
            self._accepted = record
            self._reason = None
            for listener in tuple(self._listeners):
                try:
                    listener(deepcopy(normalized["data"]))
                except Exception as exc:
                    log.warning("Weather snapshot listener failed: error=%s", exc)
            return True
        except WeatherSnapshotError as exc:
            self._reason = str(exc)
            self._last_rejected = {
                "received_at": now.isoformat(),
                "topic": str(topic),
                "event_id": str(event.get("event_id") or "")[:128],
                "reason": str(exc),
            }
            return False

    def accept_result(self, topic: str, event: dict[str, Any], *, received_at: datetime | None = None) -> bool:
        now = received_at or datetime.now(UTC)
        try:
            normalized = validate_weather_result(topic, event)
            event_id = normalized["event_id"]
            if event_id in self._seen_result_ids:
                self._reason = "duplicate_weather_result_ignored"
                return False
            self._seen_result_ids.insert(0, event_id)
            del self._seen_result_ids[100:]
            self._last_result = {
                "received_at": now.isoformat(),
                "topic": str(topic),
                "event": deepcopy(normalized),
            }
            self._reason = None
            for listener in tuple(self._result_listeners):
                try:
                    listener(deepcopy(normalized))
                except Exception as exc:
                    log.warning("Weather result listener failed: error=%s", exc)
            return True
        except WeatherSnapshotError as exc:
            self._reason = str(exc)
            self._last_rejected = {
                "received_at": now.isoformat(),
                "topic": str(topic),
                "event_id": str(event.get("event_id") or "")[:128],
                "reason": str(exc),
            }
            return False

    def prepared_snapshot(self) -> dict[str, Any] | None:
        if self._accepted is None:
            return None
        event = self._accepted.get("event")
        return deepcopy(event.get("data")) if isinstance(event, dict) and isinstance(event.get("data"), dict) else None

    def status(self) -> dict[str, Any]:
        event = self._accepted.get("event") if isinstance(self._accepted, dict) else None
        data = event.get("data") if isinstance(event, dict) and isinstance(event.get("data"), dict) else None
        tts = data.get("tts") if isinstance(data, dict) and isinstance(data.get("tts"), dict) else {}
        radar = data.get("radar") if isinstance(data, dict) and isinstance(data.get("radar"), dict) else {}
        return {
            "provider": "hexe_mqtt",
            "topics": ["hexe/nodes/+/events/weather/snapshot/updated", WEATHER_PROMOTED_TOPIC, *WEATHER_RESULT_TOPICS],
            "status": self._status,
            "reason": self._reason,
            "last_accepted_event_id": event.get("event_id") if isinstance(event, dict) else None,
            "last_accepted_at": self._accepted.get("accepted_at") if isinstance(self._accepted, dict) else None,
            "source_node_id": event.get("source_node_id") if isinstance(event, dict) else None,
            "snapshot_id": data.get("snapshot_id") if isinstance(data, dict) else None,
            "location_id": data.get("location_id") if isinstance(data, dict) else None,
            "cache_revision": data.get("cache_revision") if isinstance(data, dict) else None,
            "expires_at": data.get("expires_at") if isinstance(data, dict) else None,
            "freshness": deepcopy(data.get("freshness")) if isinstance(data, dict) else None,
            "selected_tts_quality": tts.get("quality"),
            "tts_revision": tts.get("revision"),
            "radar_status": radar.get("status"),
            "fallback_available": data is not None,
            "download_failures": [],
            "last_rejected": deepcopy(self._last_rejected),
            "last_result": deepcopy(self._last_result),
        }

    def _on_connect(self, client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        rc = getattr(reason_code, "value", reason_code)
        try:
            rc_int = int(rc)
        except Exception:
            rc_int = -1
        if rc_int != 0:
            self._status = "failed"
            self._reason = f"connect_rc:{rc_int}"
            return
        client.subscribe("hexe/nodes/+/events/weather/snapshot/updated", qos=1)
        client.subscribe(WEATHER_PROMOTED_TOPIC, qos=1)
        for topic in WEATHER_RESULT_TOPICS:
            client.subscribe(topic, qos=1)
        self._status = "connected"
        self._reason = None

    def _on_disconnect(self, client: Any, userdata: Any, disconnect_flags: Any, reason_code: Any, properties: Any = None) -> None:
        self._status = "disconnected"

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            self._reason = "invalid_json"
            return
        if not isinstance(payload, dict):
            self._reason = "invalid_payload"
            return
        topic = str(msg.topic)
        if WEATHER_RESULT_TOPIC_RE.fullmatch(topic) or WEATHER_PROMOTED_RESULT_TOPIC_RE.fullmatch(topic):
            self.accept_result(topic, payload)
        else:
            self.accept(topic, payload)


def validate_weather_result(topic: str, event: dict[str, Any]) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip()
    node_match = WEATHER_RESULT_TOPIC_RE.fullmatch(normalized_topic)
    promoted_match = WEATHER_PROMOTED_RESULT_TOPIC_RE.fullmatch(normalized_topic)
    if node_match is None and promoted_match is None:
        raise WeatherSnapshotError("unauthorized_weather_result_topic")
    source = event.get("source")
    if not isinstance(source, dict):
        raise WeatherSnapshotError("invalid_weather_result_source")
    if node_match is not None and source.get("node_id") != node_match.group(1):
        raise WeatherSnapshotError("source_topic_mismatch")
    event_type = str(event.get("promoted_event_type") or event.get("event_type") or "")
    topic_suffix = node_match.group(2) if node_match is not None else promoted_match.group(1)
    if event_type not in WEATHER_RESULT_TYPES or event_type.rsplit("_", 1)[-1] != topic_suffix:
        raise WeatherSnapshotError("unsupported_weather_result")
    if promoted_match is not None:
        routing = event.get("routing")
        policy = event.get("policy")
        if not isinstance(routing, dict) or routing.get("domain_topic") != normalized_topic:
            raise WeatherSnapshotError("invalid_core_routing")
        if not isinstance(policy, dict) or policy.get("schema_valid") is not True or policy.get("privacy_valid") is not True:
            raise WeatherSnapshotError("invalid_core_policy")
    if event.get("schema_version") != 1 or source.get("component") != "hexe.weather":
        raise WeatherSnapshotError("unsupported_weather_result_schema")
    event_id = required_string(event, "event_id", max_length=200)
    event_timestamp = event.get("occurred_at")
    if promoted_match is not None:
        event_timestamp = event.get("received_at") or event.get("promoted_at")
    parse_timestamp(event_timestamp, "occurred_at")
    subject = event.get("subject")
    if not isinstance(subject, dict) or subject.get("family") != "weather":
        raise WeatherSnapshotError("invalid_weather_result_subject")
    required_string(subject, "record_id", max_length=200)
    data = event.get("data")
    if not isinstance(data, dict):
        raise WeatherSnapshotError("invalid_weather_result_data")
    for key in ("correlation_id", "endpoint_id", "session_id"):
        required_string(data, key, max_length=200)
    if data.get("intent_id") != "weather.current":
        raise WeatherSnapshotError("invalid_weather_result_intent")
    if event_type == "weather.current_succeeded":
        required_string(data, "snapshot_id", max_length=200)
        required_string(data, "snapshot_revision", max_length=200)
    else:
        required_string(data, "error_code", max_length=120)
        required_string(data, "message", max_length=500)
        if not isinstance(data.get("recoverable"), bool):
            raise WeatherSnapshotError("invalid_weather_result_recoverable")
    return deepcopy(event)


def validate_weather_snapshot(topic: str, event: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip()
    match = WEATHER_TOPIC_RE.fullmatch(normalized_topic)
    if match is None and normalized_topic != WEATHER_PROMOTED_TOPIC:
        raise WeatherSnapshotError("unauthorized_topic")
    source = event.get("source")
    if not isinstance(source, dict):
        raise WeatherSnapshotError("invalid_event_source")
    source_node_id = required_string(source, "node_id", max_length=128)
    if match is not None and source_node_id != match.group(1):
        raise WeatherSnapshotError("source_topic_mismatch")
    event_type = str(event.get("promoted_event_type") or event.get("event_type") or "")
    if event_type != WEATHER_EVENT_TYPE or event.get("schema_version") != 1:
        raise WeatherSnapshotError("unsupported_weather_schema")
    if normalized_topic == WEATHER_PROMOTED_TOPIC:
        routing = event.get("routing")
        policy = event.get("policy")
        if not isinstance(routing, dict) or routing.get("domain_topic") != WEATHER_PROMOTED_TOPIC:
            raise WeatherSnapshotError("invalid_core_routing")
        if not isinstance(policy, dict) or policy.get("schema_valid") is not True or policy.get("privacy_valid") is not True:
            raise WeatherSnapshotError("invalid_core_policy")
    required_string(event, "event_id", max_length=160)
    event_timestamp = event.get("occurred_at")
    if normalized_topic == WEATHER_PROMOTED_TOPIC:
        event_timestamp = event.get("received_at") or event.get("promoted_at")
    parse_timestamp(event_timestamp, "occurred_at")
    data = event.get("data")
    if not isinstance(data, dict) or data.get("schema_version") != WEATHER_SCHEMA_VERSION:
        raise WeatherSnapshotError("invalid_weather_data")
    for key in ("snapshot_id", "location_id", "cache_revision"):
        required_string(data, key, max_length=160)
    if not isinstance(data.get("location"), dict):
        raise WeatherSnapshotError("invalid_location")
    fetched_at = parse_timestamp(data.get("fetched_at"), "fetched_at")
    expires_at = parse_timestamp(data.get("expires_at"), "expires_at")
    if expires_at <= fetched_at:
        raise WeatherSnapshotError("invalid_expiry")
    if fetched_at > now.replace(microsecond=0) and (fetched_at - now).total_seconds() > 300:
        raise WeatherSnapshotError("future_snapshot")
    if not isinstance(data.get("current_conditions"), dict) or not isinstance(data.get("forecast_summary"), dict):
        raise WeatherSnapshotError("invalid_weather_metadata")
    transcript = data.get("transcript")
    if transcript is not None and (not isinstance(transcript, str) or len(transcript) > 4000):
        raise WeatherSnapshotError("invalid_transcript")
    freshness = data.get("freshness")
    if not isinstance(freshness, dict) or freshness.get("weather") not in FRESH_WEATHER_STATES:
        raise WeatherSnapshotError("invalid_freshness")
    tts = validate_component(data.get("tts"), "tts")
    radar = validate_component(data.get("radar"), "radar")
    if freshness.get("tts") != tts["status"] or freshness.get("radar") != radar["status"]:
        raise WeatherSnapshotError("component_freshness_mismatch")
    validate_tts(tts, transcript)
    validate_radar(radar)
    if not isinstance(data.get("attribution"), dict):
        raise WeatherSnapshotError("invalid_attribution")
    normalized = deepcopy(event)
    normalized["source_node_id"] = source_node_id
    return normalized


def validate_component(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("status") not in COMPONENT_STATES:
        raise WeatherSnapshotError(f"invalid_{name}_component")
    sha256 = value.get("sha256")
    if sha256 is not None and (not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None):
        raise WeatherSnapshotError(f"invalid_{name}_sha256")
    return value


def validate_tts(tts: dict[str, Any], transcript: str | None) -> None:
    if tts["status"] != "ready":
        return
    asset_key = required_string(tts, "asset_key", max_length=180)
    if not asset_key.startswith("interaction/") or ".." in asset_key.split("/"):
        raise WeatherSnapshotError("invalid_tts_asset_key")
    required_string(tts, "revision", max_length=160)
    if transcript is not None and tts.get("transcript") != transcript:
        raise WeatherSnapshotError("tts_transcript_mismatch")
    validate_public_url(tts.get("audio_url"), "tts")
    variants = tts.get("variants")
    if not isinstance(variants, dict) or not variants:
        raise WeatherSnapshotError("missing_tts_variants")
    for variant in variants.values():
        if not isinstance(variant, dict) or SHA256_RE.fullmatch(str(variant.get("sha256") or "")) is None:
            raise WeatherSnapshotError("invalid_tts_variant")
        validate_public_url(variant.get("audio_url"), "tts")


def validate_radar(radar: dict[str, Any]) -> None:
    if radar["status"] not in {"ready", "stale"}:
        return
    validate_public_url(radar.get("image_url"), "radar")
    if SHA256_RE.fullmatch(str(radar.get("sha256") or "")) is None:
        raise WeatherSnapshotError("invalid_radar_sha256")
    for key in ("observation_time", "fetched_at", "expires_at"):
        parse_timestamp(radar.get(key), f"radar_{key}")
    if not radar.get("attribution"):
        raise WeatherSnapshotError("missing_radar_attribution")


def validate_public_url(value: Any, component: str) -> None:
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError as exc:
        raise WeatherSnapshotError(f"invalid_{component}_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise WeatherSnapshotError(f"invalid_{component}_url")
    sensitive = {"token", "access_token", "key", "api_key", "secret", "signature"}
    if any(key.casefold() in sensitive for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise WeatherSnapshotError(f"credential_bearing_{component}_url")


def required_string(payload: dict[str, Any], key: str, *, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise WeatherSnapshotError(f"invalid_{key}")
    return value.strip()


def parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise WeatherSnapshotError(f"invalid_{name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WeatherSnapshotError(f"invalid_{name}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
