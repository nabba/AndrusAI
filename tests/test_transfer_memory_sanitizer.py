"""Tests for app.transfer_memory.sanitizer."""

from app.transfer_memory.sanitizer import check, cap_scope
from app.transfer_memory.types import TransferScope


# ── Tier 1: hard reject ─────────────────────────────────────────────

def test_hard_rejects_aws_access_key():
    content = (
        "Long content about cloud infrastructure. "
        "AKIAIOSFODNN7EXAMPLE was found in the logs."
    )
    v = check(content)
    assert v.hard_rejected
    assert v.allowed_scope == TransferScope.SHADOW
    assert any("aws_access_key" in f[0] for f in v.findings)


def test_hard_rejects_anthropic_key():
    content = "Recovery requires sk-ant-fake12345abcdef0123456789 set in env."
    v = check(content)
    assert v.hard_rejected


def test_hard_rejects_openai_key():
    content = "The token sk-proj-abcdef1234567890ghijklmnop appeared in error."
    v = check(content)
    assert v.hard_rejected


def test_hard_rejects_jwt():
    jwt = "eyJabcdefghij12345.eyJabcdefghij12345.abcdefghij12345"
    content = f"Token in payload: {jwt} confirms the auth event."
    v = check(content)
    assert v.hard_rejected


def test_hard_rejects_bearer_token():
    content = (
        "When the request fails, check that Authorization: Bearer "
        "abcdef1234567890ghijklmnop is present."
    )
    v = check(content)
    assert v.hard_rejected


def test_hard_rejects_url_with_token():
    content = (
        "Use the API endpoint at https://api.example.com/data?token=abc123def456 "
        "to fetch results."
    )
    v = check(content)
    assert v.hard_rejected


def test_hard_rejects_postgres_url_with_password():
    content = "Connect via postgresql://user:secret123@db.host/database for testing."
    v = check(content)
    assert v.hard_rejected


def test_hard_rejected_means_should_not_persist():
    content = "Token: sk-ant-leak1234567890abcdefghij used in production."
    v = check(content)
    assert not v.should_persist()


# ── Tier 2: project-noun demotion ────────────────────────────────────

def test_demotes_to_project_local_when_project_noun_present():
    content = (
        "When pricing inconsistencies appear, verify against the PLG belief "
        "store before responding to user queries about ticketing."
    )
    v = check(content)
    assert not v.hard_rejected
    assert v.allowed_scope == TransferScope.PROJECT_LOCAL


def test_archibal_token_demotes():
    content = (
        "For Archibal-style content authenticity workflows, verify the "
        "provenance signature before finalising."
    )
    v = check(content)
    assert v.allowed_scope == TransferScope.PROJECT_LOCAL


def test_kaicart_token_demotes():
    content = (
        "When integrating with KaiCart workflows, validate the seller's "
        "configuration before scheduling."
    )
    v = check(content)
    assert v.allowed_scope == TransferScope.PROJECT_LOCAL


# ── Tier 3: same-domain demotion ─────────────────────────────────────

def test_absolute_path_demotes_to_same_domain():
    content = (
        "Validate the configuration before applying changes. "
        "The /app/workspace/skills directory holds the artefacts."
    )
    v = check(content)
    assert v.allowed_scope == TransferScope.SAME_DOMAIN_ONLY


def test_py_file_ref_demotes_to_same_domain():
    content = (
        "Always verify before applying changes; the helper in adapter.py "
        "is the canonical place."
    )
    v = check(content)
    assert v.allowed_scope == TransferScope.SAME_DOMAIN_ONLY


def test_currency_demotes_to_same_domain():
    content = (
        "Verify cost reports against the billing source. "
        "Numeric example: €245.50 should be checked daily."
    )
    v = check(content)
    assert v.allowed_scope == TransferScope.SAME_DOMAIN_ONLY


# ── No flags: global meta ────────────────────────────────────────────

def test_clean_content_allows_global_meta():
    content = (
        "When answering date-sensitive numeric claims, retrieve from the "
        "authoritative source or registered belief store before finalising. "
        "Treat prior conversation memory as a cue, not evidence. "
        "If the source cannot be checked, escalate instead of guessing."
    )
    v = check(content)
    assert not v.hard_rejected
    assert v.allowed_scope == TransferScope.GLOBAL_META
    assert v.findings == []


def test_clean_content_has_zero_leakage_risk():
    content = (
        "Verify external claims through authoritative sources before answering. "
        "Treat retrieved memory as a cue rather than evidence."
    )
    v = check(content)
    assert v.leakage_risk == 0.0


# ── Empty content ────────────────────────────────────────────────────

def test_empty_content_hard_rejected():
    v = check("")
    assert v.hard_rejected
    assert v.allowed_scope == TransferScope.SHADOW


def test_whitespace_only_hard_rejected():
    v = check("   \n   ")
    assert v.hard_rejected


# ── cap_scope ────────────────────────────────────────────────────────

def test_cap_scope_returns_stricter_of_two():
    assert cap_scope(
        TransferScope.GLOBAL_META, TransferScope.SAME_DOMAIN_ONLY,
    ) == TransferScope.SAME_DOMAIN_ONLY
    assert cap_scope(
        TransferScope.SAME_DOMAIN_ONLY, TransferScope.GLOBAL_META,
    ) == TransferScope.SAME_DOMAIN_ONLY
    assert cap_scope(
        TransferScope.SHADOW, TransferScope.GLOBAL_META,
    ) == TransferScope.SHADOW
    assert cap_scope(
        TransferScope.PROJECT_LOCAL, TransferScope.PROJECT_LOCAL,
    ) == TransferScope.PROJECT_LOCAL


# ── Leakage risk score ───────────────────────────────────────────────

def test_project_noun_increases_leakage_risk():
    content = (
        "When PLG ticketing systems show inconsistencies, verify against "
        "the belief store before responding."
    )
    v = check(content)
    assert v.leakage_risk >= 0.3


# ── Redaction ────────────────────────────────────────────────────────

def test_findings_redact_secret_value():
    content = "Token: sk-ant-fakekeyabcdef12345xyz used in production."
    v = check(content)
    assert v.hard_rejected
    leaked_substring = "fakekeyabcdef"
    for _kind, snippet in v.findings:
        # The redacted form must not contain the full secret.
        assert leaked_substring not in snippet


def test_redaction_keeps_prefix_and_length_marker():
    content = "Anthropic key sk-ant-secretvalue1234567890 leaked."
    v = check(content)
    assert v.hard_rejected
    found = [f for f in v.findings if "anthropic_key" in f[0]]
    assert found, "Expected anthropic_key finding"
    snippet = found[0][1]
    assert "redacted" in snippet
    assert "len=" in snippet
