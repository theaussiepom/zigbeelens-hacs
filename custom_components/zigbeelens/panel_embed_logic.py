"""Pure embed-safety rules for the HACS companion panel (mirrors panel JS)."""

from __future__ import annotations

from .core_origin import InvalidCoreOrigin, canonicalize_core_origin


def can_embed_dashboard(ha_protocol: str, core_url: str) -> bool:
    """Return True when browser mixed-content rules likely allow an iframe embed.

    Mirrors ``canEmbedDashboard()`` in ``panel/zigbeelens-panel.js``.
    Requires a canonical absolute HTTP(S) Core origin (no relative resolution).
    """
    ha = (ha_protocol or "").strip().lower()
    if not ha.endswith(":"):
        return False
    try:
        origin = canonicalize_core_origin(core_url)
    except InvalidCoreOrigin:
        return False
    core_protocol = f"{origin.split(':', 1)[0]}:"
    is_mixed_content_iframe = ha == "https:" and core_protocol == "http:"
    return not is_mixed_content_iframe
