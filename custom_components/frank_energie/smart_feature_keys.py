# smart_feature_keys.py
from __future__ import annotations

from hashlib import sha1


def build_registry_key(domain: str, feature_id: str, context: str | None = None) -> str:
    """Create stable registry-safe key for HA entities."""
    raw = f"{domain}:{feature_id}:{context or ''}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]
