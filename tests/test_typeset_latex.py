"""Host-safe tests for the LaTeX output backend (Phase D).

Pure string transforms (no LaTeX toolchain needed): skeleton + sections, the
abstract environment, special-char escaping, the markdown subset, BibTeX entry
shapes + unique keys, and the optional file writer.
"""

from __future__ import annotations

from app.research.citation import Citation, CitationStatus
from app.research.manuscript import Manuscript, Section
from app.research.typeset_latex import (
    latex_escape,
    manuscript_to_latex,
    references_to_bibtex,
    render_latex,
)


def _ms(*, sections=None, references=None, title="My Paper Title"):
    return Manuscript(
        title=title,
        sections=sections
        if sections is not None
        else [Section(title="Abstract", prose="We study X."), Section(title="Introduction", prose="Background on X.")],
        references=references or [],
    )


# ── .tex skeleton ─────────────────────────────────────────────────────────────


def test_latex_skeleton_and_sections():
    tex = manuscript_to_latex(_ms())
    assert "\\documentclass{article}" in tex
    assert "\\title{My Paper Title}" in tex
    assert "\\begin{document}" in tex and "\\end{document}" in tex
    assert "\\section{Introduction}" in tex


def test_abstract_is_environment_not_section():
    tex = manuscript_to_latex(_ms())
    assert "\\begin{abstract}" in tex and "\\end{abstract}" in tex
    assert "\\section{Abstract}" not in tex


def test_latex_escapes_specials():
    tex = manuscript_to_latex(_ms(sections=[Section(title="Results", prose="50% gain & cost_basis #1")]))
    assert "50\\% gain \\& cost\\_basis \\#1" in tex


def test_markdown_bold_and_italic():
    tex = manuscript_to_latex(_ms(sections=[Section(title="Method", prose="**bold** and *italic*")]))
    assert "\\textbf{bold}" in tex
    assert "\\textit{italic}" in tex


def test_fact_check_warnings_become_comments():
    sec = Section(
        title="Results",
        prose="It was 99% faster.",
        fact_check_warnings=["unverified quantitative token '99%' (absent from the section slice)"],
    )
    tex = manuscript_to_latex(_ms(sections=[sec]))
    assert "% FACT-CHECK: unverified quantitative token" in tex
    assert "It was 99\\% faster." in tex  # prose still escaped


def test_latex_escape_unit():
    assert latex_escape("a & b % c $ d # e _ f { g }") == "a \\& b \\% c \\$ d \\# e \\_ f \\{ g \\}"


# ── BibTeX ─────────────────────────────────────────────────────────────────────


def test_no_bibliography_when_no_references():
    tex = manuscript_to_latex(_ms(references=[]))
    assert "\\bibliography{" not in tex
    assert references_to_bibtex([]) == ""


def test_bibliography_lines_emitted_with_references():
    c = Citation(title="Attention Is All You Need", authors=("Ashish Vaswani",), year=2017, arxiv_id="1706.03762")
    tex = manuscript_to_latex(_ms(references=[c]))
    assert "\\bibliographystyle{plain}" in tex
    assert "\\bibliography{references}" in tex


def test_bibtex_article_with_doi():
    c = Citation(title="ResNet", authors=("Kaiming He",), year=2016, doi="10.1109/cvpr.2016.90")
    bib = references_to_bibtex([c])
    assert "@article{He2016," in bib
    assert "title = {ResNet}" in bib
    assert "author = {Kaiming He}" in bib
    assert "doi = {10.1109/cvpr.2016.90}" in bib


def test_bibtex_misc_for_arxiv_only():
    c = Citation(title="Attention", authors=("Ashish Vaswani",), year=2017, arxiv_id="1706.03762")
    bib = references_to_bibtex([c])
    assert "@misc{Vaswani2017," in bib
    assert "eprint = {1706.03762}" in bib
    assert "archivePrefix = {arXiv}" in bib


def test_bibtex_keys_unique_on_collision():
    c1 = Citation(title="A", authors=("Jane Smith",), year=2020, doi="10.1000/a")
    c2 = Citation(title="B", authors=("John Smith",), year=2020, doi="10.1000/b")
    bib = references_to_bibtex([c1, c2])
    assert "@article{Smith2020," in bib
    assert "@article{Smith2020a," in bib


def test_bibtex_escapes_specials_in_title():
    c = Citation(title="Cost & Risk_2", authors=("A B",), year=2021, doi="10.1000/x")
    bib = references_to_bibtex([c])
    assert "Cost \\& Risk\\_2" in bib


# ── render_latex (file writer) ────────────────────────────────────────────────


def test_render_latex_pure_when_no_output_dir():
    r = render_latex(_ms())
    assert r.tex_path is None and r.bib_path is None
    assert "\\documentclass" in r.tex


def test_render_latex_writes_files(tmp_path):
    c = Citation(title="X", authors=("A B",), year=2021, doi="10.1000/x", status=CitationStatus.VERIFIED)
    r = render_latex(_ms(references=[c]), output_dir=str(tmp_path))
    assert r.tex_path.exists() and r.tex_path.name == "paper.tex"
    assert r.bib_path.exists() and r.bib_path.name == "references.bib"
    assert "@article{B2021" in r.bib_path.read_text(encoding="utf-8")
    assert "\\documentclass{article}" in r.tex_path.read_text(encoding="utf-8")


def test_render_latex_no_bib_file_when_no_references(tmp_path):
    r = render_latex(_ms(references=[]), output_dir=str(tmp_path))
    assert r.tex_path.exists()
    assert r.bib_path is None
    assert not (tmp_path / "references.bib").exists()


# ── Phase C → D end to end (artifacts → composed manuscript → paper.tex + .bib) ─


def test_compose_then_render_produces_a_paper(tmp_path):
    from app.research.manuscript import ResearchArtifacts, compose_manuscript

    arts = ResearchArtifacts(
        question="Is binary search faster than linear scan for membership testing?",
        literature=[{"title": "The Art of Computer Programming, Vol 3", "id": "x"}],
        hypotheses=["Binary search wins above ~1000 elements."],
        findings="Binary search ran in 0.3 ms vs 12 ms for linear scan.",
        measurements="binary_ms=0.3 linear_ms=12",
        citations=[
            Citation(
                title="The Art of Computer Programming, Vol 3",
                authors=("Donald Knuth",),
                year=1998,
                doi="10.1000/taocp",
                status=CitationStatus.VERIFIED,
            )
        ],
    )
    ms = compose_manuscript(arts, llm_call=lambda p: "")  # deterministic fallback path
    r = render_latex(ms, output_dir=str(tmp_path))

    tex = r.tex_path.read_text(encoding="utf-8")
    assert "\\begin{abstract}" in tex
    assert "\\section{Results}" in tex
    assert "\\bibliography{references}" in tex
    assert "@article{Knuth1998," in r.bib_path.read_text(encoding="utf-8")
