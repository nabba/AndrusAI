"""app.research.typeset_latex — render a Manuscript to LaTeX (paper.tex + references.bib).

Phase D output backend. A stdlib sibling to ``app.dossier.typeset.render_pdf``:
the dossier proved its content model (``ComposedReport``) is fully decoupled
from the ReportLab renderer, so a second backend is purely additive. Here the
content model is :class:`app.research.manuscript.Manuscript`, and the renderer
emits a conference-ready ``paper.tex`` + a real ``references.bib`` built from
the **verified** citations (the manuscript's ``references`` are exactly the
Phase-B survivors).

Three pure functions (``manuscript_to_latex`` / ``references_to_bibtex`` / the
cite-key minter) do the work and are trivially host-testable; ``render_latex``
is a thin writer that drops the two files when given an ``output_dir`` and is a
pure transform otherwise. No external deps — string templating + escaping only.

Fact-check warnings ride along as ``% FACT-CHECK`` LaTeX comments: visible to
the author in the source, invisible in the compiled PDF — flag, don't silently
ship (the run's ``research:verify`` step is what *enforces*).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.research.citation import Citation
from app.research.manuscript import Manuscript

# LaTeX special characters → escaped forms. ``\`` first (it's in the values).
_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_DEFAULT_PREAMBLE = (
    "\\usepackage[utf8]{inputenc}\n"
    "\\usepackage[T1]{fontenc}\n"
    "\\usepackage{hyperref}\n"
    "\\usepackage{graphicx}\n"
)


def latex_escape(text: str) -> str:
    """Escape LaTeX special characters in body text (NOT in commands we emit)."""
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in (text or ""))


def _md_to_latex(text: str) -> str:
    """Escape, then map the small markdown subset the composer emits. Bold/italic
    markers (``*``) aren't LaTeX specials, so they survive escaping; the braces
    in the ``\\textbf{...}`` we add are the only unescaped braces in the result."""
    s = latex_escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\\textit{\1}", s)
    return s


def _cite_key(c: Citation, used: set[str]) -> str:
    """A unique, LaTeX-safe BibTeX key: first-author surname + year, falling back
    to the sanitized identifier. Collisions get a/b/c suffixes."""
    base = ""
    if c.authors:
        toks = str(c.authors[0]).split()
        if toks:
            base = re.sub(r"[^A-Za-z]", "", toks[-1])
    if c.year:
        base += str(c.year)
    if not base:
        base = re.sub(r"[^A-Za-z0-9]", "", (c.doi or c.arxiv_id or "ref"))[:16]
    base = base or "ref"
    key = base
    suffix = 0
    while key in used:
        suffix += 1
        key = f"{base}{chr(ord('a') + suffix - 1)}"
    used.add(key)
    return key


def references_to_bibtex(citations) -> str:
    """One BibTeX entry per citation. arXiv-only → ``@misc`` w/ eprint;
    otherwise ``@article``. Returns ``""`` when there are no references."""
    cits = list(citations or [])
    if not cits:
        return ""
    used: set[str] = set()
    blocks: list[str] = []
    for c in cits:
        key = _cite_key(c, used)
        fields: list[tuple[str, str]] = []
        if c.title:
            fields.append(("title", "{" + latex_escape(c.title) + "}"))
        if c.authors:
            fields.append(("author", "{" + " and ".join(latex_escape(a) for a in c.authors) + "}"))
        if c.year:
            fields.append(("year", "{" + str(c.year) + "}"))
        if c.doi:
            fields.append(("doi", "{" + c.doi + "}"))
        if c.url:
            fields.append(("url", "{" + c.url + "}"))
        if c.arxiv_id and not c.doi:
            entry_type = "misc"
            fields.append(("eprint", "{" + c.arxiv_id + "}"))
            fields.append(("archivePrefix", "{arXiv}"))
        else:
            entry_type = "article"
        body = ",\n  ".join(f"{name} = {value}" for name, value in fields)
        blocks.append(f"@{entry_type}{{{key},\n  {body}\n}}")
    return "\n\n".join(blocks) + "\n"


def manuscript_to_latex(
    manuscript: Manuscript,
    *,
    documentclass: str = "article",
    preamble: str = _DEFAULT_PREAMBLE,
    bib_name: str = "references",
) -> str:
    """Render the manuscript as a compilable ``.tex`` source string.

    The ``Abstract`` section (if present) becomes an ``abstract`` environment;
    every other section becomes a ``\\section``. A ``\\bibliography`` is emitted
    only when there are references. Fact-check warnings become ``% FACT-CHECK``
    comments after their section.
    """
    has_refs = bool(manuscript.references)
    lines: list[str] = [
        f"\\documentclass{{{documentclass}}}",
        preamble.rstrip("\n"),
        f"\\title{{{latex_escape(manuscript.title)}}}",
        "\\author{AndrusAI Research}",
        "\\date{}",
        "",
        "\\begin{document}",
        "\\maketitle",
        "",
    ]
    for sec in manuscript.sections:
        if sec.title.strip().lower() == "abstract":
            lines += ["\\begin{abstract}", _md_to_latex(sec.prose), "\\end{abstract}", ""]
        else:
            lines += [f"\\section{{{latex_escape(sec.title)}}}", _md_to_latex(sec.prose), ""]
        for w in sec.fact_check_warnings:
            lines.append("% FACT-CHECK: " + w.replace("\n", " "))
        if sec.fact_check_warnings:
            lines.append("")
    if has_refs:
        lines += ["\\bibliographystyle{plain}", f"\\bibliography{{{bib_name}}}", ""]
    lines.append("\\end{document}")
    return "\n".join(lines) + "\n"


@dataclass
class RenderedPaper:
    tex: str
    bib: str
    tex_path: Optional[Path] = None
    bib_path: Optional[Path] = None


def render_latex(
    manuscript: Manuscript,
    *,
    output_dir: Optional[str] = None,
    documentclass: str = "article",
    preamble: str = _DEFAULT_PREAMBLE,
    bib_name: str = "references",
) -> RenderedPaper:
    """Render ``manuscript`` to LaTeX. Pure transform when ``output_dir`` is
    None (returns the strings); otherwise also writes ``paper.tex`` (+ a
    ``<bib_name>.bib`` when there are references) into ``output_dir``."""
    tex = manuscript_to_latex(manuscript, documentclass=documentclass, preamble=preamble, bib_name=bib_name)
    bib = references_to_bibtex(manuscript.references)
    if output_dir is None:
        return RenderedPaper(tex=tex, bib=bib)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    tex_path = out / "paper.tex"
    tex_path.write_text(tex, encoding="utf-8")
    bib_path = None
    if bib:
        bib_path = out / f"{bib_name}.bib"
        bib_path.write_text(bib, encoding="utf-8")
    return RenderedPaper(tex=tex, bib=bib, tex_path=tex_path, bib_path=bib_path)


__all__ = [
    "latex_escape",
    "references_to_bibtex",
    "manuscript_to_latex",
    "render_latex",
    "RenderedPaper",
]
