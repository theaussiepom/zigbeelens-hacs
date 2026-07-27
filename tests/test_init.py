"""HA integration setup/unload behaviour."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.sensor import SensorEntityDescription
from homeassistant.core import is_callback
from homeassistant.exceptions import ConfigEntryAuthFailed

from zigbeelens import (
    CONFIG_SCHEMA,
    async_migrate_entry,
    async_remove_entry,
    async_reload_entry,
    async_setup_entry,
    async_unload_entry,
)
from zigbeelens.const import (
    CONF_API_TOKEN,
    CONF_CORE_URL,
    CONF_PANEL_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from zigbeelens.exceptions import ZigbeeLensConnectionError
from zigbeelens.sensor import ZigbeeLensNetworkSensor, ZigbeeLensSensor


def test_yaml_schema_is_explicitly_config_entry_only(caplog) -> None:
    config = {DOMAIN: {}}
    with (
        patch(
            "homeassistant.helpers.config_validation.async_create_issue",
            create=True,
        ),
        patch("homeassistant.helpers.issue_registry.async_create_issue"),
    ):
        assert CONFIG_SCHEMA(config) is config
    assert "does not support YAML setup" in caplog.text


def _entry(*, panel_enabled: bool = True, api_token: str | None = None):
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.disabled_by = None
    entry.data = {
        "core_url": "http://127.0.0.1:8377",
        "verify_ssl": False,
        CONF_PANEL_ENABLED: panel_enabled,
    }
    if api_token is not None:
        entry.data[CONF_API_TOKEN] = api_token
    entry.options = {"scan_interval": 60}
    return entry


def _enrichment_manager():
    manager = MagicMock()
    manager.async_start = AsyncMock()
    manager.async_request_sync = MagicMock()
    manager.diagnostics = {
        "sync_state": "never_attempted",
        "match_state": "unknown",
    }
    return manager


@pytest.mark.asyncio
async def test_unload_always_unregisters_panel():
    hass = MagicMock()
    entry = _entry(panel_enabled=False)
    hass.data = {DOMAIN: {"entry1": {"coordinator": MagicMock(), "client": MagicMock()}}}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch("zigbeelens.async_unregister_panel", new=AsyncMock()) as unregister, patch(
        "zigbeelens.async_clear_repairs"
    ):
        ok = await async_unload_entry(hass, entry)

    assert ok is True
    unregister.assert_awaited_once_with(hass, "entry1")


@pytest.mark.asyncio
async def test_setup_with_panel_disabled_unregisters_panel():
    hass = MagicMock()
    entry = _entry(panel_enabled=False)
    client = MagicMock()
    client.core_url = "http://127.0.0.1:8377"
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()

    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch("zigbeelens.async_register_panel", new=AsyncMock()) as register, patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch("zigbeelens.async_manage_repairs"):
        ok = await async_setup_entry(hass, entry)

    assert ok is True
    manager.async_start.assert_awaited_once_with()
    register.assert_not_awaited()
    unregister.assert_awaited_once_with(hass, "entry1")


@pytest.mark.asyncio
async def test_setup_with_panel_enabled_registers_panel():
    hass = MagicMock()
    entry = _entry(panel_enabled=True, api_token="a" * 32)
    client = MagicMock()
    client.core_url = "http://127.0.0.1:8377"
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()

    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ) as client_cls, patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch("zigbeelens.async_register_panel", new=AsyncMock()) as register, patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch("zigbeelens.async_manage_repairs"):
        ok = await async_setup_entry(hass, entry)

    assert ok is True
    assert client_cls.call_args.kwargs["api_token"] == "a" * 32
    register.assert_awaited_once_with(hass, "entry1", client.core_url)
    unregister.assert_not_awaited()
    entry.add_update_listener.assert_called_once_with(async_reload_entry)
    manager.async_start.assert_awaited_once_with()
    assert hass.data[DOMAIN]["entry1"]["enrichment_manager"] is manager


@pytest.mark.asyncio
async def test_setup_missing_token_key_defaults_blank():
    hass = MagicMock()
    entry = _entry(panel_enabled=True)
    assert CONF_API_TOKEN not in entry.data
    client = MagicMock()
    client.core_url = "http://127.0.0.1:8377"
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ) as client_cls, patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch("zigbeelens.async_register_panel", new=AsyncMock()), patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ), patch("zigbeelens.async_manage_repairs"):
        ok = await async_setup_entry(hass, entry)

    assert ok is True
    assert client_cls.call_args.kwargs["api_token"] == ""


@pytest.mark.asyncio
async def test_setup_options_override_legacy_panel_and_reach_coordinator_interval():
    hass = MagicMock()
    entry = _entry(panel_enabled=True)
    entry.options = {
        CONF_PANEL_ENABLED: False,
        CONF_SCAN_INTERVAL: 900,
    }
    client = MagicMock(core_url="http://127.0.0.1:8377")
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ) as coordinator_cls, patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register, patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch("zigbeelens.async_manage_repairs"):
        assert await async_setup_entry(hass, entry) is True

    assert coordinator_cls.call_args.args[2] == 900
    register.assert_not_awaited()
    unregister.assert_awaited_once_with(hass, "entry1")


@pytest.mark.asyncio
async def test_config_entry_update_listener_causes_exactly_one_reload():
    hass = MagicMock()
    hass.config_entries.async_reload = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "entry1"

    await async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with("entry1")


@pytest.mark.asyncio
async def test_setup_malformed_stored_token_auth_failed():
    hass = MagicMock()
    entry = _entry(api_token="not-a-valid-token")
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_multi_entry_secondary_skips_panel_and_repairs():
    hass = MagicMock()
    primary = _entry(panel_enabled=True, api_token="a" * 32)
    primary.entry_id = "entry_a"
    secondary = _entry(panel_enabled=True, api_token="b" * 32)
    secondary.entry_id = "entry_b"
    secondary.data = {
        "core_url": "http://127.0.0.1:9000",
        "verify_ssl": False,
        CONF_PANEL_ENABLED: True,
        CONF_API_TOKEN: "b" * 32,
    }
    client = MagicMock()
    client.core_url = "http://127.0.0.1:9000"
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    listeners = []
    coordinator.async_add_listener = MagicMock(
        side_effect=lambda listener: listeners.append(listener) or (lambda: None)
    )
    manager = _enrichment_manager()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[primary, secondary])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ) as manager_cls, patch("zigbeelens.async_register_panel", new=AsyncMock()) as register, patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch("zigbeelens.async_manage_repairs") as manage:
        ok = await async_setup_entry(hass, secondary)
        assert ok is True
        register.assert_not_awaited()
        unregister.assert_not_awaited()
        manage.assert_not_called()
        assert "entry_b" in hass.data[DOMAIN]
        diagnostics_callback = manager_cls.call_args.kwargs["on_diagnostics_changed"]
        diagnostics_callback(object())
        manage.assert_not_called()

        # If the legacy primary later unloads, promotion changes the dynamic
        # owner marker and the already-registered manager callback owns repairs.
        hass.data[DOMAIN]["_global_owner_entry_id"] = "entry_b"
        diagnostics_callback(object())
        manage.assert_called_once_with(hass, coordinator, manager)


@pytest.mark.asyncio
async def test_legacy_cold_start_ignores_disabled_lower_sorted_entry():
    hass = MagicMock()
    disabled = _entry()
    disabled.entry_id = "entry_a"
    disabled.disabled_by = "user"
    enabled = _entry()
    enabled.entry_id = "entry_b"
    client = MagicMock(core_url="http://127.0.0.1:9000")
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[disabled, enabled])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register, patch(
        "zigbeelens.async_manage_repairs"
    ) as manage:
        assert await async_setup_entry(hass, enabled) is True

    register.assert_awaited_once_with(
        hass,
        "entry_b",
        "http://127.0.0.1:9000",
    )
    manage.assert_called_once_with(hass, coordinator, manager)
    assert hass.data[DOMAIN]["_global_owner_entry_id"] == "entry_b"


@pytest.mark.asyncio
async def test_coordinator_listener_requests_enrichment_and_repairs_synchronously():
    hass = MagicMock()
    entry = _entry(panel_enabled=True)
    client = MagicMock(core_url="http://127.0.0.1:8377")
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    manager = _enrichment_manager()
    listeners = []
    coordinator.async_add_listener = MagicMock(
        side_effect=lambda listener: listeners.append(listener) or (lambda: None)
    )
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ), patch(
        "zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator
    ), patch(
        "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
    ), patch("zigbeelens.async_register_panel", new=AsyncMock()), patch(
        "zigbeelens.async_manage_repairs"
    ) as repairs:
        assert await async_setup_entry(hass, entry) is True
        assert len(listeners) == 1
        assert is_callback(listeners[0])
        result = listeners[0]()

    assert result is None
    manager.async_request_sync.assert_called_once_with()
    assert repairs.call_args_list[-1].args == (hass, coordinator, manager)


@pytest.mark.asyncio
async def test_manager_diagnostics_callback_updates_only_live_global_owner_repairs():
    hass = MagicMock()
    entry = _entry(panel_enabled=True)
    client = MagicMock(core_url="http://127.0.0.1:8377")
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    manager = _enrichment_manager()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    with (
        patch("zigbeelens.async_get_clientsession", return_value=MagicMock()),
        patch("zigbeelens.ZigbeeLensApiClient", return_value=client),
        patch("zigbeelens.ZigbeeLensDataUpdateCoordinator", return_value=coordinator),
        patch(
            "zigbeelens.HomeAssistantEnrichmentManager", return_value=manager
        ) as manager_cls,
        patch("zigbeelens.async_register_panel", new=AsyncMock()),
        patch("zigbeelens.async_manage_repairs") as repairs,
    ):
        assert await async_setup_entry(hass, entry) is True
        repairs.reset_mock()
        diagnostics_callback = manager_cls.call_args.kwargs["on_diagnostics_changed"]

        diagnostics_callback(object())
        repairs.assert_called_once_with(hass, coordinator, manager)

        # Once the runtime is removed/unloaded, a retained callback cannot own
        # issue-registry mutations.
        hass.data[DOMAIN].pop(entry.entry_id)
        diagnostics_callback(object())
        repairs.assert_called_once_with(hass, coordinator, manager)


@pytest.mark.asyncio
async def test_multi_entry_secondary_unload_does_not_clear_primary_globals():
    hass = MagicMock()
    primary = _entry()
    primary.entry_id = "entry_a"
    secondary = _entry()
    secondary.entry_id = "entry_b"
    hass.data = {
        DOMAIN: {
            "entry_a": {"coordinator": MagicMock(), "client": MagicMock()},
            "entry_b": {"coordinator": MagicMock(), "client": MagicMock()},
        }
    }
    hass.config_entries.async_entries = MagicMock(return_value=[primary, secondary])
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch("zigbeelens.async_unregister_panel", new=AsyncMock()) as unregister, patch(
        "zigbeelens.async_clear_repairs"
    ) as clear:
        ok = await async_unload_entry(hass, secondary)

    assert ok is True
    unregister.assert_not_awaited()
    clear.assert_not_called()
    assert "entry_a" in hass.data[DOMAIN]
    assert "entry_b" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_multi_entry_primary_unload_promotes_loaded_secondary_globals():
    hass = MagicMock()
    primary = _entry()
    primary.entry_id = "entry_a"
    primary.disabled_by = "user"
    secondary = _entry()
    secondary.entry_id = "entry_b"
    secondary_client = MagicMock(core_url="http://127.0.0.1:9000")
    secondary_coordinator = MagicMock()
    secondary_manager = _enrichment_manager()
    hass.data = {
        DOMAIN: {
            "_global_owner_entry_id": "entry_a",
            "entry_a": {
                "coordinator": MagicMock(),
                "client": MagicMock(core_url="http://127.0.0.1:8377"),
            },
            "entry_b": {
                "coordinator": secondary_coordinator,
                "client": secondary_client,
                "enrichment_manager": secondary_manager,
            },
        }
    }
    hass.config_entries.async_entries = MagicMock(return_value=[primary, secondary])
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register, patch(
        "zigbeelens.async_clear_repairs"
    ) as clear, patch(
        "zigbeelens.async_manage_repairs"
    ) as manage:
        ok = await async_unload_entry(hass, primary)

    assert ok is True
    unregister.assert_awaited_once_with(hass, "entry_a")
    register.assert_awaited_once_with(
        hass,
        "entry_b",
        "http://127.0.0.1:9000",
    )
    clear.assert_called_once_with(hass)
    manage.assert_called_once_with(
        hass,
        secondary_coordinator,
        secondary_manager,
    )
    assert hass.data[DOMAIN]["_global_owner_entry_id"] == "entry_b"
    assert "entry_a" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_multi_entry_primary_normal_reload_does_not_promote_secondary():
    hass = MagicMock()
    primary = _entry()
    primary.entry_id = "entry_a"
    primary.disabled_by = None
    secondary = _entry()
    secondary.entry_id = "entry_b"
    hass.data = {
        DOMAIN: {
            "_global_owner_entry_id": "entry_a",
            "entry_a": {
                "coordinator": MagicMock(),
                "client": MagicMock(core_url="http://127.0.0.1:8377"),
            },
            "entry_b": {
                "coordinator": MagicMock(),
                "client": MagicMock(core_url="http://127.0.0.1:9000"),
                "enrichment_manager": _enrichment_manager(),
            },
        }
    }
    hass.config_entries.async_entries = MagicMock(return_value=[primary, secondary])
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch(
        "zigbeelens.async_unregister_panel", new=AsyncMock()
    ) as unregister, patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register, patch(
        "zigbeelens.async_clear_repairs"
    ) as clear, patch(
        "zigbeelens.async_manage_repairs"
    ) as manage:
        assert await async_unload_entry(hass, primary) is True

    unregister.assert_awaited_once_with(hass, "entry_a")
    register.assert_not_awaited()
    clear.assert_called_once_with(hass)
    manage.assert_not_called()
    assert "_global_owner_entry_id" not in hass.data[DOMAIN]


@pytest.mark.asyncio
async def test_migrate_v1_preserves_connection_options_and_entity_identity(
    mock_coordinator,
):
    hass = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.unique_id = DOMAIN
    entry.version = 1
    entry.data = {
        CONF_CORE_URL: "https://core.example",
        CONF_VERIFY_SSL: True,
        CONF_API_TOKEN: "a" * 32,
        CONF_PANEL_ENABLED: False,
    }
    entry.options = {CONF_SCAN_INTERVAL: 900}
    entry_id_before = entry.entry_id
    unique_id_before = entry.unique_id
    summary_unique_id_before = ZigbeeLensSensor(
        mock_coordinator,
        entry.entry_id,
        SensorEntityDescription(
            key="unavailable_devices",
            translation_key="unavailable_devices",
        ),
    ).unique_id
    network_unique_id_before = ZigbeeLensNetworkSensor(
        mock_coordinator,
        entry.entry_id,
        "home_unavailable_devices",
        "Home Unavailable Devices",
        "unavailable_devices",
        "home",
    ).unique_id

    assert await async_migrate_entry(hass, entry) is True

    kwargs = hass.config_entries.async_update_entry.call_args.kwargs
    assert entry.entry_id == entry_id_before
    assert entry.unique_id == unique_id_before
    assert "unique_id" not in kwargs
    assert kwargs["version"] == CONFIG_ENTRY_VERSION
    assert kwargs["data"] == {
        CONF_CORE_URL: "https://core.example",
        CONF_VERIFY_SSL: True,
        CONF_API_TOKEN: "a" * 32,
    }
    assert kwargs["options"] == {
        CONF_SCAN_INTERVAL: 900,
        CONF_PANEL_ENABLED: False,
    }
    assert (
        ZigbeeLensSensor(
            mock_coordinator,
            entry.entry_id,
            SensorEntityDescription(
                key="unavailable_devices",
                translation_key="unavailable_devices",
            ),
        ).unique_id
        == summary_unique_id_before
    )
    assert (
        ZigbeeLensNetworkSensor(
            mock_coordinator,
            entry.entry_id,
            "home_unavailable_devices",
            "Home Unavailable Devices",
            "unavailable_devices",
            "home",
        ).unique_id
        == network_unique_id_before
    )


@pytest.mark.asyncio
async def test_explicit_config_entry_removal_uses_only_exact_clear_method():
    hass = MagicMock()
    entry = _entry(api_token="a" * 32)
    hass.config_entries.async_entries = MagicMock(return_value=[])
    client = MagicMock()
    client.async_clear_home_assistant_enrichment = AsyncMock()

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ) as client_cls:
        await async_remove_entry(hass, entry)

    client.async_clear_home_assistant_enrichment.assert_awaited_once_with()
    assert client_cls.call_args.kwargs["api_token"] == "a" * 32


@pytest.mark.asyncio
async def test_legacy_entry_removal_never_clears_while_another_entry_remains():
    hass = MagicMock()
    entry = _entry(api_token="a" * 32)
    other = _entry(api_token="b" * 32)
    other.entry_id = "entry2"
    hass.config_entries.async_entries = MagicMock(return_value=[other])

    hass.data = {
        DOMAIN: {
            "entry2": {
                "coordinator": MagicMock(),
                "client": MagicMock(core_url="http://127.0.0.1:9000"),
                "enrichment_manager": _enrichment_manager(),
            }
        }
    }

    with patch("zigbeelens.ZigbeeLensApiClient") as client_cls, patch(
        "zigbeelens.async_register_panel", new=AsyncMock()
    ) as register, patch("zigbeelens.async_manage_repairs") as manage:
        await async_remove_entry(hass, entry)

    client_cls.assert_not_called()
    register.assert_awaited_once()
    manage.assert_called_once()
    assert hass.data[DOMAIN]["_global_owner_entry_id"] == "entry2"


@pytest.mark.asyncio
async def test_removal_clear_failure_logs_only_category(caplog):
    hass = MagicMock()
    entry = _entry(api_token="a" * 32)
    hass.config_entries.async_entries = MagicMock(return_value=[])
    client = MagicMock()
    client.async_clear_home_assistant_enrichment = AsyncMock(
        side_effect=ZigbeeLensConnectionError(
            "payload-secret 0x00124b0001abcdef"
        )
    )

    with patch("zigbeelens.async_get_clientsession", return_value=MagicMock()), patch(
        "zigbeelens.ZigbeeLensApiClient", return_value=client
    ):
        await async_remove_entry(hass, entry)

    assert "payload-secret" not in caplog.text
    assert "0x00124b0001abcdef" not in caplog.text
    assert "ZigbeeLensConnectionError" in caplog.text


@pytest.mark.asyncio
async def test_normal_unload_never_clears_core_enrichment():
    hass = MagicMock()
    entry = _entry()
    client = MagicMock()
    client.async_clear_home_assistant_enrichment = AsyncMock()
    hass.data = {
        DOMAIN: {
            "entry1": {
                "coordinator": MagicMock(),
                "client": client,
                "enrichment_manager": MagicMock(),
            }
        }
    }
    hass.config_entries.async_entries = MagicMock(return_value=[entry])
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch("zigbeelens.async_unregister_panel", new=AsyncMock()), patch(
        "zigbeelens.async_clear_repairs"
    ):
        assert await async_unload_entry(hass, entry) is True

    client.async_clear_home_assistant_enrichment.assert_not_awaited()
