"""Adapt immutable prior renderers without mixing research into blind payloads."""

import json
import re

from pipeline.reconciliation.review_page import _SCRIPT as BLIND_SCRIPT
from pipeline.reconciliation.review_page import _replace_once, render_review
from pipeline.research_review.review_page import _SCRIPT as RESEARCH_SCRIPT
from pipeline.research_review.review_page import render_dossier, render_workspace
from pipeline.triage.review_page import _digest


def _local_notice(page: str) -> str:
    return _replace_once(
        page,
        "Private local review. No decisions or source data are sent to a server.",
        "Decisions save locally; nothing is uploaded automatically. Optional source links "
        "open external websites, and the external map link includes the displayed coordinates.",
    )


def _adapt_script(page: str, original: str, modified: str) -> str:
    page = _replace_once(page, original, modified)
    return _replace_once(page, _digest(original), _digest(modified))


def development_page(cases: list, sample_hash: str, names: dict) -> bytes:
    page = render_workspace(cases, sample_hash, names).decode()
    page = _local_notice(page)
    page = _replace_once(
        page,
        "scope stated in the analysis.",
        "scope documented by the source records.",
    )
    page = re.sub(
        r"<title>.*?</title>", "<title>Atlas — 27 researched cases</title>", page, count=1
    )
    match = re.search(r'(<script id="review-data" type="application/json">)(.*?)(</script>)', page)
    payload = json.loads(match[2])
    payload["metadata"]["title"] = "The remaining 27 development comparisons"
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = page[: match.start(2)] + encoded + page[match.end(2) :]
    page = re.sub(
        r'<p class="intro">.*?</p>',
        '<p class="intro">Your first 60 recommendations are accepted. These are the remaining '
        "27 development cases, each researched and challenged by a second agent. "
        "Your new decisions start blank.</p>",
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = _replace_once(page, "Read or print all 60 analyses", "Read or print all 27 analyses")
    page = _replace_once(
        page,
        '<a href="analysis.html">',
        '<a href="index.html">Review overview</a> · <a href="analysis.html">',
    )
    script = RESEARCH_SCRIPT.replace(
        "critical-minerals-atlas:research60:v1:", "critical-minerals-atlas:next60:development:v1:"
    ).replace("atlas-researched-development60-", "atlas-next60-development-")
    return _adapt_script(page, RESEARCH_SCRIPT, script).encode()


def holdout_page(cases: list, sample_hash: str, names: dict) -> bytes:
    page = render_review(
        cases,
        {
            "mode": "holdout",
            "sample_sha256": sample_hash,
            "source_names": names,
            "title": "33 blind comparisons",
        },
    ).decode()
    page = _local_notice(page)
    page = re.sub(r"<title>.*?</title>", "<title>Atlas — 33 blind cases</title>", page, count=1)
    page = _replace_once(
        page,
        "for two descriptions of one physical deposit or mine.",
        "for two descriptions of the same deposit, mine, or named complex at the "
        "scope documented by the source records. Shared identity does not make resource or "
        "production coverage interchangeable.",
    )
    page = _replace_once(
        page,
        "Two descriptions of one deposit or mine",
        "One deposit, mine, or named complex at the stated scope",
    )
    page = _replace_once(
        page,
        "</header>",
        '<p><a href="index.html">Review overview</a></p></header>',
    )
    script = BLIND_SCRIPT.replace(
        "critical-minerals-atlas:validation:v1:", "critical-minerals-atlas:next60:blind:v1:"
    ).replace("atlas-validation-", "atlas-next60-")
    return _adapt_script(page, BLIND_SCRIPT, script).encode()


def analysis_page(cases: list, summary: dict) -> bytes:
    page = render_dossier(cases, summary).decode()
    start, end = page.index("<header>"), page.index("</header>") + len("</header>")
    header = f"""<header><p>Critical Minerals Atlas · Private review</p>
<h1>The remaining 27 development comparisons</h1>
<p><a href="development.html">Open the approval workspace</a> ·
<a href="index.html">Review overview</a></p>
<p>Three agents researched nine cases each, then a different agent challenged each case.
These are proposals for your decision. Your approved first 60 remain preserved.</p>
<p>Confidence is a qualitative assessment, not a measured probability. Source-point
distance does not establish accuracy or entity boundaries. Shared source ancestry
is disclosed. The separate 33-case holdout has no recommendations.</p>
<p>{summary["citation_count"]} citation entries; all 27 cases independently reviewed.</p>
</header>"""
    page = page[:start] + header + page[end:]
    return (
        page.replace("Atlas — 60 researched comparisons", "Atlas — 27 researched comparisons")
        .replace('href="index.html#case-', 'href="development.html#case-')
        .encode()
    )


def index_page() -> bytes:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Critical Minerals Atlas — next 60 comparisons</title>
<style>
body{font:19px/1.65 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;color:#263831;
background:#fafaf7;max-width:1000px;margin:52px auto;padding:0 28px}
h1,h2{font-family:Georgia,serif;font-weight:400;line-height:1.2}h1{font-size:44px}
h2{font-size:30px}a{color:#315c49;text-underline-offset:4px}
.parts{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:36px}
section{padding:25px;border:1px solid #cbd5c8;background:#fff}section p{margin:14px 0}
.button{display:inline-block;background:#315c49;color:#fff;padding:12px 20px;
text-decoration:none;border-radius:3px}footer{margin-top:38px;border-top:1px solid #cbd5c8}
@media(max-width:700px){.parts{grid-template-columns:1fr}h1{font-size:36px}body{margin:28px auto}}
</style></head><body><header><p>Critical Minerals Atlas · Private review</p>
<h1>Your next 60 comparisons</h1>
<p>Your approval of the first 60 recommendations is recorded. This batch completes
the remaining 27 development cases and begins the blind holdout with 33 cases.</p></header>
<div class="parts"><section><h2>27 researched cases</h2>
<p>Each comparison includes a recommendation, detailed evidence, alternatives and a
second agent’s review. Approve the recommendation or choose another relationship.</p>
<p><a class="button" href="development.html">Review researched cases</a></p>
<p><a href="analysis.html">Read or print the complete analysis</a></p></section>
<section><h2>33 blind cases</h2><p>Assess the original source records, locations,
classifications and references. These cases contain no agent recommendations,
previous answers or automated scores.</p>
<p><a class="button" href="holdout.html">Review blind cases</a></p></section></div>
<p>Each section starts blank and saves its own progress. Enter your reviewer name
and export a CSV from each section. Export partial progress regularly; browser
storage alone is not a backup. An unresolved answer is valid when evidence is missing.</p>
<p>After completing both sections, 47 new holdout cases will remain. The 33 existing
unresolved relationships stay recorded as unresolved; no new research is required
from you to accept that evidence limit.</p>
<footer><p>Decisions save locally; nothing is uploaded automatically. Optional source
links open external websites, and external map links include the displayed coordinates.
The two sections preserve the development/holdout boundary.</p></footer></body></html>
""".encode()
