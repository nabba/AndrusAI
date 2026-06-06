"""Pin: the TIER_IMMUTABLE / protection gates are CASE-INSENSITIVE.

Security review 2026-06-06. The operator's host filesystem is case-insensitive
(APFS), so an exact-case membership test let a change-request for
``app/Auto_deployer.py`` slip the gate (`is_tier_immutable=False`), reach the
operator, and — once approved — write to the SAME inode as the real protected
``app/auto_deployer.py``. The fix casefolds every protection membership/prefix
test in ``change_requests.validator``, ``architecture_requests.validator`` and
``auto_deployer.get_protection_tier``.

These tests would FAIL against the pre-fix (exact-case) code and pass after.
"""
import pytest

pytest.importorskip("pydantic")  # auto_deployer/validator pull config models

from app.auto_deployer import TIER_IMMUTABLE, ProtectionTier, get_protection_tier  # noqa: E402
from app.change_requests.validator import is_protected, validate  # noqa: E402


def _app_tier_entry() -> str:
    """A real ``app/*.py`` entry from TIER_IMMUTABLE (so the test can't drift)."""
    for p in sorted(TIER_IMMUTABLE):
        if p.startswith("app/") and p.endswith(".py"):
            return p
    pytest.skip("no app/*.py entry in TIER_IMMUTABLE")


def _case_variant(p: str) -> str:
    """Flip the case of the first alpha char AFTER the last '/'. The ``app/``
    root prefix is left intact (it is checked case-sensitively as an allowlist,
    so a mangled root would be refused as 'outside repo' before reaching the
    TIER check — not what we want to exercise)."""
    head, _, tail = p.rpartition("/")
    for i, ch in enumerate(tail):
        if ch.isalpha():
            tail = tail[:i] + ch.swapcase() + tail[i + 1:]
            break
    return (f"{head}/" if head else "") + tail


def test_cr_validator_rejects_tier_immutable_case_variant():
    p = _app_tier_entry()
    v = _case_variant(p)
    assert v != p, "case variant must actually differ"
    r = validate(path=v, new_content="x = 1\n")
    assert not r.ok and r.is_tier_immutable, f"{v!r} bypassed TIER_IMMUTABLE"


def test_cr_validator_rejects_forbidden_prefix_case_variant():
    # app/subia/ is a _FORBIDDEN_PATH_PREFIXES entry; the variant must be caught.
    r = validate(path="app/Subia/kernel.py", new_content="x = 1\n")
    assert not r.ok and r.is_tier_immutable


def test_get_protection_tier_case_insensitive():
    p = _app_tier_entry()
    assert get_protection_tier(_case_variant(p)) == ProtectionTier.IMMUTABLE


def test_is_protected_case_insensitive():
    p = _app_tier_entry()
    assert is_protected(_case_variant(p))


def test_exact_case_still_rejected_baseline():
    # Sanity floor: the unmangled path stays rejected (guards against a future
    # refactor that makes casefolding a silent no-op for the exact-case path).
    p = _app_tier_entry()
    assert validate(path=p, new_content="x = 1\n").is_tier_immutable
    assert get_protection_tier(p) == ProtectionTier.IMMUTABLE
