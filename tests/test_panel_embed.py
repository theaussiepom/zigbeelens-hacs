"""Embed-safety helper and panel asset tests for optional embedded view."""

from __future__ import annotations

from pathlib import Path

from zigbeelens.panel_embed_logic import can_embed_dashboard

PANEL_JS = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "zigbeelens"
    / "panel"
    / "zigbeelens-panel.js"
)


def test_https_ha_http_core_cannot_embed():
    assert can_embed_dashboard("https:", "http://192.168.100.5:8377") is False


def test_https_ha_https_core_can_embed():
    assert can_embed_dashboard("https:", "https://zigbeelens.example.com/") is True


def test_http_ha_http_core_can_embed():
    assert can_embed_dashboard("http:", "http://192.168.100.5:8377") is True


def test_invalid_core_url_cannot_embed():
    assert can_embed_dashboard("https:", "not-a-url") is False
    assert can_embed_dashboard("https:", "") is False
    assert can_embed_dashboard("https:", "https://user:pass@host.example") is False


def test_panel_asset_has_embed_flow():
    source = PANEL_JS.read_text(encoding="utf-8")
    assert "Try Embedded View" in source
    assert "Back to Summary" in source
    assert "embed_blocked" in source
    assert "canEmbedDashboard" in source
    assert "canonicalizeCoreOrigin" in source
    assert "<ha-menu-button" in source
    assert "_syncHaMenuButton" in source
    assert "panel-header" in source
    assert 'id="menu-btn"' in source
    assert 'id="back-summary"' in source
    assert "set narrow(n)" in source
    assert "Open in new tab" not in source
    assert "hass-toggle-menu" not in source
    assert "ha-sidebar-open" not in source
    assert 'target="_blank"' in source
    assert 'rel="noopener noreferrer"' in source
    assert 'title="ZigbeeLens full dashboard"' in source
    assert 'loading="lazy"' in source
    assert 'referrerpolicy="no-referrer"' in source


def test_panel_does_not_auto_embed():
    source = PANEL_JS.read_text(encoding="utf-8")
    assert "_maybeAutoEmbed" not in source
    assert "this._view = \"summary\"" in source or "this._view = 'summary'" in source
    assert "_tryEmbeddedView" in source
    assert "_backToSummary" in source
    assert "opens here automatically" not in source
