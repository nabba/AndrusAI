"""Deterministic source→ProcessingMode classifier (Proposal 5 §5.1)."""
from __future__ import annotations

from typing import Optional

from app.subia.config import SUBIA_CONFIG
from app.subia.kernel import SceneItem

INTROSPECTIVE = "introspective"
MEMORIAL = "memorial"
PERCEPTUAL = "perceptual"
IMAGINATIVE = "imaginative"
SOCIAL = "social"

PROCESSING_MODES = (INTROSPECTIVE, MEMORIAL, PERCEPTUAL, IMAGINATIVE, SOCIAL)

# Conservative default — unclassified items lean perceptual rather than
# introspective so they cannot accidentally inflate self-coherence.
_DEFAULT_MODE = PERCEPTUAL


def classify_source(source: str, content_ref: str = "") -> str:
    """Map a scene-item source string to a ProcessingMode.

    Resolution order (most specific first):
      1. Exact match in BOUNDARY_MODE_MAP
      2. Longest prefix match (handles 'wiki/self/foo.md' → 'wiki/self/')
      3. content_ref prefix match
      4. _DEFAULT_MODE (perceptual)
    """
    if not source and not content_ref:
        return _DEFAULT_MODE
    table: dict = SUBIA_CONFIG.get("BOUNDARY_MODE_MAP", {})
    src = (source or "").strip()
    ref = (content_ref or "").strip()

    # 1. Exact source match
    if src in table:
        return table[src]

    # 2. Longest-prefix on source
    candidates = sorted(
        (k for k in table if src.startswith(k.rstrip("/"))),
        key=len,
        reverse=True,
    )
    if candidates:
        return table[candidates[0]]

    # 3. Longest-prefix on content_ref (e.g. 'wiki/self/identity.md')
    candidates = sorted(
        (k for k in table if ref.startswith(k.rstrip("/"))),
        key=len,
        reverse=True,
    )
    if candidates:
        return table[candidates[0]]

    return _DEFAULT_MODE


def classify_scene_item(item: SceneItem) -> str:
    """Classify a SceneItem and stamp item.processing_mode in place.

    Ownership refines source: items owned by self lean introspective if
    the source is ambiguous (e.g. an internal wiki page).
    """
    mode = classify_source(item.source, item.content_ref)
    # Ownership refinement: a self-owned item from an unspecified source
    # is more introspective than a third-party one.
    if mode == _DEFAULT_MODE and getattr(item, "ownership", "self") == "self":
        # only refine when source itself is empty/unspecified
        if not item.source:
            mode = INTROSPECTIVE
    item.processing_mode = mode
    return mode


def classify_scene(items) -> int:
    """Classify every item; return number of items newly classified."""
    n = 0
    for item in items or []:
        if getattr(item, "processing_mode", None) is None:
            classify_scene_item(item)
            n += 1
    return n
