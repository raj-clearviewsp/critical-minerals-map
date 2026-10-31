"""Self-contained local pair review; this template contains no private sample data."""

from __future__ import annotations

import base64
import hashlib
import json
import re

_STYLE = r"""
:root { color-scheme: light; color: #263831; background: #fafaf7;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; font-size: 18px; line-height: 1.55; }
header, main, footer { width: min(1240px, calc(100% - 64px)); margin: auto; }
header { padding: 28px 0 22px; border-bottom: 1px solid #d7ddd6; }
.wordmark { margin: 0 0 24px; font-size: 21px; font-weight: 600; }
h1, h2, p { margin-top: 0; }
h1 { font: 400 clamp(32px, 4vw, 46px)/1.15 Georgia, serif; margin-bottom: 18px;
  overflow-wrap: anywhere; }
h2 { font-size: 24px; line-height: 1.25; }
.intro { max-width: 890px; margin-bottom: 16px; color: #56645d; }
summary { cursor: pointer; width: fit-content; padding: 5px 0; }
details p { max-width: 950px; margin: 12px 0; }
a { color: #315c49; text-underline-offset: 4px; }
button, input, textarea { font: inherit; }
button { cursor: pointer; color: #263831; background: white; padding: 11px 18px;
  border: 1px solid #9caea1; border-radius: 3px; min-height: 48px; }
button:hover { background: #edf1eb; }
button:disabled { cursor: default; opacity: .5; }
button.primary { background: #315c49; color: white; border-color: #315c49; }
button.primary:hover { background: #254936; }
:focus-visible { outline: 3px solid #315c49; outline-offset: 4px; }
.skip { position: absolute; top: -100px; left: 24px; background: white; padding: 12px; }
.skip:focus { top: 12px; }
.toolbar { display: flex; align-items: center; justify-content: space-between;
  gap: 18px 28px; flex-wrap: wrap; padding: 26px 0 18px; }
.toolbar p { margin: 0; }
nav { display: flex; flex-wrap: wrap; gap: 10px; }
.progress-wrap { flex: 1; min-width: 220px; max-width: 470px; }
progress { display: block; width: 100%; height: 8px; margin-top: 12px;
  border: 0; border-radius: 0; accent-color: #315c49; }
progress::-webkit-progress-bar { background: #e1e6df; }
progress::-webkit-progress-value { background: #315c49; }
progress::-moz-progress-bar { background: #315c49; }
.pair-heading { display: flex; justify-content: space-between; gap: 14px;
  flex-wrap: wrap; padding: 14px 0 18px; }
.pair-heading h2 { margin: 0; font-weight: 500; font-size: 22px; }
.pair-heading p { margin: 0; color: #56645d; font-size: 16px;
  overflow-wrap: anywhere; max-width: 100%; }
.pair-context { color: #56645d; margin: 0 0 20px; font-size: 16px; }
.comparison { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; }
.record { padding: 28px; border: 1px solid #d7ddd6; background: white; min-width: 0; }
.record h2 { font: 400 29px/1.2 Georgia, serif; overflow-wrap: anywhere; margin-bottom: 10px; }
.record .source { margin-bottom: 20px; color: #56645d; font-size: 16px; }
dl { margin: 0; }
dt { font-weight: 600; margin-top: 18px; font-size: 16px; }
dd { margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.commodity-list { list-style: none; margin: 0; padding: 0; }
.commodity-list li + li { margin-top: 12px; }
.commodity-detail, .overflow-note { font-size: 16px; color: #56645d; }
.overflow-note { margin: 14px 0 0; }
.record details { border-top: 1px solid #d7ddd6; margin-top: 24px; padding-top: 12px;
  font-size: 16px; }
.record details dt { font-size: 16px; }
.decision { padding: 28px 0; }
fieldset { border: 0; margin: 0; padding: 0; }
legend { font-size: 23px; font-weight: 500; margin-bottom: 8px; }
.help { color: #56645d; font-size: 16px; margin-bottom: 20px; }
.choices { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.choice { display: flex; align-items: center; gap: 12px; padding: 16px 20px;
  border: 1px solid #9caea1; border-radius: 3px; background: white; cursor: pointer; }
.choice:has(input:checked) { background: #e7eee6; border-color: #315c49; }
input[type=radio] { width: 21px; height: 21px; accent-color: #315c49; flex-shrink: 0; }
.notes-label { display: block; margin: 24px 0 8px; }
textarea, input[type=text] { display: block; width: 100%; border: 1px solid #9caea1;
  padding: 12px 14px; border-radius: 3px; background: white; color: #263831; }
textarea { min-height: 110px; resize: vertical; }
.decision-tools { display: flex; justify-content: space-between; flex-wrap: wrap;
  gap: 12px; margin-top: 14px; }
.export { border-top: 1px solid #d7ddd6; padding: 26px 0; display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(270px, 1fr); gap: 28px; }
.export label { display: block; margin-bottom: 8px; }
.export-actions { align-self: end; }
.export-actions p { margin: 12px 0 0; font-size: 16px; color: #56645d; }
#save-status { min-height: 26px; margin: 0 0 16px; font-size: 16px; color: #56645d; }
footer { border-top: 1px solid #d7ddd6; padding: 22px 0 36px; font-size: 16px; }
footer p { margin: 8px 0; }
.hash { overflow-wrap: anywhere; }
.print-only { display: none; }
[hidden] { display: none !important; }
@media (max-width: 780px) {
  header, main, footer { width: calc(100% - 36px); }
  header { padding-top: 20px; }
  .wordmark { margin-bottom: 20px; }
  .comparison, .export { grid-template-columns: 1fr; gap: 20px; }
  .record { padding: 22px; }
  .choices { grid-template-columns: 1fr; gap: 10px; }
  .toolbar { align-items: stretch; }
  .progress-wrap { max-width: none; }
  nav { width: 100%; }
  nav button { flex: 1; }
}
@media print {
  @page { size: A4; margin: 15mm; }
  body { background: white; font-size: 11pt; color: black; }
  header, main, footer { width: 100%; }
  header { padding: 0 0 14px; }
  h1 { font-size: 25pt; }
  .wordmark { margin-bottom: 14px; font-size: 13pt; }
  .skip, .toolbar, .decision, .export, #save-status, header details, footer details {
    display: none; }
  .comparison { gap: 16px; grid-template-columns: 1fr 1fr; }
  .record { padding: 16px; }
  .record h2 { font-size: 19pt; }
  .record details, .record .source, dt, .pair-context, .pair-heading p, footer { font-size: 10pt; }
  .record, .print-only { break-inside: avoid; }
  .print-only { display: block; margin-top: 24px; white-space: pre-wrap; }
  footer { padding-bottom: 0; }
}
"""

_SCRIPT = r"""
"use strict";
const LABELS = new Set(["same_site", "different_sites", "uncertain"]);
function safeHttpsUrl(value) {
  if (typeof value !== "string" || !/^https:\/\//i.test(value) || /[\u0000-\u0020]/.test(value)) {
    return null;
  }
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function csvCell(value) {
  let text = String(value ?? "");
  if (/^\s*[=+\-@]/u.test(text) || /^[\t\r]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
function buildCsv(pairs, decisions, reviewer, sampleHash) {
  const rows = [["pair_id", "left_record_id", "right_record_id", "label",
    "reviewer", "notes", "sample_sha256"]];
  for (const pair of pairs) {
    const decision = decisions[pair.pair_id] || {};
    rows.push([pair.pair_id, pair.left.record_id, pair.right.record_id,
      LABELS.has(decision.label) ? decision.label : "", reviewer,
      typeof decision.notes === "string" ? decision.notes : "", sampleHash]);
  }
  return "\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
function restoreState(pairs, saved, sampleHash) {
  const state = {reviewer: "", decisions: Object.create(null)};
  const valid = saved && saved.schema_version === 1 && saved.sample_sha256 === sampleHash;
  if (valid && typeof saved.reviewer === "string") state.reviewer = saved.reviewer.slice(0, 120);
  for (const pair of pairs) {
    const old = valid && saved.decisions && Object.hasOwn(saved.decisions, pair.pair_id)
      ? saved.decisions[pair.pair_id] : null;
    state.decisions[pair.pair_id] = {
      label: old && LABELS.has(old.label) ? old.label : "",
      notes: old && typeof old.notes === "string" ? old.notes.slice(0, 5000) : ""
    };
  }
  return state;
}
function deepFreeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(deepFreeze);
    Object.freeze(value);
  }
  return value;
}
function compactCommodities(record, limit = 6) {
  const entries = Array.isArray(record.pilot_evidence) ? record.pilot_evidence : [];
  const items = [];
  const seen = new Set();
  for (const entry of entries) {
    if (!entry || typeof entry.raw_value !== "string" || !entry.raw_value.trim()) continue;
    const term = entry.raw_value.trim().replace(/\s+/g, " ");
    const role = typeof entry.role === "string" ? entry.role : "unspecified";
    const codes = Array.isArray(entry.codes) ? [...new Set(entry.codes)].sort() : [];
    const qualified = entry.qualified === true;
    const identity = JSON.stringify([term.toLocaleLowerCase(), role, codes, qualified]);
    if (seen.has(identity)) continue;
    seen.add(identity);
    items.push({term, role, codes, qualified, truncated: term.length > 140});
  }
  return {items: items.slice(0, limit), remaining: Math.max(0, items.length - limit)};
}
function countrySummary(record) {
  if (Array.isArray(record.country_iso3s) && record.country_iso3s.length) {
    return record.country_iso3s.join("; ");
  }
  return typeof record.country === "string" && record.country.trim()
    ? record.country + " (as reported; ISO country unresolved)" : "Not supplied";
}
function precisionSummary(record) {
  const labels = {exact: "Exact (as reported)", approximate: "Approximate (as reported)",
    district: "District level (as reported)", "country centroid": "Country centroid (as reported)"};
  if (Object.hasOwn(labels, record.location_precision)) return labels[record.location_precision];
  const policy = typeof record.precision_policy === "string" ? record.precision_policy : "";
  const declared = policy.match(/^source-reported (exact|approximate|district) precision;/);
  return declared ? labels[declared[1]] : "Not stated by source";
}
/* Browser application. */
const payload = deepFreeze(JSON.parse(document.getElementById("review-data").textContent));
const pairs = payload.pairs;
const sampleHash = payload.metadata.sample_sha256;
const storageKey = "critical-minerals-atlas:pair-review:v1:" + sampleHash;
const byId = id => document.getElementById(id);
let saved = null;
let storageAvailable = true;
let statusMessage = "Progress is saved in this browser. Export a CSV to keep a separate copy.";
try {
  const text = localStorage.getItem(storageKey);
  if (text) saved = JSON.parse(text);
} catch {
  storageAvailable = false;
  statusMessage = "Saved progress is unavailable here. Export before closing this page.";
}
const state = restoreState(pairs, saved, sampleHash);
let index = 0;
const sourceNames = payload.metadata.source_names || {};
const labelNames = {same_site: "Same site", different_sites: "Different sites",
  uncertain: "Uncertain"};
byId("reviewer").value = state.reviewer;
byId("sample-hash").textContent = sampleHash;
byId("save-status").textContent = statusMessage;
if (payload.metadata.title) byId("page-title").textContent = payload.metadata.title;
function display(value) {
  if (value === null || value === undefined || value === "") return "Not supplied";
  if (Array.isArray(value)) return value.length ? value.map(display).join("; ") : "Not supplied";
  if (typeof value === "object") {
    const values = Object.entries(value).filter(([, item]) => item !== null && item !== "");
    return values.length ? values.map(([key, item]) => key + ": " + display(item)).join("\n")
      : "Not supplied";
  }
  return String(value);
}
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function fact(list, label, value) {
  list.append(node("dt", label), node("dd", display(value)));
}
function commodityFacts(list, record) {
  const summary = compactCommodities(record);
  const value = node("dd");
  const terms = node("ul", undefined, "commodity-list");
  const roles = {primary: "Primary", secondary: "Secondary", tertiary: "Tertiary",
    byproduct: "Byproduct as reported", collateral: "Collateral, not an asset commodity",
    unspecified: "Role not specified"};
  for (const entry of summary.items) {
    const item = node("li");
    item.append(node("div", entry.truncated ? entry.term.slice(0, 139) + "…" : entry.term));
    const detail = [roles[entry.role] || "Role not specified"];
    if (entry.codes.length) detail.push("Codes: " + entry.codes.join(", "));
    if (entry.qualified) detail.push("Qualified in source");
    item.append(node("div", detail.join(" · "), "commodity-detail"));
    terms.append(item);
  }
  if (summary.items.length) value.append(terms);
  else value.textContent = "See Source details for reported commodities.";
  if (summary.remaining || summary.items.some(item => item.truncated)) {
    const text = summary.remaining ? summary.remaining + " more source terms in Source details."
      : "Full source wording is in Source details.";
    value.append(node("p", text, "overflow-note"));
  }
  list.append(node("dt", "Commodities as reported"), value);
}
function renderRecord(record, side) {
  const article = node("article", undefined, "record");
  article.setAttribute("aria-label", side + " source record");
  article.append(node("h2", record.name || "Name not supplied"));
  const source = sourceNames[record.source_id] || record.source_id;
  article.append(node("p", display(source), "source"));
  const facts = node("dl");
  fact(facts, "Other names", record.aliases);
  fact(facts, "Record type", record.record_kind);
  fact(facts, "Country", countrySummary(record));
  const hasPoint = Number.isFinite(record.lat) && Number.isFinite(record.lon);
  fact(facts, "Coordinates", hasPoint ? record.lat + ", " + record.lon : "No point supplied");
  fact(facts, "Location precision", precisionSummary(record));
  commodityFacts(facts, record);
  fact(facts, "Status as reported", record.raw_status);
  article.append(facts);
  const link = safeHttpsUrl(record.source_url);
  const linkLine = node("p");
  if (link) {
    const anchor = node("a", "Open publisher source");
    anchor.href = link;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.referrerPolicy = "no-referrer";
    linkLine.append(anchor);
  } else linkLine.textContent = "Publisher link unavailable";
  article.append(linkLine);
  const details = node("details");
  details.append(node("summary", "Source details"));
  const provenance = node("dl");
  for (const [label, field] of [["Record ID", "record_id"], ["Source file", "source_file"],
    ["Source table", "source_table"], ["Source row", "source_row"],
    ["Retrieved", "retrieved_at"], ["Evidence grade", "evidence_grade"],
    ["Original country label", "country"], ["Deposit type as reported", "raw_deposit_type"],
    ["Location precision policy", "precision_policy"],
    ["All original commodity fields", "raw_commodities"],
    ["Pilot commodity evidence", "pilot_evidence"], ["References", "reference"]]) {
    fact(provenance, label, record[field]);
  }
  details.append(provenance);
  article.append(details);
  return article;
}
function updateProgress() {
  const decisions = Object.values(state.decisions);
  const reviewed = decisions.filter(item => LABELS.has(item.label)).length;
  const uncertain = decisions.filter(item => item.label === "uncertain").length;
  byId("progress-text").textContent = reviewed + " of " + pairs.length + " reviewed" +
    (uncertain ? " · " + uncertain + " uncertain" : "");
  byId("progress").max = pairs.length;
  byId("progress").value = reviewed;
  byId("export").disabled = !state.reviewer.trim() || reviewed === 0;
  byId("next-unreviewed").disabled = reviewed === pairs.length;
  const decision = state.decisions[pairs[index].pair_id];
  byId("print-decision").textContent = "Decision: " + (labelNames[decision.label] || "Unreviewed") +
    "\nReviewer: " + (state.reviewer || "Not supplied") + "\nNotes: " + (decision.notes || "None");
}
function persist() {
  if (storageAvailable) {
    try {
      localStorage.setItem(storageKey, JSON.stringify({schema_version: 1,
        sample_sha256: sampleHash, reviewer: state.reviewer, decisions: state.decisions}));
    } catch {
      storageAvailable = false;
      byId("save-status").textContent =
        "This browser could not save progress. Export before closing this page.";
    }
  }
  updateProgress();
}
function render(focus = false) {
  const pair = pairs[index];
  byId("pair-position").textContent = "Pair " + (index + 1) + " of " + pairs.length;
  byId("pair-id").textContent = pair.pair_id;
  const distance = Number.isFinite(pair.distance_km) && pair.distance_km >= 0
    ? "Point distance: " +
      pair.distance_km.toLocaleString(undefined, {maximumFractionDigits: 2}) + " km"
    : "Point distance unavailable";
  const familyNames = {Ni: "Nickel", Co: "Cobalt", REE: "Rare earths"};
  const groups = Array.isArray(pair.shared_families)
    ? pair.shared_families.map(value => familyNames[value] || value).join(", ") : "";
  byId("pair-context").textContent = distance +
    (groups ? " · Shared commodity groups: " + groups : "") +
    ". Proximity and shared commodities do not establish site identity.";
  byId("records").replaceChildren(
    renderRecord(pair.left, "Left"), renderRecord(pair.right, "Right"));
  const decision = state.decisions[pair.pair_id];
  for (const radio of document.querySelectorAll('input[name="decision"]')) {
    radio.checked = radio.value === decision.label;
  }
  byId("notes").value = decision.notes;
  byId("back").disabled = index === 0;
  byId("next").disabled = index === pairs.length - 1;
  updateProgress();
  if (focus) byId("pair-position").focus();
}
function move(offset) {
  const next = index + offset;
  if (next >= 0 && next < pairs.length) { index = next; render(true); }
}
byId("back").addEventListener("click", () => move(-1));
byId("next").addEventListener("click", () => move(1));
byId("next-unreviewed").addEventListener("click", () => {
  for (let step = 1; step <= pairs.length; step++) {
    const next = (index + step) % pairs.length;
    if (!state.decisions[pairs[next].pair_id].label) { index = next; render(true); return; }
  }
});
for (const radio of document.querySelectorAll('input[name="decision"]')) {
  radio.addEventListener("change", () => {
    if (radio.checked && LABELS.has(radio.value)) {
      state.decisions[pairs[index].pair_id].label = radio.value;
      persist();
    }
  });
}
byId("notes").addEventListener("input", event => {
  state.decisions[pairs[index].pair_id].notes = event.target.value;
  persist();
});
byId("reviewer").addEventListener("input", event => {
  state.reviewer = event.target.value;
  persist();
});
byId("clear").addEventListener("click", () => {
  state.decisions[pairs[index].pair_id].label = "";
  persist();
  render();
});
byId("export").addEventListener("click", () => {
  if (!state.reviewer.trim() || !Object.values(state.decisions).some(item => item.label)) return;
  const csv = buildCsv(pairs, state.decisions, state.reviewer.trim(), sampleHash);
  const url = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
  const anchor = node("a");
  anchor.href = url;
  const timestamp = new Date().toISOString().replaceAll(":", "-");
  anchor.download = "atlas-pair-review-" + sampleHash.slice(0, 12) + "-" + timestamp + ".csv";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  byId("save-status").textContent =
    "CSV download requested. Your source records and saved decisions are unchanged.";
});
byId("print").addEventListener("click", () => window.print());
render();
"""

_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<title>Critical Minerals Atlas — pair review</title>
<style>__STYLE__</style>
</head>
<body>
<a class="skip" href="#pair-position">Skip to the current pair</a>
<header>
<p class="wordmark">Critical Minerals Atlas</p>
<h1 id="page-title">Review possible site matches</h1>
<p class="intro">Compare the original source records and decide whether they describe the same
physical deposit or mine. New reviews start without decisions.</p>
<details>
<summary>What counts as the same site?</summary>
<p>Choose <strong>Same site</strong> when the evidence identifies the same physical deposit or mine.
Similar names, close coordinates and shared commodities alone are not enough. Nearby deposits,
separate mines, and associated processing facilities can be distinct assets.</p>
<p>Choose <strong>Different sites</strong> when the evidence supports distinct physical assets.
Choose <strong>Uncertain</strong> when the evidence is incomplete, conflicting, or describes a wider
group rather than a specific site. Uncertain pairs require adjudication; they are not counted as
same-site or different-site decisions.</p>
<p>Source status and retrieval dates can be historical. This review labels identity only.</p>
</details>
</header>
<main>
<div class="toolbar">
<div class="progress-wrap">
<p id="progress-text" role="status" aria-live="polite">No decisions yet</p>
<progress id="progress" value="0" max="200" aria-label="Pairs reviewed"></progress>
</div>
<nav aria-label="Review navigation">
<button id="back" type="button">Back</button>
<button id="next" type="button">Next</button>
<button id="next-unreviewed" type="button">Next unreviewed</button>
</nav>
</div>
<div class="pair-heading">
<h2 id="pair-position" tabindex="-1">Current pair</h2>
</div>
<p id="pair-context" class="pair-context"></p>
<section id="records" class="comparison" aria-label="Source record comparison"></section>
<section class="decision" aria-label="Your review">
<fieldset aria-describedby="decision-help">
<legend>Do these records describe the same site?</legend>
<p id="decision-help" class="help">Choose Uncertain if the source evidence does not support a clear
answer. You can change or clear a decision at any time.</p>
<div class="choices">
<label class="choice"><input type="radio" name="decision" value="same_site">Same site</label>
<label class="choice"><input type="radio" name="decision" value="different_sites">
Different sites</label>
<label class="choice"><input type="radio" name="decision" value="uncertain">Uncertain</label>
</div>
</fieldset>
<label class="notes-label" for="notes">Notes <span class="help">(optional)</span></label>
<textarea id="notes" rows="3" maxlength="5000"
  placeholder="Record the evidence or question behind your decision."></textarea>
<div class="decision-tools">
<button id="clear" type="button">Clear decision</button>
<button id="print" type="button">Print this pair</button>
</div>
</section>
<p id="print-decision" class="print-only"></p>
<section class="export" aria-label="Save your review">
<div>
<label for="reviewer">Reviewer name <span class="help">(required for export)</span></label>
<input id="reviewer" type="text" maxlength="120" autocomplete="off">
</div>
<div class="export-actions">
<button id="export" type="button" class="primary" disabled>Export decisions as CSV</button>
<p>You can export a partial review after your first decision. Unreviewed labels remain blank.
Each export creates a new download.</p>
</div>
</section>
<p id="save-status" role="status" aria-live="polite"></p>
<noscript>This local review needs JavaScript to display the source records and save decisions.
No decisions have been supplied or inferred.</noscript>
</main>
<footer>
<p>This is an internal, local review. Decisions stay in this browser until you export them.</p>
<details>
<summary>About this review sample</summary>
<p>Saved progress is specific to this immutable sample. A different sample begins blank.</p>
<p class="hash">Current pair ID: <span id="pair-id"></span></p>
<p class="hash">Sample SHA-256: <span id="sample-hash"></span></p>
</details>
</footer>
<script id="review-data" type="application/json">__DATA__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""


def _digest(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


def render_review(pairs: list[dict], metadata: dict) -> bytes:
    """Render a safe, deterministic local review without modifying input objects.

    Metadata must supply the immutable sample's full ``sample_sha256``. Optional
    ``title`` and ``source_names`` are plain display text. Unknown pair metadata is
    retained in the JSON payload but never turned into suggestions or predictions.
    """
    if not isinstance(metadata, dict) or not re.fullmatch(
        r"[a-f0-9]{64}", str(metadata.get("sample_sha256", ""))
    ):
        raise ValueError("Review metadata requires a lowercase SHA-256 sample digest")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Review requires at least one pair")
    seen = set()
    for pair in pairs:
        if not isinstance(pair, dict) or not isinstance(pair.get("pair_id"), str):
            raise ValueError("Every review pair requires a textual pair_id")
        if not pair["pair_id"].strip() or pair["pair_id"] in seen:
            raise ValueError("Review pair IDs must be nonblank and unique")
        seen.add(pair["pair_id"])
        ids = []
        for side in ("left", "right"):
            record = pair.get(side)
            if not isinstance(record, dict) or not isinstance(record.get("record_id"), str):
                raise ValueError("Each side requires a source record_id")
            if not record["record_id"].strip():
                raise ValueError("Source record IDs must be nonblank")
            ids.append(record["record_id"])
        if ids[0] == ids[1]:
            raise ValueError("A review pair must compare distinct source record IDs")
    if "title" in metadata and not isinstance(metadata["title"], str):
        raise ValueError("Review title must be text")
    names = metadata.get("source_names", {})
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        raise ValueError("Source display names must be a text dictionary")
    data = json.dumps({"pairs": pairs, "metadata": metadata}, ensure_ascii=True, allow_nan=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    csp = (
        "default-src 'none'; connect-src 'none'; font-src 'none'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-src 'none'; "
        f"script-src 'sha256-{_digest(_SCRIPT)}'; style-src 'sha256-{_digest(_STYLE)}'"
    )
    replacements = {"__CSP__": csp, "__STYLE__": _STYLE, "__DATA__": data, "__SCRIPT__": _SCRIPT}
    # A single substitution pass prevents source text containing template tokens
    # from being interpreted as template syntax.
    return re.sub(r"__(?:CSP|STYLE|DATA|SCRIPT)__", lambda m: replacements[m[0]], _HTML).encode()
