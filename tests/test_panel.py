"""Native companion panel registration and websocket summary tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zigbeelens.const import DOMAIN, PANEL_STATE_KEY
from zigbeelens.panel import (
    PANEL_WEBCOMPONENT,
    _find_coordinator,
    _ws_panel_summary,
    async_register_panel,
    async_unregister_panel,
)


@pytest.mark.asyncio
async def test_panel_registered_as_native_custom_panel():
    hass = MagicMock()
    hass.data = {"zigbeelens": {}}
    hass.http.async_register_static_paths = AsyncMock()
    with (
        patch("zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()) as register,
        patch("zigbeelens.panel.websocket_api.async_register_command") as ws_register,
    ):
        await async_register_panel(hass, "entry1", "http://192.168.100.5:8377")

    register.assert_awaited_once()
    _args, kwargs = register.call_args
    assert kwargs["webcomponent_name"] == PANEL_WEBCOMPONENT
    assert kwargs["embed_iframe"] is False
    assert kwargs["frontend_url_path"] == "zigbeelens"
    assert kwargs["module_url"].endswith(".js")
    assert kwargs["config"] == {"core_url": "http://192.168.100.5:8377"}
    assert "api_token" not in kwargs["config"]
    assert "Authorization" not in json.dumps(kwargs["config"])
    ws_register.assert_called_once()
    hass.http.async_register_static_paths.assert_awaited_once()
    assert hass.data["zigbeelens"]["_panel_state"]["panel_registered"] is True
    assert hass.data["zigbeelens"]["_panel_state"]["owner_entry_id"] == "entry1"


@pytest.mark.asyncio
async def test_panel_registration_never_includes_token_sentinel():
    sentinel = "zl-hacs-panel-sentinel-token-aaaaaa"
    hass = MagicMock()
    hass.data = {"zigbeelens": {}}
    hass.http.async_register_static_paths = AsyncMock()
    with (
        patch("zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()) as register,
        patch("zigbeelens.panel.websocket_api.async_register_command"),
    ):
        await async_register_panel(hass, "entry1", "http://192.168.100.5:8377")
    blob = json.dumps(register.call_args.kwargs)
    assert sentinel not in blob
    assert register.call_args.kwargs["config"] == {
        "core_url": "http://192.168.100.5:8377"
    }


@pytest.mark.asyncio
async def test_panel_not_registered_twice_when_core_url_unchanged():
    from homeassistant.components import frontend

    hass = MagicMock()
    hass.data = {
        "zigbeelens": {"_panel_state": {"panel_registered": True}},
        frontend.DATA_PANELS: {"zigbeelens": {"config": {"core_url": "http://old"}}},
    }
    hass.http.async_register_static_paths = AsyncMock()
    with patch(
        "zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()
    ) as register:
        await async_register_panel(hass, "entry1", "http://old")
        register.assert_not_awaited()
    assert (
        hass.data[frontend.DATA_PANELS]["zigbeelens"]["config"]["core_url"]
        == "http://old"
    )
    assert hass.data["zigbeelens"]["_panel_state"]["owner_entry_id"] == "entry1"


@pytest.mark.asyncio
async def test_panel_reregisters_when_core_url_changes():
    from homeassistant.components import frontend

    hass = MagicMock()
    hass.data = {
        "zigbeelens": {"_panel_state": {"panel_registered": True}},
        frontend.DATA_PANELS: {"zigbeelens": {"config": {"core_url": "http://old"}}},
    }
    hass.http.async_register_static_paths = AsyncMock()
    with patch(
        "zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()
    ) as register, patch("zigbeelens.panel.frontend.async_remove_panel") as remove:
        await async_register_panel(hass, "entry1", "http://localhost:8377")
        remove.assert_called_once()
        register.assert_awaited_once()


@pytest.mark.asyncio
async def test_panel_removed_on_unload():
    hass = MagicMock()
    hass.data = {
        "zigbeelens": {"_panel_state": {"panel_registered": True}},
        "frontend_panels": {"zigbeelens": object()},
    }
    with patch("zigbeelens.panel.frontend.async_remove_panel") as remove:
        await async_unregister_panel(hass, "entry1")
        remove.assert_called_once()
        assert hass.data["zigbeelens"]["_panel_state"]["panel_registered"] is False


def test_ws_panel_summary_returns_connected_payload(mock_coordinator):
    hass = MagicMock()
    hass.data = {
        "zigbeelens": {
            "entry1": {"coordinator": mock_coordinator, "client": mock_coordinator.client}
        }
    }
    connection = MagicMock()

    _ws_panel_summary(hass, connection, {"id": 7})

    connection.send_result.assert_called_once()
    msg_id, payload = connection.send_result.call_args[0]
    assert msg_id == 7
    assert payload["connected"] is True
    assert payload["core_url"] == "http://localhost:8377"
    assert payload["active_incident_count"] == 1
    assert payload["device_count"] == 10
    assert payload["networks"][0]["name"] == "Home"
    assert "shared_decisions_available" in payload
    assert "decision_contract_version" in payload
    assert "core_version_compatible" in payload
    assert "capabilities" not in payload
    assert "score" not in json.dumps(payload)
    assert "action_group" not in json.dumps(payload)
    assert "card_type" not in json.dumps(payload)
    assert "device_ieees" not in json.dumps(payload)
    # Must not leak broker URL / credentials.
    serialized = json.dumps(payload).lower()
    assert "mqtt_server" not in payload
    assert "password" not in serialized
    assert "broker" not in serialized


def test_ws_panel_summary_disconnected_is_calm():
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.data = None
    coordinator.last_exception = "Cannot connect to ZigbeeLens Core"
    client = MagicMock(core_url="http://192.168.100.5:8377")
    hass.data = {"zigbeelens": {"e": {"coordinator": coordinator, "client": client}}}
    connection = MagicMock()

    _ws_panel_summary(hass, connection, {"id": 1})

    _id, payload = connection.send_result.call_args[0]
    assert payload["connected"] is False
    assert payload["core_url"] == "http://192.168.100.5:8377"
    assert payload["error"] == "Cannot connect to ZigbeeLens Core"
    assert payload["networks"] == []
    assert payload["core_version_compatible"] is None
    assert payload["shared_decisions_available"] is False


@pytest.mark.asyncio
async def test_panel_reregister_when_flag_set_but_panel_missing():
    from homeassistant.components import frontend

    hass = MagicMock()
    hass.data = {
        "zigbeelens": {"_panel_state": {"panel_registered": True}},
        frontend.DATA_PANELS: {},
    }
    hass.http.async_register_static_paths = AsyncMock()
    with patch(
        "zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()
    ) as register:
        await async_register_panel(hass, "entry1", "http://localhost:8377")
        register.assert_awaited_once()
    assert hass.data["zigbeelens"]["_panel_state"]["panel_registered"] is True


def test_ws_panel_summary_handles_bad_dashboard_data():
    hass = MagicMock()
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = MagicMock()
    coordinator.data.dashboard = "bad-shape"
    coordinator.data.health = {}
    coordinator.data.core_version = None
    coordinator.data.collector_connected = False
    coordinator.last_exception = None
    client = MagicMock(core_url="http://192.168.100.5:8377")
    hass.data = {"zigbeelens": {"e": {"coordinator": coordinator, "client": client}}}
    connection = MagicMock()

    _ws_panel_summary(hass, connection, {"id": 9})

    _id, payload = connection.send_result.call_args[0]
    assert payload["connected"] is False
    assert payload["error"] == "Panel summary unavailable"


def test_ws_panel_summary_without_coordinator():
    hass = MagicMock()
    hass.data = {"zigbeelens": {}}
    connection = MagicMock()

    _ws_panel_summary(hass, connection, {"id": 3})

    _id, payload = connection.send_result.call_args[0]
    assert payload["connected"] is False
    assert payload["networks"] == []


def _runtime(core_url: str, *, connected: bool = True) -> dict:
    coordinator = MagicMock()
    coordinator.last_update_success = connected
    coordinator.data = {"dashboard": {}} if connected else None
    coordinator.last_exception = None
    client = MagicMock(core_url=core_url)
    return {"coordinator": coordinator, "client": client}


def test_find_coordinator_prefers_owner_when_secondary_inserted_first():
    """Insertion order must not change primary panel ownership."""
    hass = MagicMock()
    secondary = _runtime("http://secondary:8377")
    primary = _runtime("http://primary:8377")
    # Dict insertion order: secondary first, then primary.
    hass.data = {
        DOMAIN: {
            "entry_secondary": secondary,
            "entry_primary": primary,
            PANEL_STATE_KEY: {
                "panel_registered": True,
                "owner_entry_id": "entry_primary",
            },
        }
    }
    coordinator, core_url = _find_coordinator(hass)
    assert coordinator is primary["coordinator"]
    assert core_url == "http://primary:8377"


def test_find_coordinator_prefers_owner_when_primary_inserted_first():
    hass = MagicMock()
    primary = _runtime("http://primary:8377")
    secondary = _runtime("http://secondary:8377")
    hass.data = {
        DOMAIN: {
            "entry_primary": primary,
            "entry_secondary": secondary,
            PANEL_STATE_KEY: {
                "panel_registered": True,
                "owner_entry_id": "entry_primary",
            },
        }
    }
    coordinator, core_url = _find_coordinator(hass)
    assert coordinator is primary["coordinator"]
    assert core_url == "http://primary:8377"


def test_find_coordinator_missing_owner_does_not_fallback_to_secondary():
    hass = MagicMock()
    secondary = _runtime("http://secondary:8377")
    hass.data = {
        DOMAIN: {
            "entry_secondary": secondary,
            PANEL_STATE_KEY: {
                "panel_registered": True,
                "owner_entry_id": "entry_primary",
            },
        }
    }
    connection = MagicMock()
    coordinator, core_url = _find_coordinator(hass)
    assert coordinator is None
    assert core_url == ""

    _ws_panel_summary(hass, connection, {"id": 11})
    _id, payload = connection.send_result.call_args[0]
    assert payload["connected"] is False
    assert payload["networks"] == []
    assert payload.get("core_url") in ("", None)
    blob = json.dumps(payload)
    assert "secondary" not in blob
    assert "owner_entry_id" not in blob
    assert "entry_primary" not in blob


@pytest.mark.asyncio
async def test_secondary_unregister_does_not_remove_primary_panel():
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            PANEL_STATE_KEY: {
                "panel_registered": True,
                "owner_entry_id": "entry_primary",
            },
        },
        "frontend_panels": {"zigbeelens": object()},
    }
    with patch("zigbeelens.panel.frontend.async_remove_panel") as remove:
        await async_unregister_panel(hass, "entry_secondary")
    remove.assert_not_called()
    assert hass.data[DOMAIN][PANEL_STATE_KEY]["panel_registered"] is True
    assert hass.data[DOMAIN][PANEL_STATE_KEY]["owner_entry_id"] == "entry_primary"


@pytest.mark.asyncio
async def test_primary_unregister_removes_panel_and_clears_owner():
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            PANEL_STATE_KEY: {
                "panel_registered": True,
                "owner_entry_id": "entry_primary",
            },
        },
        "frontend_panels": {"zigbeelens": object()},
    }
    with patch("zigbeelens.panel.frontend.async_remove_panel") as remove:
        await async_unregister_panel(hass, "entry_primary")
    remove.assert_called_once()
    assert hass.data[DOMAIN][PANEL_STATE_KEY]["panel_registered"] is False
    assert "owner_entry_id" not in hass.data[DOMAIN][PANEL_STATE_KEY]


@pytest.mark.asyncio
async def test_existing_valid_panel_reuse_stamps_owner():
    from homeassistant.components import frontend

    hass = MagicMock()
    hass.data = {
        DOMAIN: {PANEL_STATE_KEY: {"panel_registered": True}},
        frontend.DATA_PANELS: {
            "zigbeelens": {"config": {"core_url": "http://localhost:8377"}}
        },
    }
    hass.http.async_register_static_paths = AsyncMock()
    with patch("zigbeelens.panel.panel_custom.async_register_panel", new=AsyncMock()) as register:
        await async_register_panel(hass, "entry_primary", "http://localhost:8377")
    register.assert_not_awaited()
    assert hass.data[DOMAIN][PANEL_STATE_KEY]["panel_registered"] is True
    assert hass.data[DOMAIN][PANEL_STATE_KEY]["owner_entry_id"] == "entry_primary"


def test_single_runtime_without_owner_marker_still_resolves():
    hass = MagicMock()
    sole = _runtime("http://localhost:8377")
    hass.data = {DOMAIN: {"entry1": sole}}
    coordinator, core_url = _find_coordinator(hass)
    assert coordinator is sole["coordinator"]
    assert core_url == "http://localhost:8377"


def test_multiple_runtimes_without_owner_marker_fail_closed():
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            "entry_a": _runtime("http://a:8377"),
            "entry_b": _runtime("http://b:8377"),
        }
    }
    coordinator, core_url = _find_coordinator(hass)
    assert coordinator is None
    assert core_url == ""
