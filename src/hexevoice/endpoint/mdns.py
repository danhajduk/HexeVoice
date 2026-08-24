from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse

from hexevoice.config.settings import Settings


log = logging.getLogger("hexevoice")


@dataclass(frozen=True)
class EndpointMdnsMetadata:
    service_name: str
    service_type: str
    advertised_ip: str
    api_port: int
    ui_port: int
    api_url: str
    ui_url: str
    node_id: str
    node_type: str
    tls: bool

    def txt_properties(self) -> dict[str, str]:
        return {
            "api_url": self.api_url,
            "ui_url": self.ui_url,
            "api_port": str(self.api_port),
            "ui_port": str(self.ui_port),
            "node_id": self.node_id,
            "node_type": self.node_type,
            "tls": "true" if self.tls else "false",
            "advertised_ip": self.advertised_ip,
        }


def build_endpoint_mdns_metadata(
    settings: Settings,
    *,
    advertised_ip: str | None = None,
    node_id: str | None = None,
) -> EndpointMdnsMetadata:
    lan_ip = advertised_ip or resolve_advertised_lan_ip(settings)
    if lan_ip is None:
        raise ValueError("advertised_lan_ip_unavailable")
    if not _is_usable_ip(lan_ip):
        raise ValueError("invalid_advertised_lan_ip")

    scheme = "https" if settings.endpoint_discovery_use_tls else "http"
    api_port = _port_from_url(settings.public_api_base_url, default=settings.api_port)
    ui_port = _port_from_url(settings.public_ui_base_url, default=8084)
    api_url = _ip_url(settings.public_api_base_url, scheme=scheme, ip=lan_ip, port=api_port)
    ui_url = _ip_url(settings.public_ui_base_url, scheme=scheme, ip=lan_ip, port=ui_port)
    return EndpointMdnsMetadata(
        service_name=_clean_service_instance(settings.endpoint_mdns_service_name),
        service_type=_normalize_service_type(settings.endpoint_mdns_service_type),
        advertised_ip=lan_ip,
        api_port=api_port,
        ui_port=ui_port,
        api_url=api_url,
        ui_url=ui_url,
        node_id=(node_id or settings.node_name).strip() or "hexevoice",
        node_type=settings.node_type,
        tls=settings.endpoint_discovery_use_tls,
    )


def resolve_advertised_lan_ip(settings: Settings) -> str | None:
    for candidate in (
        settings.endpoint_mdns_advertise_host,
        settings.endpoint_discovery_advertise_host,
        _hostname_from_url(settings.public_api_base_url),
    ):
        if candidate and _is_usable_ip(candidate):
            return candidate

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            detected = sock.getsockname()[0]
            if _is_usable_ip(detected):
                return detected
    except OSError:
        pass

    try:
        detected = socket.gethostbyname(socket.gethostname())
        if _is_usable_ip(detected):
            return detected
    except OSError:
        pass
    return None


class EndpointMdnsAdvertiser:
    def __init__(self, *, settings: Settings, node_id_provider=None) -> None:
        self._settings = settings
        self._node_id_provider = node_id_provider
        self._zeroconf = None
        self._service_info = None
        self._metadata: EndpointMdnsMetadata | None = None
        self._last_error: str | None = None
        self._active = False

    def start(self) -> None:
        if not self._settings.endpoint_mdns_enabled:
            self._last_error = None
            return
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            self._last_error = "zeroconf_not_installed"
            log.warning("Endpoint mDNS advertiser disabled because zeroconf is not installed")
            return

        try:
            node_id = self._node_id_provider() if self._node_id_provider is not None else None
            metadata = build_endpoint_mdns_metadata(self._settings, node_id=node_id)
            service_type = metadata.service_type
            service_name = f"{metadata.service_name}.{service_type}"
            properties = metadata.txt_properties()
            self._zeroconf = Zeroconf()
            self._service_info = ServiceInfo(
                service_type,
                service_name,
                addresses=[socket.inet_aton(metadata.advertised_ip)],
                port=metadata.api_port,
                properties=properties,
                server=f"{metadata.service_name.lower()}.local.",
            )
            self._zeroconf.register_service(self._service_info)
            self._metadata = metadata
            self._last_error = None
            self._active = True
            log.info("Endpoint mDNS advertiser started for %s", metadata.api_url)
        except Exception as exc:
            self._last_error = exc.__class__.__name__
            self._active = False
            log.warning("Endpoint mDNS advertiser could not start", exc_info=True)
            self.stop()

    def stop(self) -> None:
        if self._zeroconf is not None and self._service_info is not None:
            try:
                self._zeroconf.unregister_service(self._service_info)
            except Exception:
                log.debug("Endpoint mDNS service unregister failed", exc_info=True)
        if self._zeroconf is not None:
            try:
                self._zeroconf.close()
            except Exception:
                log.debug("Endpoint mDNS zeroconf close failed", exc_info=True)
        self._zeroconf = None
        self._service_info = None
        self._active = False

    def status(self) -> dict[str, object]:
        metadata = self._metadata
        return {
            "enabled": self._settings.endpoint_mdns_enabled,
            "active": self._active,
            "status": "active" if self._active else ("error" if self._last_error else "disabled"),
            "last_error": self._last_error,
            "service_name": metadata.service_name if metadata else self._settings.endpoint_mdns_service_name,
            "service_type": metadata.service_type if metadata else _normalize_service_type(self._settings.endpoint_mdns_service_type),
            "advertised_ip": metadata.advertised_ip if metadata else None,
            "api_url": metadata.api_url if metadata else None,
            "ui_url": metadata.ui_url if metadata else None,
            "api_port": metadata.api_port if metadata else self._settings.api_port,
            "ui_port": metadata.ui_port if metadata else _port_from_url(self._settings.public_ui_base_url, default=8084),
        }


def _hostname_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).hostname


def _port_from_url(url: str | None, *, default: int) -> int:
    if not url:
        return default
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    if parsed.scheme == "https":
        return 443
    if parsed.scheme == "http":
        return 80
    return default


def _ip_url(configured_url: str | None, *, scheme: str, ip: str, port: int) -> str:
    path = ""
    if configured_url:
        parsed = urlparse(configured_url)
        path = parsed.path if parsed.path not in {"", "/"} else ""
    return f"{scheme}://{ip}:{port}{path}"


def _is_usable_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.version == 4
        and not ip.is_unspecified
        and not ip.is_loopback
        and not ip.is_multicast
        and not ip.is_reserved
    )


def _clean_service_instance(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return cleaned or "HexeVoice"


def _normalize_service_type(value: str) -> str:
    service_type = value.strip() or "_hexevoice._tcp.local."
    return service_type if service_type.endswith(".") else f"{service_type}."
