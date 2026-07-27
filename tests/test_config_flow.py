"""Config flow tests — user, reauth, reconfigure, and options."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import TextSelector

from zigbeelens import async_reload_entry, async_setup_entry
from zigbeelens.config_flow import (
    ZigbeeLensConfigFlow,
    ZigbeeLensOptionsFlow,
    _normalize_core_url,
    _user_schema,
)
from zigbeelens.const import (
    CONF_API_TOKEN,
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_REMOVE_API_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from zigbeelens.exceptions import (
    ZigbeeLensAuthError,
    ZigbeeLensConnectionError,
    ZigbeeLensInvalidResponseError,
)
from zigbeelens.panel import PANEL_URL_PATH, async_update_panel_core_url

VALID_TOKEN = "a" * 32
SENTINEL = "zl-hacs-flow-sentinel-token-aaaaaa"


def _flow(
    *,
    existing_entries: list[object] | None = None,
    matching_flow: bool = False,
    hass: MagicMock | None = None,
) -> ZigbeeLensConfigFlow:
    flow = ZigbeeLensConfigFlow()
    flow.hass = hass or MagicMock()
    flow.hass.config_entries.flow.async_has_matching_flow = MagicMock(
        return_value=matching_flow
    )
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow._async_current_entries = MagicMock(return_value=list(existing_entries or []))
    flow.context = {"source": "user"}
    return flow


def test_normalize_core_url():
    assert _normalize_core_url("http://localhost:8377/") == "http://localhost:8377"


def test_normalize_core_url_invalid():
    with pytest.raises(ValueError):
        _normalize_core_url("not-a-url")


def test_user_schema_uses_password_selector_without_default_token():
    schema = _user_schema()
    selectors = [v for v in schema.schema.values() if isinstance(v, TextSelector)]
    assert selectors
    assert SENTINEL not in str(schema)


@pytest.mark.asyncio
async def test_config_flow_trusted_open_blank_token():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "",
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_API_TOKEN] == ""
    assert CONF_PANEL_ENABLED not in result["data"]
    assert result["options"][CONF_PANEL_ENABLED] is True
    assert result["options"][CONF_SCAN_INTERVAL] == 60
    validate.assert_awaited_once()
    assert validate.await_args.args[3] == ""


@pytest.mark.asyncio
async def test_second_entry_same_url_rejected_without_http():
    existing = MagicMock()
    existing.data = {CONF_CORE_URL: "http://localhost:8377"}
    flow = _flow(existing_entries=[existing])
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"
    validate.assert_not_awaited()
    assert VALID_TOKEN not in str(result)
    assert "localhost" not in str(result.get("reason", ""))


@pytest.mark.asyncio
async def test_second_entry_different_url_rejected_without_http():
    existing = MagicMock()
    existing.data = {CONF_CORE_URL: "http://core-a:8377"}
    flow = _flow(existing_entries=[existing])
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://core-b:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "abort"
    assert result["reason"] == "single_instance_allowed"
    validate.assert_not_awaited()
    assert VALID_TOKEN not in str(result)
    assert "core-b" not in str(result)


@pytest.mark.asyncio
async def test_concurrent_user_flows_different_urls_rejected_without_http():
    """Two simultaneous user flows: only one may validate/create."""
    shared_hass = MagicMock()
    flow_a = _flow(hass=shared_hass, matching_flow=False)
    flow_b = _flow(hass=shared_hass, matching_flow=False)
    assert flow_a.is_matching(flow_b)

    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate_a:
        result_a = await flow_a.async_step_user(
            {
                CONF_CORE_URL: "http://core-a:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result_a["type"] == "create_entry"
    validate_a.assert_awaited_once()

    # After A is in progress / created, B sees a matching flow and must abort
    # before Core HTTP validation.
    shared_hass.config_entries.flow.async_has_matching_flow = MagicMock(return_value=True)
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate_b:
        result_b = await flow_b.async_step_user(
            {
                CONF_CORE_URL: "http://core-b:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result_b["type"] == "abort"
    assert result_b["reason"] == "single_instance_allowed"
    validate_b.assert_not_awaited()
    assert VALID_TOKEN not in str(result_b)
    assert "core-b" not in str(result_b)
    assert "core-a" not in str(result_b)


@pytest.mark.asyncio
async def test_config_flow_protected_correct_token():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_API_TOKEN] == VALID_TOKEN
    assert validate.await_args.args[3] == VALID_TOKEN


@pytest.mark.asyncio
async def test_config_flow_protected_blank_token_invalid_auth():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(side_effect=ZigbeeLensAuthError("Authentication required")),
    ):
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "",
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"
    assert SENTINEL not in str(result)


@pytest.mark.asyncio
async def test_config_flow_malformed_token_zero_http():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "too-short",
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"
    validate.assert_not_awaited()
    assert "too-short" not in str(result)


@pytest.mark.asyncio
async def test_config_flow_cannot_connect():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(side_effect=ZigbeeLensConnectionError("down")),
    ):
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "",
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_invalid_response():
    flow = _flow()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(side_effect=ZigbeeLensInvalidResponseError("bad")),
    ):
        result = await flow.async_step_user(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: VALID_TOKEN,
                CONF_VERIFY_SSL: False,
                CONF_PANEL_ENABLED: True,
            }
        )
    assert result["errors"]["base"] == "invalid_response"


@pytest.mark.asyncio
async def test_reauth_replaces_token_and_reloads_once():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_PANEL_ENABLED: True,
        CONF_API_TOKEN: "old" + ("x" * 29),
    }
    flow._get_reauth_entry = MagicMock(return_value=entry)
    entry.update_listeners = (MagicMock(),)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=True)

    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: VALID_TOKEN})

    assert result["reason"] == "reauth_successful"
    flow.hass.config_entries.async_update_entry.assert_called_once()
    kwargs = flow.hass.config_entries.async_update_entry.call_args.kwargs
    assert kwargs["data"][CONF_API_TOKEN] == VALID_TOKEN
    assert validate.await_args.args[3] == VALID_TOKEN
    # Old token retained until success — entry data not mutated before helper.
    assert entry.data[CONF_API_TOKEN].startswith("old")
    flow.hass.config_entries.async_schedule_reload.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_same_token_schedules_one_fallback_reload():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.update_listeners = (MagicMock(),)
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: VALID_TOKEN,
    }
    flow._get_reauth_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=False)

    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: VALID_TOKEN})

    assert result["reason"] == "reauth_successful"
    flow.hass.config_entries.async_update_entry.assert_called_once()
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with("entry1")


@pytest.mark.asyncio
async def test_reauth_changed_token_without_setup_listener_schedules_one_reload():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.update_listeners = ()
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: "old" + ("x" * 29),
    }
    flow._get_reauth_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=True)

    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: VALID_TOKEN})

    assert result["reason"] == "reauth_successful"
    flow.hass.config_entries.async_update_entry.assert_called_once()
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with("entry1")


@pytest.mark.asyncio
async def test_reauth_blank_clears_token_for_trusted_open():
    flow = _flow()
    entry = MagicMock()
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: VALID_TOKEN,
    }
    flow._get_reauth_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=True)
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: ""})
    assert result["reason"] == "reauth_successful"
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][
            CONF_API_TOKEN
        ]
        == ""
    )


@pytest.mark.asyncio
async def test_reauth_failed_validation_keeps_old_token():
    flow = _flow()
    old = "old" + ("y" * 29)
    entry = MagicMock()
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: old,
    }
    flow._get_reauth_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock()
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(side_effect=ZigbeeLensAuthError("Authentication required")),
    ):
        result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: VALID_TOKEN})
    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"
    flow.hass.config_entries.async_update_entry.assert_not_called()
    assert entry.data[CONF_API_TOKEN] == old


@pytest.mark.asyncio
async def test_reconfigure_keeps_token_when_blank_and_not_removed():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.unique_id = "http://localhost:8377"
    entry.update_listeners = (MagicMock(),)
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_PANEL_ENABLED: True,
        CONF_API_TOKEN: VALID_TOKEN,
    }
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=False)
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ) as validate:
        result = await flow.async_step_reconfigure(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "",
                CONF_REMOVE_API_TOKEN: False,
                CONF_VERIFY_SSL: False,
            }
        )
    assert result["reason"] == "reconfigure_successful"
    assert validate.await_args.args[3] == VALID_TOKEN
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][
            CONF_API_TOKEN
        ]
        == VALID_TOKEN
    )
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with("entry1")


@pytest.mark.asyncio
async def test_reconfigure_remove_token():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.update_listeners = ()
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: VALID_TOKEN,
    }
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock(return_value=True)
    with patch(
        "zigbeelens.config_flow._validate_core",
        new=AsyncMock(return_value={"status": "ok"}),
    ):
        result = await flow.async_step_reconfigure(
            {
                CONF_CORE_URL: "http://localhost:8377",
                CONF_API_TOKEN: "",
                CONF_REMOVE_API_TOKEN: True,
                CONF_VERIFY_SSL: False,
            }
    )
    assert result["reason"] == "reconfigure_successful"
    assert (
        flow.hass.config_entries.async_update_entry.call_args.kwargs["data"][
            CONF_API_TOKEN
        ]
        == ""
    )
    flow.hass.config_entries.async_schedule_reload.assert_called_once_with("entry1")


@pytest.mark.asyncio
async def test_reconfigure_token_and_remove_conflict():
    flow = _flow()
    entry = MagicMock()
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: VALID_TOKEN,
    }
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_update_entry = MagicMock()
    result = await flow.async_step_reconfigure(
        {
            CONF_CORE_URL: "http://localhost:8377",
            CONF_API_TOKEN: VALID_TOKEN,
            CONF_REMOVE_API_TOKEN: True,
            CONF_VERIFY_SSL: False,
        }
    )
    assert result["errors"]["base"] == "token_conflict"
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_reconfigure_duplicate_core_url_rejected():
    flow = _flow()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {
        CONF_CORE_URL: "http://localhost:8377",
        CONF_VERIFY_SSL: False,
        CONF_API_TOKEN: "",
    }
    other = MagicMock()
    other.entry_id = "entry2"
    flow._get_reconfigure_entry = MagicMock(return_value=entry)
    flow.hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=other)
    result = await flow.async_step_reconfigure(
        {
            CONF_CORE_URL: "http://192.168.1.10:8377",
            CONF_API_TOKEN: "",
            CONF_REMOVE_API_TOKEN: False,
            CONF_VERIFY_SSL: False,
        }
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_options_flow_returns_panel_and_scan_without_manual_reload():
    hass = MagicMock(spec=HomeAssistant)
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    hass.config_entries.async_get_known_entry = MagicMock()

    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {
        CONF_CORE_URL: "http://192.168.100.5:8377",
        CONF_VERIFY_SSL: False,
        CONF_PANEL_ENABLED: True,
        CONF_API_TOKEN: SENTINEL,
    }
    entry.options = {CONF_SCAN_INTERVAL: 60}
    hass.config_entries.async_get_known_entry.return_value = entry

    flow = ZigbeeLensOptionsFlow()
    flow.hass = hass
    flow.handler = "entry1"

    result = await flow.async_step_init(
        {
            CONF_PANEL_ENABLED: False,
            CONF_SCAN_INTERVAL: 90,
        }
    )

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_PANEL_ENABLED: False,
        CONF_SCAN_INTERVAL: 90,
    }
    hass.config_entries.async_update_entry.assert_not_called()
    hass.config_entries.async_reload.assert_not_awaited()
    assert entry.data[CONF_CORE_URL] == "http://192.168.100.5:8377"
    assert entry.data[CONF_API_TOKEN] == SENTINEL


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [15, 60, 900])
async def test_options_flow_accepts_exact_interval_boundaries(interval: int):
    hass = MagicMock(spec=HomeAssistant)
    hass.config_entries = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_PANEL_ENABLED: True}
    entry.options = {
        CONF_PANEL_ENABLED: False,
        CONF_SCAN_INTERVAL: 60,
    }
    hass.config_entries.async_get_known_entry.return_value = entry
    flow = ZigbeeLensOptionsFlow()
    flow.hass = hass
    flow.handler = "entry1"

    result = await flow.async_step_init(
        {CONF_PANEL_ENABLED: True, CONF_SCAN_INTERVAL: interval}
    )

    assert result["data"][CONF_SCAN_INTERVAL] == interval


@pytest.mark.asyncio
async def test_options_flow_reopens_with_persisted_values_and_rejects_boundaries():
    hass = MagicMock(spec=HomeAssistant)
    hass.config_entries = MagicMock()
    entry = MagicMock()
    entry.data = {CONF_PANEL_ENABLED: True}
    entry.options = {
        CONF_PANEL_ENABLED: False,
        CONF_SCAN_INTERVAL: 900,
    }
    hass.config_entries.async_get_known_entry.return_value = entry
    flow = ZigbeeLensOptionsFlow()
    flow.hass = hass
    flow.handler = "entry1"

    result = await flow.async_step_init()
    schema = result["data_schema"]
    assert schema({}) == {
        CONF_PANEL_ENABLED: False,
        CONF_SCAN_INTERVAL: 900,
    }
    with pytest.raises(vol.Invalid):
        schema({CONF_PANEL_ENABLED: True, CONF_SCAN_INTERVAL: 14})
    with pytest.raises(vol.Invalid):
        schema({CONF_PANEL_ENABLED: True, CONF_SCAN_INTERVAL: 901})


def _manager_test_entry() -> ConfigEntry:
    """Build a real ConfigEntry across the exact minimum/current HA lanes."""
    kwargs = {
        "version": 2,
        "minor_version": 1,
        "domain": DOMAIN,
        "title": "ZigbeeLens",
        "data": {
            CONF_CORE_URL: "http://127.0.0.1:8377",
            CONF_VERIFY_SSL: False,
            CONF_API_TOKEN: "",
        },
        "options": {
            CONF_PANEL_ENABLED: True,
            CONF_SCAN_INTERVAL: 60,
        },
        "source": "user",
        "unique_id": DOMAIN,
        "entry_id": "entry1",
        "discovery_keys": {},
        "subentries_data": [],
    }
    parameters = inspect.signature(ConfigEntry).parameters
    return ConfigEntry(**{key: value for key, value in kwargs.items() if key in parameters})


@pytest.mark.asyncio
async def test_options_manager_persists_reloads_once_and_drives_setup(tmp_path):
    """Exercise HA's manager, not the OptionsFlow step in isolation."""
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    entry = _manager_test_entry()
    hass.config_entries._entries[entry.entry_id] = entry
    unsubscribe = entry.add_update_listener(async_reload_entry)

    with patch(
        "homeassistant.config_entries._async_get_flow_handler",
        new=AsyncMock(return_value=ZigbeeLensConfigFlow),
    ), patch.object(
        hass.config_entries,
        "async_reload",
        new=AsyncMock(),
    ) as reload_entry:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        saved = await hass.config_entries.options.async_configure(
            form["flow_id"],
            user_input={
                CONF_PANEL_ENABLED: False,
                CONF_SCAN_INTERVAL: 90,
            },
        )
        await hass.async_block_till_done()

        assert saved["type"] == "create_entry"
        assert dict(entry.options) == {
            CONF_PANEL_ENABLED: False,
            CONF_SCAN_INTERVAL: 90,
        }
        reload_entry.assert_awaited_once_with(entry.entry_id)

        reopened = await hass.config_entries.options.async_init(entry.entry_id)
        assert reopened["data_schema"]({}) == {
            CONF_PANEL_ENABLED: False,
            CONF_SCAN_INTERVAL: 90,
        }
        await hass.config_entries.options.async_configure(
            reopened["flow_id"],
            user_input=dict(entry.options),
        )
        await hass.async_block_till_done()
        assert reload_entry.await_count == 1

    unsubscribe()
    client = MagicMock(core_url="http://127.0.0.1:8377")
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = MagicMock()
    manager.async_start = AsyncMock()
    manager.async_request_sync = MagicMock()
    manager.diagnostics = {"sync_state": "never_attempted"}

    with patch(
        "zigbeelens.async_get_clientsession", return_value=MagicMock()
    ), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ) as coordinator_class, patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register_panel, patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister_panel, patch(
        "zigbeelens.async_manage_repairs"
    ), patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        new=AsyncMock(return_value=True),
    ):
        assert await async_setup_entry(hass, entry) is True

    assert coordinator_class.call_args.args[2] == 90
    register_panel.assert_not_awaited()
    unregister_panel.assert_awaited_once_with(hass, entry.entry_id)
    await hass.async_stop()


def test_translation_strings_mention_https_embed_guidance():
    strings = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "custom_components/zigbeelens/strings.json"
        ).read_text(encoding="utf-8")
    )
    user_desc = strings["config"]["step"]["user"]["description"]
    assert "HTTP is fine" in user_desc
    assert "embedded dashboard view" in user_desc
    assert "api_token" in strings["config"]["step"]["user"]["data"]
    assert "invalid_auth" in strings["config"]["error"]
    assert "reauth_confirm" in strings["config"]["step"]
    assert "reconfigure" in strings["config"]["step"]
    assert SENTINEL not in json.dumps(strings)


def test_update_panel_core_url():
    hass = MagicMock()
    hass.data = {frontend.DATA_PANELS: {PANEL_URL_PATH: {"config": {"core_url": "http://old"}}}}
    async_update_panel_core_url(hass, "https://zigbeelens.theaussiepom.me")
    assert (
        hass.data[frontend.DATA_PANELS][PANEL_URL_PATH]["config"]["core_url"]
        == "https://zigbeelens.theaussiepom.me"
    )
