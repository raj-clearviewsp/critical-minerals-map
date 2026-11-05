import json
import re

from pipeline.reconciliation.review_page import _replace_once
from pipeline.research_review.review_page import _SCRIPT, render_dossier, render_workspace
from pipeline.review_next.pages import _adapt_script, _local_notice


def workspace(cases: list, sample_hash: str, names: dict) -> bytes:
    page = _local_notice(render_workspace(cases, sample_hash, names).decode())
    page = _replace_once(
        page, "scope stated in the analysis.", "scope documented by the source records."
    )
    page = re.sub(
        r"<title>.*?</title>", "<title>Atlas — final 47 researched cases</title>", page, count=1
    )
    page = re.sub(
        r'<p class="intro">.*?</p>',
        '<p class="intro">The final 47 comparisons, researched by three agents and challenged '
        "by a second reviewer. At your request, these cases are now agent-assisted and are no "
        "longer a blind evaluation set. Recommendations are proposals; "
        "your answers start blank.</p>",
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = _replace_once(page, "Read or print all 60 analyses", "Read or print all 47 analyses")
    match = re.search(r'(<script id="review-data" type="application/json">)(.*?)(</script>)', page)
    payload = json.loads(match[2])
    payload["metadata"]["title"] = "The final 47 comparisons"
    payload["metadata"]["mode"] = "adjudication"
    payload["metadata"]["review_role"] = "agent_assisted_audit_not_blind"
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = page[: match.start(2)] + encoded + page[match.end(2) :]
    script = _SCRIPT.replace(
        "critical-minerals-atlas:research60:v1:", "critical-minerals-atlas:final47:assisted:v1:"
    ).replace("atlas-researched-development60-", "atlas-final47-assisted-")
    return _adapt_script(page, _SCRIPT, script).encode()


def dossier(cases: list, summary: dict) -> bytes:
    page = render_dossier(cases, summary).decode()
    start, end = page.index("<header>"), page.index("</header>") + len("</header>")
    header = f"""<header><p>Critical Minerals Atlas · Private review</p>
<h1>The final 47 comparisons</h1><p><a href="index.html">Open the approval workspace</a></p>
<p>Every case has primary-source research and a separate second review. Recommendations
remain proposals for your approval. The original sources, earlier answers and submitted
27-case and 33-case exports are preserved.</p>
<p>At your request these 47 are now agent-assisted, not a blind human holdout. They will
not be used to fit the frozen experimental matcher. Agreement with these recommendations
is reported separately from its evaluation against your 33 blind human answers.</p>
<p>Confidence is qualitative. Source points do not define boundaries or known accuracy.
Copies of one underlying source are not independent corroboration.</p>
<p>{summary["citation_count"]} citation entries;
{summary["independently_reviewed"]} second reviews.</p>
</header>"""
    page = page[:start] + header + page[end:]
    return page.replace(
        "Atlas — 60 researched comparisons", "Atlas — final 47 researched comparisons"
    ).encode()
