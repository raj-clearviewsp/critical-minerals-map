"""Offline approval workspace and a printable, cited research dossier."""

from __future__ import annotations

import copy
import html
import json
import re

from pipeline.reconciliation.review_page import (
    _SCRIPT as BASE_SCRIPT,
)
from pipeline.reconciliation.review_page import (
    _STYLE as BASE_STYLE,
)
from pipeline.reconciliation.review_page import (
    _html,
    _replace_once,
    sanitize_cases,
)
from pipeline.triage.review_page import _digest, map_data

LABEL_NAMES = {
    "same_site": "Same physical site",
    "different_sites": "Distinct sites",
    "left_contains_right": "A contains B",
    "right_contains_left": "B contains A",
    "related_scope_unclear": "Related, but scope is unclear",
    "insufficient_evidence": "Insufficient evidence",
}

_RESEARCH = r"""
function researchList(panel, heading, values) {
  if (!values || !values.length) return;
  panel.append(node("h3", heading));
  const list = node("ul");
  for (const value of values) list.append(node("li", value));
  panel.append(list);
}
function useRecommendation() {
  const proposal = cases[index].research.recommendation;
  if (!LABELS.has(proposal)) return;
  state.decisions[cases[index].pair_id].label = proposal;
  persist(); render();
  byId("approval-status").textContent = "Your decision is now: " + LABEL_NAMES[proposal] +
    ". Export your decisions to retain a separate copy.";
}
function renderResearch(item) {
  const research = item.research;
  const panel = byId("research"); panel.replaceChildren(); panel.hidden = false;
  panel.append(node("h3", "Research recommendation"));
  panel.append(node("p", LABEL_NAMES[research.recommendation], "proposal-label"));
  panel.append(node("p", research.summary));
  panel.append(node("p", research.confidence[0].toUpperCase() + research.confidence.slice(1) +
    " confidence · qualitative assessment, not a measured probability", "help"));
  const answer = node("p", "Your answer: " +
    (LABEL_NAMES[state.decisions[item.pair_id].label] || "Not yet recorded"), "help");
  answer.id = "research-answer"; panel.append(answer);
  const approval = node("button", "Use this recommendation", "primary");
  approval.type = "button"; approval.addEventListener("click", useRecommendation);
  panel.append(approval, node("p", "This fills your answer for this case only. You may choose " +
    "a different relationship below. No answer is selected until you act.", "help"));
  const details = node("a", "Read the complete analysis and citations ↓");
  details.href = "#analysis";
  details.addEventListener("click", event => {
    event.preventDefault(); byId("analysis").scrollIntoView({block:"start"});
  });
  panel.append(details);
  const analysis = byId("analysis"); analysis.replaceChildren();
  analysis.append(node("h2", "Analysis and evidence"));
  const interpretations = node("div", undefined, "research-columns");
  for (const [title, field] of [["Record A", "left_interpretation"],
    ["Record B", "right_interpretation"]]) {
    const section = node("section"); section.append(node("h3", title), node("p", research[field]));
    interpretations.append(section);
  }
  analysis.append(interpretations);
  for (const [title, field] of [["Location and precision", "geographic_analysis"],
    ["Entity scope", "scope_analysis"], ["Minerals and geology", "commodity_geology_analysis"]]) {
    analysis.append(node("h3", title), node("p", research[field]));
  }
  researchList(analysis, "Why this is the best-supported answer", research.rationale);
  researchList(analysis, "Strongest alternative interpretations", research.counterarguments);
  researchList(analysis, "What remains uncertain", research.uncertainties);
  researchList(analysis, "What would change the recommendation", research.what_would_change);
  analysis.append(node("h3", "Independent review"), node("p", research.second_review.note));
  if (research.second_review.verifications?.length) researchList(analysis,
    "Second reviewer checks", research.second_review.verifications);
  if (research.editorial_review) analysis.append(node("p", research.editorial_review));
  analysis.append(node("h3", "Cited evidence"));
  const citations = node("ol", undefined, "citations");
  for (const citation of research.citations) {
    const entry = node("li");
    entry.append(externalLink(citation.url, citation.title), node("p", citation.claim));
    entry.append(node("p", [citation.publisher, citation.source_date || "Date not stated",
      citation.locator, "Access: " + citation.access.replaceAll("_", " ")].join(" · "), "help"));
    if (citation.independence_note) entry.append(node("p", citation.independence_note, "help"));
    citations.append(entry);
  }
  analysis.append(citations);
  byId("approval-status").textContent = "";
}
"""

_SCRIPT = re.sub(
    r"function renderResearch\(item\) \{.*?\n\}\n(?=function renderPrior)",
    _RESEARCH,
    BASE_SCRIPT,
    count=1,
    flags=re.DOTALL,
)
if _SCRIPT == BASE_SCRIPT:
    raise ValueError("Research renderer adapter no longer matches its frozen parent")
_SCRIPT = _replace_once(
    _SCRIPT,
    '"critical-minerals-atlas:validation:v1:"',
    '"critical-minerals-atlas:research60:v1:"',
)
_SCRIPT = _replace_once(
    _SCRIPT,
    'anchor.download="atlas-validation-"+payload.metadata.mode+"-"+',
    'anchor.download="atlas-researched-development60-"+',
)
_SCRIPT = _replace_once(
    _SCRIPT,
    "let index = 0;",
    "let index = Math.max(0, Math.min(cases.length - 1, "
    "Number(location.hash.match(/^#case-(\\d+)$/)?.[1] || 1) - 1));",
)
_SCRIPT = _replace_once(
    _SCRIPT,
    'byId("pair-id").textContent = item.pair_id;',
    'byId("pair-id").textContent = item.pair_id;\n'
    '  history.replaceState(null, "", "#case-" + (index + 1));',
)
_SCRIPT = _replace_once(
    _SCRIPT,
    'byId("print").addEventListener("click",()=>window.print());',
    'byId("approve-bottom").addEventListener("click", useRecommendation);\n'
    'byId("print").addEventListener("click",()=>window.print());',
)
_SCRIPT = _replace_once(
    _SCRIPT,
    "const decision = state.decisions[cases[index].pair_id];",
    "const decision = state.decisions[cases[index].pair_id];\n"
    '  if (byId("research-answer")) byId("research-answer").textContent = "Your answer: " +\n'
    '    (LABEL_NAMES[decision.label] || "Not yet recorded");',
)
_SCRIPT = _replace_once(
    _SCRIPT,
    'item.distance_km.toLocaleString(undefined,{maximumFractionDigits:2}) + " km apart" +',
    "(item.distance_km < 1\n"
    '    ? (item.distance_km * 1000).toLocaleString(undefined,{maximumFractionDigits:1}) + " m"\n'
    '    : item.distance_km.toLocaleString(undefined,{maximumFractionDigits:2}) + " km") +\n'
    '    " apart" +',
)
_STYLE = (
    BASE_STYLE
    + r"""
.research .proposal-label { font: 400 28px/1.3 Georgia, serif; }
.research button { margin: 10px 0 14px; }
.research > .help { margin-top: 12px; }
#analysis { max-width: 1000px; margin: 36px auto; line-height: 1.65; }
#analysis h2 { font: 400 31px/1.3 Georgia, serif; border-top: 1px solid #cbd5c8;
  padding-top: 28px; }
#analysis h3 { font-size: 21px; margin: 26px 0 10px; }
#analysis li + li { margin-top: 14px; }
.research-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }
.citations { padding-left: 28px; }
.citations li { padding: 12px 0; border-top: 1px solid #d7ddd6; overflow-wrap: anywhere; }
.citations p { margin: 8px 0; }
.dossier-link { margin: 20px 0 0; }
#approval-status { margin: 12px 0; color: #315c49; }
@media (max-width: 780px) { .research-columns { grid-template-columns: 1fr; gap: 0; } }
@media print { .research button, #approve-bottom, .dossier-link { display: none; } }
"""
)


def render_workspace(cases: list[dict], sample_hash: str, source_names: dict) -> bytes:
    enriched = sanitize_cases(cases, "development")
    for case in enriched:
        case["review_map"] = map_data(case)
    metadata = {
        "mode": "development",
        "sample_sha256": sample_hash,
        "title": "Sixty comparisons, researched for your decision",
        "source_names": source_names,
    }
    data = json.dumps({"cases": enriched, "metadata": metadata}, ensure_ascii=True, allow_nan=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = _html(len(cases), "development")
    page = _replace_once(
        page,
        "Every review decision starts blank. Previous decisions remain\n"
        "read-only above, and a new answer never overwrites them.",
        "Every decision starts blank. Your earlier decisions remain preserved "
        "in their original review rounds.",
    )
    page = _replace_once(
        page,
        "for two descriptions of one physical deposit or mine.",
        "for two descriptions of the same deposit, mine, or named complex at the "
        "scope stated in the analysis. Shared identity does not make resource or "
        "production coverage interchangeable.",
    )
    page = _replace_once(
        page,
        "Two descriptions of one deposit or mine",
        "One deposit, mine, or named complex at the stated scope",
    )
    page = re.sub(
        r'<p class="intro">.*?</p>',
        '<p class="intro">The first 60 new development comparisons, researched by three agents '
        "and challenged in an independent review pass. Recommendations are proposals for your "
        "approval. Your earlier decisions and the 80-case holdout remain unchanged.</p>"
        '<p class="dossier-link"><a href="analysis.html">Read or print all 60 analyses</a></p>',
        page,
        count=1,
        flags=re.DOTALL,
    )
    page = _replace_once(
        page,
        '<section class="decision" aria-label="Your review">',
        '<section id="analysis" aria-label="Detailed case analysis"></section>\n'
        '<section class="decision" aria-label="Your review">\n'
        '<button id="approve-bottom" type="button" class="primary">Use this recommendation</button>'
        '<p id="approval-status" role="status" aria-live="polite"></p>',
    )
    csp = (
        "default-src 'none'; connect-src 'none'; font-src 'none'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-src 'none'; "
        f"script-src 'sha256-{_digest(_SCRIPT)}'; style-src 'sha256-{_digest(_STYLE)}'"
    )
    replacements = {"__CSP__": csp, "__STYLE__": _STYLE, "__DATA__": data, "__SCRIPT__": _SCRIPT}
    return re.sub(
        r"__(?:CSP|STYLE|DATA|SCRIPT)__", lambda match: replacements[match[0]], page
    ).encode()


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def render_dossier(cases: list[dict], summary: dict) -> bytes:
    """Plain HTML keeps the complete evidence readable, searchable and printable offline."""
    style = """
body{font:18px/1.65 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;
background:#fafaf7;
color:#263831;

max-width:1050px;
margin:40px auto;
padding:0 28px}
h1,h2{font-family:Georgia,serif;
font-weight:400;
line-height:1.25}

h1{font-size:40px}
h2{font-size:30px}
h3{font-size:20px;
margin-top:25px}
a{color:#315c49;
text-underline-offset:3px}

article{border-top:1px solid #cbd5c8;
margin-top:45px;
padding-top:25px}
li+li{margin-top:12px}

.cite{font-size:16px;
color:#56645d}
td,th{padding:10px;
text-align:left;
border-bottom:1px solid #d7ddd6}

table{width:100%;
border-collapse:collapse;
font-size:16px}
blockquote{border-left:3px solid #789078;
padding-left:20px;

margin:20px 0}
 .muted{color:#56645d}
.evidence li{padding-block:12px;
border-top:1px solid #d7ddd6;
overflow-wrap:anywhere}

@media print{body{font-size:11pt;
max-width:none;
margin:0}
article{break-before:page}
h2,h3{break-after:avoid}
}

"""
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="referrer" content="no-referrer">',
        "<title>Atlas — 60 researched comparisons</title>",
        f"<style>{style}</style></head><body><header>"
        "<p>Critical Minerals Atlas · Private review</p>",
        "<h1>Sixty comparisons, researched for your decision</h1>",
        '<p><a href="index.html">Open the approval workspace</a></p>',
        "<p>Every recommendation below is an agent proposal. No new human decisions have been "
        "recorded. The batch retains the first 60 cases in the frozen development order. "
        "The remaining 27 development and 80 holdout cases are unchanged.</p>",
        "<p>High, medium and low confidence are qualitative judgments about the proposed "
        "relationship, not calibrated probabilities. A confident recommendation to defer a "
        "case is not a claim that its physical identity has been resolved.</p>",
        "<p>Distance is between source points whose precision may be unknown. Shared minerals, "
        "owners, districts and coordinates do not by themselves establish identity. Copies of "
        "the same upstream record are not independent corroboration.</p>",
        f"<p>{_e(summary['citation_count'])} citation entries; "
        f"{_e(summary['independently_reviewed'])} cases received a second review.</p></header>",
        "<table><thead><tr><th>Case</th><th>Comparison</th><th>Recommendation</th>"
        "<th>Confidence</th></tr></thead><tbody>",
    ]
    for number, case in enumerate(cases, 1):
        research = case["research"]
        title = f"{case['left']['display_name']} / {case['right']['display_name']}"
        parts.append(
            f'<tr><td>{number}</td><td><a href="#case-{number}">{_e(title)}</a></td>'
            f"<td>{_e(LABEL_NAMES[research['recommendation']])}</td>"
            f"<td>{_e(research['confidence'])}</td></tr>"
        )
    parts.append("</tbody></table>")
    for number, case in enumerate(cases, 1):
        research = copy.deepcopy(case["research"])
        title = f"{case['left']['display_name']} / {case['right']['display_name']}"
        parts.extend(
            [
                f'<article id="case-{number}"><h2>{number}. {_e(title)}</h2>',
                f"<p><strong>{_e(LABEL_NAMES[research['recommendation']])}</strong> · "
                f"{_e(research['confidence'])} confidence · {case['distance_km']:.3f} km "
                "between source points</p>",
                f"<blockquote>{_e(research['summary'])}</blockquote>",
                f'<p><a href="index.html#case-{number}">Approve or revise this case</a></p>',
            ]
        )
        for title, field in [
            ("Record A", "left_interpretation"),
            ("Record B", "right_interpretation"),
            ("Location and precision", "geographic_analysis"),
            ("Entity scope", "scope_analysis"),
            ("Minerals and geology", "commodity_geology_analysis"),
        ]:
            parts.append(f"<h3>{_e(title)}</h3><p>{_e(research[field])}</p>")
        for title, field in [
            ("Why this answer", "rationale"),
            ("Strongest alternatives", "counterarguments"),
            ("What remains uncertain", "uncertainties"),
            ("What would change the recommendation", "what_would_change"),
        ]:
            if research[field]:
                parts.append(
                    f"<h3>{_e(title)}</h3><ul>"
                    + "".join(f"<li>{_e(value)}</li>" for value in research[field])
                    + "</ul>"
                )
        second = research["second_review"]
        parts.append(f"<h3>Independent review</h3><p>{_e(second['note'])}</p>")
        if second.get("verifications"):
            parts.append(
                "<ul>" + "".join(f"<li>{_e(v)}</li>" for v in second["verifications"]) + "</ul>"
            )
        if research.get("editorial_review"):
            parts.append(f"<p>{_e(research['editorial_review'])}</p>")
        parts.append('<h3>Cited evidence</h3><ol class="evidence">')
        for citation in research["citations"]:
            parts.append(
                f'<li><a href="{_e(citation["url"])}" target="_blank" rel="noopener noreferrer">'
                f'{_e(citation["title"])}</a><p>{_e(citation["claim"])}</p><p class="cite">'
                + _e(
                    " · ".join(
                        [
                            citation["publisher"],
                            citation["source_date"] or "Date not stated",
                            citation["locator"],
                            "Access: " + citation["access"].replace("_", " "),
                        ]
                    )
                )
                + f'</p><p class="cite">{_e(citation["independence_note"])}</p></li>'
            )
        parts.append(
            "</ol><details><summary>Exact source identities</summary><ul>"
            + "".join(f"<li>{_e(case[side]['record_id'])}</li>" for side in ["left", "right"])
            + f"</ul><p>{_e(case['pair_id'])}</p></details></article>"
        )
    parts.append("</body></html>")
    return "\n".join(parts).encode()
