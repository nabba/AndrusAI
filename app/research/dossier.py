"""app.research.dossier — assemble + render a research run into a PDF dossier.

Phase B of the auto-research layer. A finished (or partial) research
``ExecutorRun`` already carries everything a reader needs: the literature it
retrieved, the hypotheses it proposed, the investigation notes, the findings
draft, and the evidence-gate verdict. This module turns those artifacts into a
multi-page PDF — at ZERO added LLM cost (it assembles existing text; it does
not re-summarise).

Two layers, split on dependency weight:

  * :func:`build_research_dossier` ``(run) -> ResearchDossier``
        Pure-stdlib assembly. Pulls the research artifacts out of the run's
        step results (reusing :mod:`app.research.run`'s decoders) into
        research-local section dataclasses. Host-safe — no reportlab, no
        pydantic, no LLM.

  * :func:`render_research_dossier` ``(run, *, output_path=None) -> Path``
        Renders the assembled dossier to a PDF. Reuses the dossier subsystem's
        *generic* typesetting helpers (``app.dossier.typeset``:
        ``_build_styles`` / ``_render_section`` / ``_markdown_to_paragraph_html``
        / ``_resolve_output_path`` / ``_slug`` / ``_truncate``) with a
        research-specific cover, TOC, and literature-provenance appendix.
        reportlab + typeset are imported lazily inside this function, so module
        load stays pure stdlib.

Why not reuse ``app.dossier.typeset.render_pdf`` directly: that flow is
company-coupled — its cover says "Investment Dossier", and its appendices
iterate a :class:`CompanyDossier`'s typed fields + ``coverage_report``.
Shimming a fake ``CompanyDossier`` would leak company field-names into the
research surface. Instead we reuse only typeset's generic helpers (typography,
section rendering, path resolution) and own the research-shaped flow.

The on-demand render works in *any* run state: an operator can pull a dossier
from a BLOCKED run mid-flight to read the flagged claims. Gate escalation is
surfaced as a section's ``fact_check_warnings`` (typeset renders those as a
"Data-quality flags" sidebar), so the report is honest about what didn't pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # annotations only — keeps module load pure stdlib
    from pathlib import Path

    from app.autonomous_executor.models import ExecutorRun

logger = logging.getLogger(__name__)


# ── Research-local value types ───────────────────────────────────────────────


@dataclass
class ResearchSection:
    """One rendered section.

    Duck-types :class:`app.dossier.compose.SectionOutput` for exactly the
    fields :func:`app.dossier.typeset._render_section` reads — ``.title`` /
    ``.prose`` / ``.fact_check_warnings`` — so the generic section renderer
    works on it unchanged.
    """

    key: str
    title: str
    prose: str
    fact_check_warnings: list[str] = field(default_factory=list)


@dataclass
class ResearchDossier:
    """The assembled research dossier, ready to render."""

    question: str
    status: str
    sections: list[ResearchSection] = field(default_factory=list)
    literature: list[dict] = field(default_factory=list)
    n_hypotheses: int = 0
    gate_action: Optional[str] = None
    gate_note: str = ""

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "status": self.status,
            "n_literature": len(self.literature),
            "n_hypotheses": self.n_hypotheses,
            "gate_action": self.gate_action,
            "gate_note": self.gate_note,
            "sections": [
                {
                    "key": s.key,
                    "title": s.title,
                    "prose": s.prose,
                    "fact_check_warnings": list(s.fact_check_warnings),
                }
                for s in self.sections
            ],
        }


def _esc(text: str) -> str:
    """Minimal HTML-escape for ReportLab Paragraph text we render directly
    (cover/TOC/table cells) — no markdown processing, just safety."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ── Assembly (pure stdlib, host-safe) ─────────────────────────────────────────


def build_research_dossier(run: "ExecutorRun") -> ResearchDossier:
    """Assemble a :class:`ResearchDossier` from a run's artifacts.

    Reuses :mod:`app.research.run`'s decoders so the JSON-in-``result_text``
    convention lives in exactly one place. Works on a run in any state —
    missing artifacts render as honest placeholders rather than errors.
    """
    from app.research.run import (
        HINT_HYPOTHESES,
        HINT_INVESTIGATE,
        HINT_LITERATURE,
        _decode_list,
        _text_for,
        summarise_run,
    )

    outcome = summarise_run(run)
    literature = _decode_list(run, HINT_LITERATURE)
    hypotheses = _decode_list(run, HINT_HYPOTHESES)
    investigation = _text_for(run, HINT_INVESTIGATE).strip()
    draft = (outcome.draft or "").strip()

    sections: list[ResearchSection] = []

    # 1. Summary — counts + leading hypothesis + gate verdict at a glance.
    summary_lines = [
        f"**Question.** {run.goal}",
        f"**Status.** {outcome.status}",
        f"**Literature reviewed.** {len(literature)} source(s).",
        f"**Hypotheses proposed.** {len(hypotheses)}.",
    ]
    if outcome.top_hypothesis:
        summary_lines.append(f"**Leading hypothesis.** {outcome.top_hypothesis}")
    if outcome.gate_action:
        summary_lines.append(
            f"**Evidence gate.** Escalated to {outcome.gate_action} "
            "— see flags below."
        )
    else:
        summary_lines.append("**Evidence gate.** Cleared.")
    sections.append(
        ResearchSection(
            key="summary",
            title="Summary",
            prose="\n\n".join(summary_lines),
        )
    )

    # 2. Literature reviewed — titled list; full provenance is the appendix.
    if literature:
        lit_lines: list[str] = []
        for hit in literature:
            title = str(hit.get("title") or hit.get("text") or "").strip()
            if not title:
                continue
            src = str(hit.get("source") or "").strip()
            tag = f"  *[{src}]*" if src else ""
            lit_lines.append(f"- {title[:300]}{tag}")
        lit_prose = "\n\n".join(lit_lines) or "(no titled sources retrieved)"
    else:
        lit_prose = "No literature was retrieved for this question."
    sections.append(
        ResearchSection(key="literature", title="Literature Reviewed", prose=lit_prose)
    )

    # 3. Hypotheses — ranked, with novelty when present.
    if hypotheses:
        hyp_lines: list[str] = []
        for hyp in hypotheses:
            text = str(hyp.get("text") or "").strip()
            if not text:
                continue
            rank = hyp.get("rank")
            novelty = str(hyp.get("novelty") or "").strip()
            prefix = f"{rank}. " if rank else "- "
            suffix = f"  *({novelty})*" if novelty else ""
            hyp_lines.append(f"{prefix}{text}{suffix}")
        hyp_prose = "\n\n".join(hyp_lines) or "(no hypotheses proposed)"
    else:
        hyp_prose = "No hypotheses were proposed for this question."
    sections.append(
        ResearchSection(key="hypotheses", title="Hypotheses", prose=hyp_prose)
    )

    # 4. Investigation — the raw investigation-step notes.
    sections.append(
        ResearchSection(
            key="investigation",
            title="Investigation",
            prose=investigation or "No investigation notes were recorded.",
        )
    )

    # 5. Findings — the findings draft (the citable write-up).
    sections.append(
        ResearchSection(
            key="findings",
            title="Findings",
            prose=draft or "No findings draft was produced.",
        )
    )

    # 6. Evidence gate — escalation becomes a data-quality flag.
    gate_warnings: list[str] = []
    if outcome.gate_action:
        gate_warnings.append(
            f"Evidence gate escalated to {outcome.gate_action}: "
            f"{outcome.gate_note or '(no detail)'}"
        )
        gate_prose = (
            f"The research-evidence gate escalated this draft to "
            f"**{outcome.gate_action}** before it could be accepted. The "
            "flagged concern is listed under the data-quality flags below; "
            "the affected claims need a citation or independent verification."
        )
    else:
        gate_prose = (
            "The research-evidence gate cleared this draft — no uncited "
            "empirical claims were detected."
        )
        if outcome.gate_note:
            gate_prose += f"\n\n{outcome.gate_note}"
    sections.append(
        ResearchSection(
            key="gate",
            title="Evidence Gate",
            prose=gate_prose,
            fact_check_warnings=gate_warnings,
        )
    )

    return ResearchDossier(
        question=run.goal,
        status=outcome.status,
        sections=sections,
        literature=literature,
        n_hypotheses=len(hypotheses),
        gate_action=outcome.gate_action,
        gate_note=outcome.gate_note,
    )


# ── Render (lazy reportlab + typeset helpers) ─────────────────────────────────


def render_research_dossier(
    run: "ExecutorRun", *, output_path: str | None = None
) -> "Path":
    """Render the run's dossier to a PDF and return its path.

    Reuses ``app.dossier.typeset``'s generic helpers for typography, section
    rendering, and path resolution; owns the research-specific cover, TOC, and
    literature-provenance appendix.

    Raises ``RuntimeError`` if reportlab isn't installed — mirrors
    :func:`app.dossier.typeset.render_pdf`; the typesetter has no HTML
    fallback. The assembly step is failure-isolated, so only the reportlab
    dependency can make this unavailable.
    """
    from app.dossier import typeset as T

    if not T._RL_PACK:
        raise RuntimeError(
            "reportlab not available — cannot render research dossier. "
            "Install reportlab in the runtime image."
        )

    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        BaseDocTemplate,
        Frame,
        PageBreak,
        PageTemplate,
    )

    dossier = build_research_dossier(run)
    styles = T._build_styles()
    generated_at = datetime.now(timezone.utc)

    if output_path:
        path = T._resolve_output_path(output_path)
    else:
        slug = T._slug((dossier.question or "research")[:60])
        rid = getattr(run, "run_id", "") or "run"
        date_str = generated_at.strftime("%Y%m%d")
        path = T._resolve_output_path(f"research_{slug}_{rid}_{date_str}.pdf")

    margin = 0.75 * inch
    doc = BaseDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin + 0.25 * inch,
        bottomMargin=margin,
        title=f"Research Dossier — {dossier.question[:80]}",
        author="BotArmy Research Subsystem",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates(
        [
            PageTemplate(
                id="default",
                frames=[frame],
                onPage=lambda c, d: _page_furniture(c, d, generated_at=generated_at),
            )
        ]
    )

    flowables: list = []
    flowables.extend(_render_cover(dossier, generated_at, styles))
    flowables.append(PageBreak())
    flowables.extend(_render_toc(dossier, styles))
    flowables.append(PageBreak())
    for section in dossier.sections:
        flowables.extend(T._render_section(section, styles))
        flowables.append(PageBreak())
    flowables.extend(_render_provenance_appendix(dossier, styles))

    doc.build(flowables)
    logger.info("research.dossier: wrote PDF to %s", path)
    return path


def _page_furniture(canvas, doc, *, generated_at) -> None:
    """Header/footer on every page after the cover (research-branded)."""
    from app.dossier.typeset import _RL_PACK

    if not _RL_PACK:
        return
    from reportlab.lib import colors
    from reportlab.lib.units import inch

    page_num = canvas.getPageNumber()
    if page_num == 1:
        return  # cover page is bare
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    canvas.drawString(0.75 * inch, 10.7 * inch, "Research Dossier")
    canvas.drawRightString(7.75 * inch, 10.7 * inch, generated_at.strftime("%Y-%m-%d"))
    canvas.line(0.75 * inch, 10.65 * inch, 7.75 * inch, 10.65 * inch)
    canvas.drawCentredString(4.25 * inch, 0.5 * inch, f"— page {page_num} —")
    canvas.restoreState()


def _render_cover(dossier: ResearchDossier, generated_at, styles) -> list:
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer

    out: list = []
    out.append(Spacer(1, 2.0 * inch))
    out.append(Paragraph("Research Dossier", styles["title"]))
    out.append(Spacer(1, 0.2 * inch))
    out.append(Paragraph(_esc(dossier.question), styles["subtitle"]))
    out.append(Spacer(1, 0.3 * inch))
    out.append(
        Paragraph(f"Generated {generated_at.strftime('%B %d, %Y')}", styles["subtitle"])
    )
    out.append(Spacer(1, 1.2 * inch))
    out.append(
        Paragraph(
            "This dossier is auto-assembled from a research run's own artifacts "
            "— the literature it retrieved, the hypotheses it proposed, its "
            "investigation notes, and the findings draft. The findings were "
            "checked by the research-evidence gate; any escalation is surfaced "
            "inline as a data-quality flag rather than hidden.",
            styles["body"],
        )
    )
    out.append(Spacer(1, 0.3 * inch))
    gate_line = (
        f"Evidence gate: escalated to {dossier.gate_action}."
        if dossier.gate_action
        else "Evidence gate: cleared."
    )
    out.append(
        Paragraph(
            f"Status: {_esc(dossier.status)}. "
            f"Literature: {len(dossier.literature)} source(s). "
            f"Hypotheses: {dossier.n_hypotheses}. {gate_line}",
            styles["small"],
        )
    )
    return out


def _render_toc(dossier: ResearchDossier, styles) -> list:
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer

    out: list = []
    out.append(Paragraph("Contents", styles["h1"]))
    out.append(Spacer(1, 0.2 * inch))
    for i, section in enumerate(dossier.sections, start=1):
        warn = ""
        if section.fact_check_warnings:
            warn = (
                f' <font size="7" color="#A0410B">'
                f"⚠ {len(section.fact_check_warnings)} flag(s)</font>"
            )
        out.append(
            Paragraph(f"{i}. {_esc(section.title)}{warn}", styles["toc_entry"])
        )
    out.append(
        Paragraph(
            f"{len(dossier.sections) + 1}. Literature Provenance",
            styles["toc_entry"],
        )
    )
    return out


def _render_provenance_appendix(dossier: ResearchDossier, styles) -> list:
    """Every retrieved source as a table: #, Title, Source, Identifier, Published."""
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    from app.dossier.typeset import _truncate

    out: list = []
    out.append(Paragraph("Literature Provenance", styles["h1"]))
    out.append(
        Paragraph(
            "Every source the run retrieved, with its origin and identifier. "
            "Use this to trace the findings back to primary literature.",
            styles["small"],
        )
    )
    out.append(Spacer(1, 0.15 * inch))

    if not dossier.literature:
        out.append(Paragraph("(no literature retrieved)", styles["body"]))
        return out

    rows: list[list[str]] = [["#", "Title", "Source", "Identifier", "Published"]]
    for i, hit in enumerate(dossier.literature, start=1):
        title = str(hit.get("title") or hit.get("text") or "").strip() or "(untitled)"
        rows.append(
            [
                str(i),
                _truncate(_esc(title), 54),
                _esc(str(hit.get("source") or "—")),
                _truncate(_esc(str(hit.get("id") or "—")), 28),
                _truncate(_esc(str(hit.get("published") or "—")), 12),
            ]
        )

    col_widths = [0.35 * inch, 3.1 * inch, 0.7 * inch, 1.85 * inch, 0.9 * inch]
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B3D91")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    out.append(table)
    return out


__all__ = [
    "ResearchSection",
    "ResearchDossier",
    "build_research_dossier",
    "render_research_dossier",
]
