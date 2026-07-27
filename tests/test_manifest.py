"""Manifest and HACS metadata validation."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "zigbeelens"


def test_manifest_parses_and_has_expected_fields() -> None:
    manifest = json.loads((INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["domain"] == "zigbeelens"
    assert manifest["name"] == "ZigbeeLens"
    assert manifest["config_flow"] is True
    assert manifest["single_config_entry"] is True
    assert manifest["iot_class"] == "local_polling"


def test_config_flow_strings_do_not_embed_urls() -> None:
    strings = json.loads((INTEGRATION_DIR / "strings.json").read_text(encoding="utf-8"))
    translations = json.loads(
        (INTEGRATION_DIR / "translations" / "en.json").read_text(encoding="utf-8")
    )
    for payload in (strings, translations):
        for step in ("user", "reconfigure"):
            description = payload["config"]["step"][step]["data_description"]["core_url"]
            assert "://" not in description


def test_ha_branding_assets_exist() -> None:
    # Inline brand assets for HA Settings (2026.3+ brands proxy). HACS downloads
    # uses its own integration placeholder; root repo icons are not packaged.
    assert (INTEGRATION_DIR / "brand" / "icon.png").is_file()
    assert (INTEGRATION_DIR / "brand" / "logo.png").is_file()
    assert (INTEGRATION_DIR / "brand" / "icon@2x.png").is_file()
    assert (INTEGRATION_DIR / "brand" / "logo@2x.png").is_file()
    assert (REPO_ROOT / "docs" / "zigbeelens-icon.svg").is_file()
    assert (REPO_ROOT / "docs" / "zigbeelens-logo.svg").is_file()


def test_hacs_json_parses() -> None:
    hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs["name"] == "ZigbeeLens"
    assert hacs["render_readme"] is True
    assert "homeassistant" in hacs
