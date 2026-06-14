"""Pure embed-safety rules for the HACS companion panel (mirrors panel JS)."""

from __future__ import annotations

from urllib.parse import urlparse


def can_embed_dashboard(ha_protocol: str, core_url: str) -> bool:
    """Return True when browser mixed-content rules likely allow an iframe embed.

    Mirrors ``canEmbedDashboard()`` in ``panel/zigbeelens-panel.js``.
    """
    ha = (ha_protocol or "").strip().lower()
    if not ha.endswith(":"):
        return False
    if not (core_url or "").strip():
        return False
    try:
        parsed = urlparse(core_url.strip())
        if not parsed.scheme or not parsed.netloc:
            return False
        core_protocol = f"{parsed.scheme.lower()}:"
    except ValueError:
        return False
    is_mixed_content_iframe = ha == "https:" and core_protocol == "http:"
    return not is_mixed_content_iframe
