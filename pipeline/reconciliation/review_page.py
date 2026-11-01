"""Offline source-evidence review with separate, blinded validation payloads."""

from __future__ import annotations

import copy
import json
import math
import re

from pipeline.triage.review_page import _HTML as BASE_HTML
from pipeline.triage.review_page import _SCRIPT as BASE_SCRIPT
from pipeline.triage.review_page import _STYLE as BASE_STYLE
from pipeline.triage.review_page import _digest, map_data

MODES = {"adjudication", "development", "holdout"}
BLINDED_KEYS = {
    "queue",
    "features",
    "reason_codes",
    "reasons",
    "explanation",
    "assessment",
    "previous_decision",
    "prior",
    "research",
    "recommendation",
    "label",
    "labels",
    "reviewer",
    "notes",
    "human_label",
    "model_label",
    "score",
    "probability",
}
CASE_FIELDS = {
    "pair_id",
    "left_record_id",
    "right_record_id",
    "left",
    "right",
    "distance_km",
    "shared_families",
    "neighbors",
    "neighbor_count",
}


def _replace_once(value: str, before: str, after: str) -> str:
    if value.count(before) != 1:
        raise ValueError("The retained review renderer changed; inspect the adapter")
    return value.replace(before, after)


_RESEARCH_SCRIPT = r"""
function renderResearch(item) {
  const panel = byId("research"); panel.replaceChildren();
  const research = payload.metadata.mode === "holdout" ? null : item.research;
  panel.hidden = !research;
  if (!research) return;
  panel.append(node("h3", "Source evidence"));
  for (const finding of (research.findings || [])) {
    const entry = node("div", undefined, "research-finding");
    entry.append(node("p", finding.claim));
    const citation = node("p", undefined, "help");
    if (safeHttpsUrl(finding.url)) {
      citation.append(externalLink(finding.url, finding.title || "Open cited source"));
    } else { citation.append(node("span", finding.title || "Source link unavailable")); }
    const details = [finding.locator, finding.source_date].filter(Boolean);
    if (details.length) citation.append(document.createTextNode(" · " + details.join(" · ")));
    entry.append(citation); panel.append(entry);
  }
  if (research.recommendation) {
    panel.append(node("h4", "Research proposal — requires your decision"));
    panel.append(node("p", LABEL_NAMES[research.recommendation] ||
      display(research.recommendation)));
    if (research.confidence_reason) panel.append(node("p", research.confidence_reason));
    panel.append(node("p", "This is an agent proposal, not a human label. " +
      "Your new answer starts blank.", "help"));
  }
  if (research.unresolved_questions && research.unresolved_questions.length) {
    panel.append(node("h4", "What remains unresolved"));
    const questions = node("ul");
    for (const question of research.unresolved_questions) questions.append(node("li", question));
    panel.append(questions);
  }
}
"""


def _script() -> str:
    script = re.sub(
        r"function renderAssessment\(item\) \{.*?\n\}\n(?=function renderPrior)",
        _RESEARCH_SCRIPT,
        BASE_SCRIPT,
        count=1,
        flags=re.DOTALL,
    )
    if script == BASE_SCRIPT:
        raise ValueError("The retained assessment renderer changed")
    replacements = [
        (
            'const storageKey = "critical-minerals-atlas:triage-pilot:v2:" + sampleHash;',
            'const storageKey = "critical-minerals-atlas:validation:v1:" + '
            'payload.metadata.mode + ":" + sampleHash;',
        ),
        ('byId("assessment").replaceChildren(renderAssessment(item));', "renderResearch(item);"),
        (
            'anchor.download="atlas-triage-pilot-"+sampleHash.slice(0,12)+"-"+',
            'anchor.download="atlas-validation-"+payload.metadata.mode+"-"+'
            'sampleHash.slice(0,12)+"-"+',
        ),
        ('"Your original decision is preserved"', '"Your previous decision is preserved"'),
        (
            '"This pilot decision is separate. Your original CSV, label and notes " +',
            '"This review decision is separate. Your earlier CSV, label and notes " +',
        ),
        ('"Pilot decision: "', '"Review decision: "'),
        (
            '"New pilot CSV download requested. Original decisions are unchanged."',
            '"New review CSV download requested. Earlier decisions are unchanged."',
        ),
        (
            'byId("pair-position").textContent = "Case " + (index+1) + " of " + cases.length;',
            'byId("pair-position").textContent = "Case " + (index+1) + " of " + cases.length;\n'
            '  byId("case-jump").value = String(index + 1);',
        ),
        (
            'byId("back").addEventListener("click",()=>move(-1));',
            'byId("case-jump").max = String(cases.length);\n'
            'byId("jump").addEventListener("click",()=>{\n'
            '  const value = Number(byId("case-jump").value);\n'
            "  if(Number.isInteger(value) && value >= 1 && value <= cases.length) {\n"
            "    index = value - 1; render(true);\n"
            "  }\n"
            "});\n"
            'byId("back").addEventListener("click",()=>move(-1));',
        ),
    ]
    for before, after in replacements:
        script = _replace_once(script, before, after)
    return script


_SCRIPT = _script()
_STYLE = (
    BASE_STYLE
    + r"""
.research { padding: 22px 26px; background: #edf1eb; overflow-wrap: anywhere; }
.research h3 { margin: 0 0 14px; font-size: 21px; }
.research h4 { margin: 20px 0 10px; font-size: 18px; }
.research-finding + .research-finding { border-top: 1px solid #d7ddd6; padding-top: 14px; }
.research-finding p { margin-bottom: 10px; }
.research-finding .help { font-size: 16px; }
.case-jump { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
.case-jump input { width: 90px; padding: 10px; font-size: 18px; }
.case-jump label { margin: 0; }
.context-grid:has(.context-evidence > [hidden]:first-child):has(.prior[hidden]) {
  grid-template-columns: 1fr;
}
.context-evidence:has(> [hidden]:first-child):has(.prior[hidden]) { display: none; }
@media print { .case-jump { display: none; } }
"""
)


def _html(count: int, mode: str) -> str:
    intro = {
        "adjudication": "Review the cited evidence and record your decision. Earlier answers "
        "remain preserved. Research proposals are separate from your labels.",
        "development": "Review source locations, classifications and cited evidence. Previous "
        "answers, where available, remain preserved. New decisions start blank.",
        "holdout": "Assess each pair from its source records. This independent evaluation view "
        "omits automated routing, previous answers and research proposals.",
    }[mode]
    html = re.sub(
        r'<p class="intro">.*?</p>',
        '<p class="intro">' + intro + "</p>",
        BASE_HTML,
        count=1,
        flags=re.DOTALL,
    )
    html = _replace_once(
        html,
        '<div id="assessment"></div>',
        '<section id="research" class="research" '
        'aria-label="Cited source evidence" hidden></section>',
    )
    html = _replace_once(
        html,
        '<div class="pair-heading">',
        '<div class="case-jump"><label for="case-jump">Go to case</label>'
        f'<input id="case-jump" type="number" min="1" max="{count}" value="1">'
        '<button id="jump" type="button">Go</button></div>'
        '<div class="pair-heading">',
    )
    html = html.replace('max="20"', f'max="{count}"')
    html = html.replace("A more focused review", "Evidence review")
    html = html.replace("focused review pilot", "source evidence review")
    html = html.replace("pilot", "review")
    html = html.replace("Your review review", "Your review")
    html = html.replace("Save your review review", "Save your review")
    html = html.replace(
        "An automated triage suggestion is never a human label.",
        "Choose only a relationship supported by the source evidence.",
    )
    if mode == "holdout":
        html = html.replace(
            "Every review decision starts blank. Previous decisions remain\n"
            "read-only above, and a new answer never overwrites them.",
            "Every decision starts blank. Use the source evidence "
            "to make an independent assessment.",
        )
    return html


def _blind(value):
    if isinstance(value, dict):
        return {key: _blind(item) for key, item in value.items() if key not in BLINDED_KEYS}
    if isinstance(value, list):
        return [_blind(item) for item in value]
    return value


def _validate_research(value) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not isinstance(value.get("findings", []), list):
        raise ValueError("Research evidence must contain a findings list")
    for finding in value.get("findings", []):
        if not isinstance(finding, dict) or not isinstance(finding.get("claim"), str):
            raise ValueError("Each source finding requires a text claim")
        if any(
            key in finding
            and not isinstance(finding[key], str)
            and not (key == "source_date" and finding[key] is None)
            for key in ("url", "title", "locator", "source_date")
        ):
            raise ValueError("Source citation fields must be text")
    questions = value.get("unresolved_questions", [])
    if not isinstance(questions, list) or not all(isinstance(item, str) for item in questions):
        raise ValueError("Unresolved research questions must be text")


def sanitize_metadata(metadata: dict) -> dict:
    """Allow only visible review metadata; never forward selection or answer fields."""
    if not isinstance(metadata, dict) or metadata.get("mode") not in MODES:
        raise ValueError("Review metadata requires adjudication, development or holdout mode")
    if not re.fullmatch(r"[a-f0-9]{64}", str(metadata.get("sample_sha256", ""))):
        raise ValueError("Review metadata requires a lowercase SHA-256 sample digest")
    if "title" in metadata and not isinstance(metadata["title"], str):
        raise ValueError("Review title must be text")
    names = metadata.get("source_names", {})
    if not isinstance(names, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in names.items()
    ):
        raise ValueError("Source names must be a text dictionary")
    return {
        key: copy.deepcopy(metadata[key])
        for key in ("sample_sha256", "mode", "title", "source_names")
        if key in metadata
    }


def sanitize_cases(cases: list[dict], mode: str) -> list[dict]:
    """Copy validated source-display cases, removing assessments and held-out answer history.

    Use the same returned cases in JSON packets and HTML rendering. Map geometry is
    added only by render_review, so the caller can bind the source packet before rendering.
    """
    if mode not in MODES:
        raise ValueError("Review mode must be adjudication, development or holdout")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Review requires between 1 and 200 cases")
    seen_ids, seen_pairs, enriched = set(), set(), []
    for case in cases:
        if not isinstance(case, dict) or any(
            not isinstance(case.get(key), str) or not case[key].strip()
            for key in ("pair_id", "left_record_id", "right_record_id")
        ):
            raise ValueError("Review pair and record IDs must be nonblank strings")
        identity = tuple(sorted((case["left_record_id"], case["right_record_id"])))
        if case["pair_id"] in seen_ids or identity in seen_pairs or identity[0] == identity[1]:
            raise ValueError("Review requires unique pairs of distinct source records")
        seen_ids.add(case["pair_id"])
        seen_pairs.add(identity)
        for side in ("left", "right"):
            if not isinstance(case.get(side), dict) or (
                case[side].get("record_id") != case[f"{side}_record_id"]
            ):
                raise ValueError("Review side records must match the accepted pair IDs")
        distance = case.get("distance_km")
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (int, float))
            or (not math.isfinite(distance) or distance < 0)
        ):
            raise ValueError("Review distance must be finite and nonnegative")
        item = copy.deepcopy({key: value for key, value in case.items() if key in CASE_FIELDS})
        if mode == "holdout":
            item = _blind(item)
        else:
            _validate_research(case.get("research"))
            for key in ("research", "previous_decision"):
                if case.get(key) is not None:
                    item[key] = copy.deepcopy(case[key])
        enriched.append(item)
    return enriched


def render_review(cases: list[dict], metadata: dict) -> bytes:
    """Render 1–200 independent decisions without supplied assessments in held-out payloads."""
    visible_metadata = sanitize_metadata(metadata)
    enriched = sanitize_cases(cases, visible_metadata["mode"])
    for item in enriched:
        item["review_map"] = map_data(item)
    data = (
        json.dumps(
            {"cases": enriched, "metadata": visible_metadata}, ensure_ascii=True, allow_nan=False
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    csp = (
        "default-src 'none'; connect-src 'none'; font-src 'none'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-src 'none'; "
        f"script-src 'sha256-{_digest(_SCRIPT)}'; style-src 'sha256-{_digest(_STYLE)}'"
    )
    replacements = {"__CSP__": csp, "__STYLE__": _STYLE, "__DATA__": data, "__SCRIPT__": _SCRIPT}
    return re.sub(
        r"__(?:CSP|STYLE|DATA|SCRIPT)__",
        lambda match: replacements[match[0]],
        _html(len(cases), metadata["mode"]),
    ).encode()
