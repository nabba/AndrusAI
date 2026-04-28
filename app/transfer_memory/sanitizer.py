"""Sanitiser for compiled transfer-memory insights.

Three-tier promotion ladder:

  1. Hard-reject — credentials, API keys, auth tokens, URLs with secrets.
     Drafts tagged here never land in any KB.

  2. Demote to PROJECT_LOCAL — project-identifying proper nouns
     (PLG, Archibal, KaiCart, Piletilevi, iAbilet, etc.) or customer-
     specific facts. The draft can still inform within-project agents
     but does not transfer across projects.

  3. Demote to SAME_DOMAIN_ONLY — filenames, shell commands, code paths,
     specific tool/library references. The draft can inform same-domain
     work but is too concrete for cross-domain transfer.

Anything that passes all three tiers is GLOBAL_META eligible.

The sanitiser does NOT modify content. Per CLAUDE.md "Self-Improver
cannot modify its own evaluation criteria", denylists and regex are
hard-coded constants — adding an entry requires a code change reviewed
by the operator.

IMMUTABLE — infrastructure-level module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.transfer_memory.types import TransferScope


# ── Tier 1: hard-reject patterns ─────────────────────────────────────────

# Credentials, API keys, auth tokens. Any match → drop the draft.
_HARD_REJECT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("aws_secret_key", re.compile(r"(?i)\baws_secret_access_key\b\s*[:=]\s*\S{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openrouter_key", re.compile(r"sk-or-[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"gh[opsu]_[A-Za-z0-9]{36,}")),
    ("jwt", re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    )),
    ("bearer_token", re.compile(
        r"(?i)\bauthorization\s*:\s*bearer\s+\S{20,}"
    )),
    ("url_with_token", re.compile(
        r"https?://[^\s]*[?&](?:token|key|secret|api[_-]?key|access[_-]?token)=\S+"
    )),
    ("postgres_url_with_pw", re.compile(
        r"postgres(?:ql)?://[^/\s:@]+:[^@\s]+@"
    )),
    ("private_key", re.compile(
        r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    )),
)


# ── Tier 2: project-identifying proper nouns ─────────────────────────────

# Proper nouns that uniquely identify a project. Mention triggers demotion
# to PROJECT_LOCAL — the insight may still be useful inside the project
# but must not leak across.
#
# Deliberately narrower than ``project_isolation.PROJECT_KEYWORDS``: generic
# domain words ("ticket", "venue", "event") are NOT here because legitimate
# cross-domain insights may reference them abstractly.
_PROJECT_PROPER_NOUNS: tuple[str, ...] = (
    # PLG (Protect Group / Piletilevi)
    "plg", "piletilevi", "iabilet", "protect group",
    # Archibal
    "archibal", "c2pa",
    # KaiCart
    "kaicart", "tiktok shop", "thai sellers",
    # Geographic markers used as customer identifiers
    "thailand", "estonia ticketing", "latvia ticketing", "lithuania ticketing",
)


# ── Tier 3: same-domain-only signals ─────────────────────────────────────

# Paths, filenames, shell commands, library/tool names. Indicates the
# insight is too concrete for cross-domain transfer. May still be useful
# within its source domain.
_SAME_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("absolute_path", re.compile(
        r"(?:^|\s)/(?:app|usr|etc|var|home|opt|root)/[A-Za-z0-9_./-]+"
    )),
    ("py_file_ref", re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.py(?::\d+)?\b")),
    ("shell_command", re.compile(
        r"(?:^|\s)(?:cd|grep|find|sed|awk|cat|ls|pip|npm|yarn|docker|kubectl|psql)\s+\S+"
    )),
    ("module_dot_path", re.compile(
        r"\b(?:app|src|lib|tests?)\.[A-Za-z_][A-Za-z0-9_.]+\b"
    )),
)


# Currency-prefixed numeric values. Specific monetary figures rarely
# transfer well — they belong in fact-stores (belief_adapter), not
# procedural memory.
_CURRENCY_PATTERN = re.compile(
    r"[€$£¥]\s*\d{1,4}(?:[.,]\d+)?|\b\d{1,4}[.,]\d+\s*(?:EUR|USD|GBP|JPY)\b"
)


# Promotion ladder ordering (low → high permissiveness).
_SCOPE_ORDER: dict[TransferScope, int] = {
    TransferScope.SHADOW: 0,
    TransferScope.PROJECT_LOCAL: 1,
    TransferScope.SAME_DOMAIN_ONLY: 2,
    TransferScope.GLOBAL_META: 3,
}


@dataclass
class SanitizerVerdict:
    """Outcome of running ``check()`` on a candidate draft.

    ``allowed_scope`` is the most permissive scope the draft may be
    promoted to. ``hard_rejected`` is True only when Tier-1 patterns
    matched; in that case the draft must not be persisted at all.
    """
    allowed_scope: TransferScope
    hard_rejected: bool = False
    findings: list[tuple[str, str]] = field(default_factory=list)
    leakage_risk: float = 0.0   # 0..1 — derived from finding tier + count

    def should_persist(self) -> bool:
        return not self.hard_rejected


def check(content: str) -> SanitizerVerdict:
    """Apply the three-tier sanitiser. Returns a verdict; never raises.

    Empty content is treated as hard-rejected — no insight to compile.
    """
    if not content or not content.strip():
        return SanitizerVerdict(
            allowed_scope=TransferScope.SHADOW,
            hard_rejected=True,
            findings=[("empty_content", "")],
            leakage_risk=1.0,
        )

    findings: list[tuple[str, str]] = []
    text = content
    text_lower = content.lower()

    # Tier 1 — credentials. Any match is fatal.
    for name, pat in _HARD_REJECT_PATTERNS:
        m = pat.search(text)
        if m:
            findings.append((f"hard_reject:{name}", _redact(m.group(0))))
    if findings:
        return SanitizerVerdict(
            allowed_scope=TransferScope.SHADOW,
            hard_rejected=True,
            findings=findings,
            leakage_risk=1.0,
        )

    # Tier 2 — project proper nouns.
    project_findings: list[tuple[str, str]] = []
    for token in _PROJECT_PROPER_NOUNS:
        if token in text_lower:
            project_findings.append((f"project_noun:{token}", token))
    findings.extend(project_findings)

    # Tier 3 — same-domain markers.
    same_domain_findings: list[tuple[str, str]] = []
    for name, pat in _SAME_DOMAIN_PATTERNS:
        for m in pat.finditer(text):
            same_domain_findings.append((f"same_domain:{name}", m.group(0)[:80]))
            if len(same_domain_findings) >= 5:
                break
        if len(same_domain_findings) >= 5:
            break
    for m in _CURRENCY_PATTERN.finditer(text):
        same_domain_findings.append(("same_domain:currency", m.group(0)))
        if len(same_domain_findings) >= 8:
            break
    findings.extend(same_domain_findings)

    # Pick the strictest demotion implied by findings.
    if project_findings:
        scope = TransferScope.PROJECT_LOCAL
    elif same_domain_findings:
        scope = TransferScope.SAME_DOMAIN_ONLY
    else:
        scope = TransferScope.GLOBAL_META

    # Risk score: weighted by tier severity. Cap at 1.0.
    risk = 0.35 * len(project_findings) + 0.10 * len(same_domain_findings)
    risk = min(1.0, risk)

    return SanitizerVerdict(
        allowed_scope=scope,
        hard_rejected=False,
        findings=findings,
        leakage_risk=round(risk, 3),
    )


def cap_scope(requested: TransferScope, allowed: TransferScope) -> TransferScope:
    """Return whichever of the two is stricter on the promotion ladder.

    Caller can never expand beyond what the sanitiser allows.
    """
    return requested if _SCOPE_ORDER[requested] <= _SCOPE_ORDER[allowed] else allowed


def _redact(s: str) -> str:
    """Truncate + redact a matched secret for inclusion in the verdict log.

    First 4 chars + length. Lets the operator confirm the regex fired
    without leaking the credential value into the audit log.
    """
    head = s[:4]
    return f"{head}…[redacted len={len(s)}]"
