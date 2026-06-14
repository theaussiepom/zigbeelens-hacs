"""Build the redacted summary payload for the native companion panel.

This is a pure, side-effect-free transform of coordinator data into the small
dict the panel frontend renders. It must never include secrets (MQTT
credentials, passwords, tokens, or raw broker URLs) — only safe summary counts,
states, and the user-configured Core URL.
"""

from __future__ import annotations

from typing import Any

from .coordinator import ZigbeeLensCoordinatorData


def _severity_label(value: Any) -> str:
    """Map Core severity/health values onto the panel's three calm states."""
    text = str(value or "").lower()
    if text in ("healthy", "ok"):
        return "ok"
    if text == "watch":
        return "watch"
    if text in ("incident", "critical"):
        return "incident"
    return "unknown"


def build_panel_summary(
    data: ZigbeeLensCoordinatorData | None,
    *,
    core_url: str,
    connected: bool,
    last_exception: str | None = None,
) -> dict[str, Any]:
    """Return a redacted summary dict for the companion panel frontend."""
    summary: dict[str, Any] = {
        "connected": bool(connected and data is not None),
        "core_url": core_url,
        "core_version": None,
        "overall_health": "unknown",
        "overall_severity": "unknown",
        "current_finding": None,
        "active_incident_count": 0,
        "watching_incident_count": 0,
        "network_count": 0,
        "device_count": 0,
        "unavailable_devices": 0,
        "router_risks": 0,
        "stale_devices": 0,
        "weak_link_devices": 0,
        "low_battery_devices": 0,
        "collector_connected": False,
        "last_update": None,
        "mock_mode": False,
        "networks": [],
        "error": last_exception if not connected else None,
    }

    if data is None:
        return summary

    dashboard = data.dashboard or {}
    health = data.health or {}
    snapshot = dashboard.get("health_snapshot") or {}
    collector = health.get("collector") or {}

    summary["core_version"] = data.core_version or str(health.get("version") or "") or None
    summary["overall_severity"] = _severity_label(dashboard.get("overall_severity"))
    summary["overall_health"] = summary["overall_severity"]

    finding = dashboard.get("current_finding") or {}
    summary["current_finding"] = finding.get("summary")

    summary["active_incident_count"] = int(dashboard.get("active_incident_count") or 0)
    summary["watching_incident_count"] = int(dashboard.get("watching_incident_count") or 0)
    summary["network_count"] = int(
        snapshot.get("network_count") or len(dashboard.get("networks") or [])
    )
    summary["device_count"] = int(snapshot.get("device_count") or 0)
    summary["unavailable_devices"] = int(snapshot.get("unavailable_count") or 0)
    summary["router_risks"] = len(dashboard.get("router_risks") or [])
    summary["stale_devices"] = len(dashboard.get("stale_devices") or [])
    summary["weak_link_devices"] = len(dashboard.get("weak_links") or [])
    summary["low_battery_devices"] = len(dashboard.get("low_batteries") or [])
    summary["collector_connected"] = bool(data.collector_connected)
    summary["mock_mode"] = bool(health.get("mock_mode"))
    summary["last_update"] = dashboard.get("generated_at") or collector.get("last_message_at")

    router_risks = dashboard.get("router_risks") or []
    networks: list[dict[str, Any]] = []
    for net in dashboard.get("networks") or []:
        network_id = net.get("id")
        per_network_router_risks = len(
            [r for r in router_risks if r.get("network_id") == network_id]
        )
        network_health = net.get("health") or {}
        networks.append(
            {
                "id": network_id,
                "name": net.get("name") or network_id,
                "bridge_state": str(net.get("bridge_state") or "unknown"),
                "device_count": int(net.get("device_count") or 0),
                "unavailable_devices": int(net.get("unavailable_count") or 0),
                "router_risks": per_network_router_risks,
                "health": _severity_label(
                    net.get("incident_state") or network_health.get("severity")
                ),
            }
        )
    summary["networks"] = networks
    return summary
