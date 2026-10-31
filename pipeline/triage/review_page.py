"""Private, offline review pilot with geography, source scope and preserved decisions."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re

from pipeline.link.review_page import _STYLE as BASE_STYLE

EARTH_RADIUS_KM = 6371.0088


def _point(record: dict) -> tuple[float, float] | None:
    lat, lon = record.get("lat"), record.get("lon")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in (lat, lon)):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180):
        return None
    return float(lat), float(lon)


def _project(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Spherical azimuthal-equidistant km east/north of the stated map origin."""
    phi, phi0 = math.radians(lat), math.radians(lat0)
    delta = math.radians((lon - lon0 + 180) % 360 - 180)
    cos_c = math.sin(phi0) * math.sin(phi) + math.cos(phi0) * math.cos(phi) * math.cos(delta)
    c = math.acos(max(-1.0, min(1.0, cos_c)))
    if c < 1e-10:
        return 0.0, 0.0
    if math.pi - c < 1e-7:
        raise ValueError("Antipodal points cannot share a local reference map")
    k = c / math.sin(c)
    return (
        EARTH_RADIUS_KM * k * math.cos(phi) * math.sin(delta),
        EARTH_RADIUS_KM
        * k
        * (math.cos(phi0) * math.sin(phi) - math.sin(phi0) * math.cos(phi) * math.cos(delta)),
    )


def map_data(case: dict) -> dict:
    """Build deterministic local map geometry, preserving every displayed source point.

    The map is descriptive, not a location-quality assessment. Exact coordinate
    duplicates share one marker; no jitter or invented precision is introduced.
    Distant/invalid neighbors are omitted and counted. Pair points are mandatory.
    """
    a, b = _point(case["left"]), _point(case["right"])
    if a is None or b is None:
        return {"available": False, "reason": "A source point is unavailable."}
    # Spherical midpoint avoids the longitude seam; for the impossible local
    # antipodal case, fail explicitly rather than invent a map.
    vectors = []
    for lat, lon in (a, b):
        phi, lam = math.radians(lat), math.radians(lon)
        vectors.append(
            (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))
        )
    x, y, z = (sum(v[i] for v in vectors) for i in range(3))
    if math.sqrt(x * x + y * y + z * z) < 1e-8:
        return {"available": False, "reason": "Pair points are too far apart for a local map."}
    lat0 = math.degrees(math.atan2(z, math.hypot(x, y)))
    lon0 = math.degrees(math.atan2(y, x))
    records = [("A", case["left"]), ("B", case["right"])]
    seen = {case["left"]["record_id"], case["right"]["record_id"]}
    for record in sorted(case.get("neighbors", []), key=lambda row: row["record_id"]):
        if record["record_id"] not in seen:
            records.append(("", record))
            seen.add(record["record_id"])
    points, omitted = [], 0
    for label, record in records:
        coordinate = _point(record)
        if coordinate is None:
            omitted += 1
            continue
        try:
            east, north = _project(*coordinate, lat0, lon0)
        except ValueError:
            if label:
                return {
                    "available": False,
                    "reason": "Pair points are too far apart for a local map.",
                }
            omitted += 1
            continue
        if not label and math.hypot(east, north) > 25:
            omitted += 1
            continue
        points.append(
            {
                "label": label,
                "record_id": record["record_id"],
                "name": record.get("display_name")
                or record.get("primary_name")
                or "Name not supplied",
                "source_id": record.get("source_id"),
                "entity_level": record.get("entity_level"),
                "lat": coordinate[0],
                "lon": coordinate[1],
                "east_km": east,
                "north_km": north,
            }
        )
    if any(math.hypot(p["east_km"], p["north_km"]) > 100 for p in points if p["label"]):
        return {"available": False, "reason": "Pair points are too far apart for a local map."}
    # A common metric scale on both axes prevents visual distance distortion.
    half_x = max(0.35, *(abs(p["east_km"]) for p in points)) * 1.2
    half_y = max(0.15, *(abs(p["north_km"]) for p in points)) * 1.2
    scale = min(700 / (2 * half_x), 260 / (2 * half_y))
    extent_x, extent_y = 350 / scale, 130 / scale
    groups = {}
    for point in points:
        point["x"] = 400 + scale * point["east_km"]
        point["y"] = 170 - scale * point["north_km"]
        groups.setdefault((point["lat"], point["lon"]), []).append(point)
    markers = []
    for members in groups.values():
        pair_labels = [point["label"] for point in members if point["label"]]
        markers.append(
            {
                "x": members[0]["x"],
                "y": members[0]["y"],
                "count": len(members),
                "label": "/".join(pair_labels),
                "members": members,
            }
        )
    target = extent_x / 2
    power = 10 ** math.floor(math.log10(target))
    scale_km = max(v * power for v in (1, 2, 5) if v * power <= target)
    return {
        "available": True,
        "origin": {"lat": lat0, "lon": lon0},
        "projection": "local azimuthal-equidistant",
        "extent_east_km": extent_x,
        "extent_north_km": extent_y,
        "scale_km": scale_km,
        "scale_px": scale_km * scale,
        "points": points,
        "markers": markers,
        "omitted_neighbors": omitted,
        "polar_orientation": abs(lat0) > 89.99,
    }


_STYLE = (
    BASE_STYLE
    + r"""
header { padding: 22px 0 18px; }
.wordmark { margin-bottom: 16px; }
h1 { font-size: 36px; margin-bottom: 12px; }
.intro { max-width: 980px; }
.toolbar { position: sticky; top: 0; background: #fafaf7; z-index: 2;
  border-bottom: 1px solid #d7ddd6; padding: 16px 0; }
.case-title { font: 400 32px/1.25 Georgia, serif; margin: 10px 0 10px; }
.context-line { color: #56645d; margin-bottom: 18px; }
.pair-heading { padding-bottom: 0; }
.context-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr);
  gap: 24px; align-items: start; margin-bottom: 24px; }
.context-grid > *, .context-evidence { min-width: 0; }
.context-evidence .assessment { margin-bottom: 0; }
.context-evidence .prior { margin-top: 18px; }
.context-grid .map-panel { margin-bottom: 0; }
.assessment { padding: 22px 26px; background: #edf1eb; margin: 0 0 24px; }
.assessment h3, .prior h3 { margin: 0 0 10px; font-size: 20px; }
.assessment p, .prior p { margin-bottom: 10px; }
.assessment ul { margin: 10px 0 0; padding-left: 24px; }
.assessment li + li { margin-top: 8px; }
.map-panel { border: 1px solid #d7ddd6; padding: 22px; background: white; margin: 0 0 26px; }
.map-panel h3 { margin: 0 0 6px; font-size: 21px; }
.map-panel .help { margin-bottom: 12px; }
.map-panel svg { display: block; width: 100%; height: 340px;
  background: #f4f6f2; border: 1px solid #e0e6dd; }
.map-panel svg text { font: 22px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
.map-panel .map-caption { font-size: 16px; color: #56645d; margin: 12px 0 0; }
.map-grid { stroke: #dce3d8; stroke-width: 1; }
.map-axis { stroke: #aebcaf; stroke-width: 1; }
.map-scale { stroke: #263831; stroke-width: 2; }
.map-neighbor { fill: #adb9ae; stroke: white; stroke-width: 2; }
.map-a { fill: #315c49; stroke: white; stroke-width: 3; }
.map-b { fill: #99542e; stroke: white; stroke-width: 3; }
.map-ab { fill: #4e4d6a; stroke: white; stroke-width: 3; }
.map-tag { fill: #263831; font-weight: 700 !important; paint-order: stroke;
  stroke: #f4f6f2; stroke-width: 4px; }
.map-pair-line { stroke: #899c8b; stroke-width: 1.5; stroke-dasharray: 5 5; }
.neighbor-list { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px;
  margin: 14px 0 0; padding-left: 24px; font-size: 16px; }
.neighbor-list li { overflow-wrap: anywhere; }
.record .scope { margin-bottom: 16px; font-size: 18px; color: #315c49; font-weight: 600; }
.record h2 { font-size: 28px; }
.record .parent { margin: 10px 0 16px; color: #56645d; }
.record dl.compact { display: grid; grid-template-columns: 140px 1fr; gap: 12px; }
.record dl.compact dt, .record dl.compact dd { margin: 0; }
.record dl.compact dt { font-size: 17px; }
.record details { margin-top: 20px; }
.prior { padding: 20px 24px; border-left: 3px solid #bc9569; background: #f5f0e7;
  margin-top: 24px; }
.prior blockquote { margin: 10px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.choice { align-items: flex-start; }
.choice small { display: block; font-size: 16px; font-weight: normal; color: #56645d; }
.choice input { margin-top: 4px; }
.decision .choices { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.review-actions { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 20px; }
.scope-help { margin: 14px 0 0; }
@media (max-width: 1099px) {
  .context-grid { grid-template-columns: 1fr; gap: 20px; }
  .map-panel svg { height: 340px; }
}
@media (max-width: 780px) {
  .toolbar { position: static; }
  .case-title { font-size: 27px; }
  .assessment, .map-panel { padding: 18px; }
  .map-panel svg { height: auto; min-height: 185px; }
  .map-panel svg text { font-size: 28px; }
  .record dl.compact { display: block; }
  .record dl.compact dt { margin-top: 16px; }
  .record dl.compact dd { margin-top: 4px; }
  .decision .choices, .neighbor-list { grid-template-columns: 1fr; }
}
@media print {
  .toolbar, .review-actions { display: none; }
  .map-panel { break-inside: avoid; }
  .context-grid { display: block; }
  .map-panel svg { max-height: 250px; }
  .assessment, .prior { background: white; border: 1px solid #999; }
}
"""
)

_SCRIPT = r"""
"use strict";
const LABELS = new Set(["same_site", "different_sites", "left_contains_right",
  "right_contains_left", "related_scope_unclear", "insufficient_evidence"]);
const LABEL_NAMES = {same_site: "Same physical site", different_sites: "Distinct sites",
  left_contains_right: "A contains B", right_contains_left: "B contains A",
  related_scope_unclear: "Related, but scope is unclear",
  insufficient_evidence: "Insufficient evidence",
  uncertain: "Uncertain (original review)"};
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
function buildCsv(cases, decisions, reviewer, sampleHash) {
  const rows = [["pair_id", "left_record_id", "right_record_id", "label",
    "reviewer", "notes", "sample_sha256"]];
  for (const item of cases) {
    const decision = decisions[item.pair_id] || {};
    rows.push([item.pair_id, item.left_record_id, item.right_record_id,
      LABELS.has(decision.label) ? decision.label : "", reviewer,
      typeof decision.notes === "string" ? decision.notes : "", sampleHash]);
  }
  return "\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}
function restoreState(cases, saved, sampleHash) {
  const state = {reviewer: "", decisions: Object.create(null)};
  const valid = saved && saved.schema_version === 2 && saved.sample_sha256 === sampleHash;
  if (valid && typeof saved.reviewer === "string") state.reviewer = saved.reviewer.slice(0, 120);
  for (const item of cases) {
    const old = valid && saved.decisions && Object.hasOwn(saved.decisions, item.pair_id)
      ? saved.decisions[item.pair_id] : null;
    state.decisions[item.pair_id] = {
      label: old && LABELS.has(old.label) ? old.label : "",
      notes: old && typeof old.notes === "string" ? old.notes.slice(0, 5000) : ""
    };
  }
  return state;
}
function compactTerms(record) {
  const seen = new Set(), result = [];
  for (const item of (Array.isArray(record.pilot_evidence) ? record.pilot_evidence : [])) {
    if (!item || typeof item.raw_value !== "string") continue;
    const term = item.raw_value.trim().replace(/\s+/g, " ");
    const role = typeof item.role === "string" ? item.role : "unspecified";
    if (!term || seen.has(term.toLowerCase() + "|" + role)) continue;
    seen.add(term.toLowerCase() + "|" + role);
    result.push(term.slice(0, 100) + (term.length > 100 ? "…" : "") +
      (role !== "unspecified" ? " (" + role + ")" : "") + (item.qualified ? " [qualified]" : ""));
  }
  return {items: result.slice(0, 4), more: Math.max(0, result.length - 4)};
}
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
function countrySummary(record) {
  return Array.isArray(record.country_iso3s) && record.country_iso3s.length
    ? record.country_iso3s.join("; ")
    : (record.country ? display(record.country) + " (as reported; ISO unresolved)"
      : "Not supplied");
}
function scopeName(level) {
  return ({site: "Individual deposit or mine", subsite: "Named subsite",
    operation_complex: "Operation or geological complex", district_area: "District or area",
    unknown: "Scope needs clarification"})[level] || "Scope needs clarification";
}
function reasonText(item) {
  const value = item.explanation || item.reasons;
  if (Array.isArray(value)) return value.map(display);
  if (typeof value === "string" && value) return [value];
  return (item.reason_codes || []).map(code => String(code).replaceAll("_", " "));
}
/* Browser application. */
const payload = JSON.parse(document.getElementById("review-data").textContent);
const cases = payload.cases, sampleHash = payload.metadata.sample_sha256;
const sourceNames = payload.metadata.source_names || {};
const storageKey = "critical-minerals-atlas:triage-pilot:v2:" + sampleHash;
const byId = id => document.getElementById(id);
let saved = null, storageAvailable = true;
try { saved = JSON.parse(localStorage.getItem(storageKey) || "null"); }
catch { storageAvailable = false; }
const state = restoreState(cases, saved, sampleHash);
let index = 0;
byId("reviewer").value = state.reviewer;
byId("sample-hash").textContent = sampleHash;
byId("save-status").textContent = storageAvailable
  ? "Progress is saved in this browser. Export a CSV to keep a separate copy."
  : "Browser storage is unavailable. Export before closing this page.";
if (payload.metadata.title) byId("page-title").textContent = payload.metadata.title;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function fact(list, label, value) { list.append(node("dt", label), node("dd", display(value))); }
function externalLink(url, text) {
  const safe = safeHttpsUrl(url);
  if (!safe) return node("span", "Record-level publisher link unavailable");
  const anchor = node("a", text);
  anchor.href = safe; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
  anchor.referrerPolicy = "no-referrer";
  return anchor;
}
function renderRecord(record, letter) {
  const article = node("article", undefined, "record");
  article.setAttribute("aria-label", letter + " source record");
  article.append(node("p", letter + " · " + scopeName(record.entity_level), "scope"));
  article.append(node("h2", record.display_name || record.primary_name || "Name not supplied"));
  if (record.parent_name) article.append(node("p", "Parent: " + record.parent_name, "parent"));
  article.append(node("p", sourceNames[record.source_id] || record.source_id, "source"));
  const facts = node("dl", undefined, "compact");
  fact(facts, "Country", countrySummary(record));
  fact(facts, "Geology", Array.isArray(record.deposit_types)
    ? record.deposit_types.map(code => String(code).replaceAll("_", " ")) : record.deposit_types);
  fact(facts, "Coordinates", Number.isFinite(record.lat) && Number.isFinite(record.lon)
    ? record.lat + ", " + record.lon : "No point supplied");
  fact(facts, "Precision", record.location_precision || "Not stated by source");
  const terms = compactTerms(record);
  fact(facts, "Commodities", terms.items.length ? terms.items.join("; ") +
    (terms.more ? " · " + terms.more + " more terms in details" : "") : record.pilot_families);
  fact(facts, "Source status", record.raw_status);
  article.append(facts);
  const links = node("p");
  links.append(externalLink(record.site_url, "Open source evidence"));
  if (!safeHttpsUrl(record.site_url) && safeHttpsUrl(record.source_url)) {
    links.append(document.createTextNode(" · "),
      externalLink(record.source_url, "Source download"));
  }
  article.append(links);
  const details = node("details");
  details.append(node("summary", "Names, classification and evidence"));
  const evidence = node("dl");
  for (const [label, field] of [["Scope evidence", "entity_level_evidence"],
    ["Scope flags", "scope_flags"], ["Identity aliases", "identity_aliases"],
    ["Parent / area / component names", "context_names"],
    ["Unverified name variants", "unverified_aliases"],
    ["Name evidence", "alias_evidence"],
    ["Original deposit type", "deposit_types_raw"],
    ["Deposit classification evidence", "deposit_type_evidence"],
    ["Source notes", "source_notes"], ["Full commodity evidence", "pilot_evidence"],
    ["References", "reference"], ["Source provenance", "provenance"], ["Record ID", "record_id"]]) {
    fact(evidence, label, record[field]);
  }
  details.append(evidence);
  const citations = node("ul");
  for (const link of (Array.isArray(record.source_links) ? record.source_links : [])) {
    if (!link || !safeHttpsUrl(link.url)) continue;
    const entry = node("li");
    entry.append(externalLink(link.url, link.label || "Source reference"));
    citations.append(entry);
  }
  if (citations.childNodes.length) details.append(citations);
  article.append(details);
  return article;
}
function svgNode(tag, attrs, text) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
  if (text !== undefined) element.textContent = text;
  return element;
}
function renderMap(item) {
  const panel = node("section", undefined, "map-panel");
  panel.setAttribute("aria-label", "Geographic context");
  panel.append(node("h3", "Location and nearby records"));
  panel.append(node("p", "Local reference map · source points; no terrain basemap", "help"));
  const map = item.review_map;
  if (!map.available) { panel.append(node("p", map.reason)); return panel; }
  const svg = svgNode("svg", {viewBox: "0 0 800 340", role: "img",
    "aria-label": "North-oriented source points: A and B, with nearby source records"});
  svg.append(svgNode("title", {}, "Source point map"));
  svg.append(svgNode("desc", {}, "Source locations in a local metric projection. " +
    "Points show recorded coordinates, not boundaries or known accuracy."));
  for (const x of [50, 225, 400, 575, 750]) {
    svg.append(svgNode("line", {x1:x,y1:40,x2:x,y2:300,class:"map-grid"}));
  }
  for (const y of [40, 105, 170, 235, 300]) {
    svg.append(svgNode("line", {x1:50,y1:y,x2:750,y2:y,class:"map-grid"}));
  }
  svg.append(svgNode("line", {x1:400,y1:40,x2:400,y2:300,class:"map-axis"}));
  svg.append(svgNode("line", {x1:50,y1:170,x2:750,y2:170,class:"map-axis"}));
  const pairPoints = map.points.filter(point => point.label);
  if (pairPoints.length === 2) svg.append(svgNode("line", {
    x1:pairPoints[0].x,y1:pairPoints[0].y,x2:pairPoints[1].x,y2:pairPoints[1].y,
    class:"map-pair-line"}));
  const neighbors = map.markers.filter(marker => !marker.label);
  const focal = map.markers.filter(marker => marker.label);
  let markerIndex = 0;
  for (const marker of [...neighbors, ...focal]) {
    const label = marker.label || String(++markerIndex);
    const group = svgNode("g", {});
    group.append(svgNode("title", {}, marker.members.map(point => point.name).join("; ")));
    group.append(svgNode("circle", {cx:marker.x,cy:marker.y,r:marker.label ? 10 : 6,
      class:marker.label === "A/B" ? "map-ab" : marker.label === "A" ? "map-a"
        : marker.label === "B" ? "map-b" : "map-neighbor"}));
    group.append(svgNode("text", {x:marker.x+13,y:marker.y-11,class:"map-tag"},
      label + (marker.count > 1 ? " (" + marker.count + " records)" : "")));
    svg.append(group);
  }
  svg.append(svgNode("line", {x1:730,y1:70,x2:730,y2:30,class:"map-scale"}));
  svg.append(svgNode("path", {d:"M724 39 L730 29 L736 39",fill:"none",class:"map-scale"}));
  svg.append(svgNode("text", {x:725,y:22}, "N"));
  const scaleEnd = 60 + map.scale_px;
  svg.append(svgNode("line", {x1:60,y1:315,x2:scaleEnd,y2:315,class:"map-scale"}));
  for (const x of [60, scaleEnd]) {
    svg.append(svgNode("line", {x1:x,y1:310,x2:x,y2:320,class:"map-scale"}));
  }
  svg.append(svgNode("text", {x:scaleEnd+10,y:321}, map.scale_km + " km"));
  panel.append(svg);
  const labels = node("ul", undefined, "neighbor-list");
  for (const label of ["A", "B"]) {
    const point = map.points.find(point => point.label === label);
    if (point) labels.append(node("li", label + " — " + point.name));
  }
  for (const marker of focal) {
    const otherNames = marker.members.filter(point => !point.label).map(point => point.name);
    if (otherNames.length) {
      labels.append(node("li", "At " + marker.label + " — " + otherNames.join("; ")));
    }
  }
  neighbors.forEach((marker, i) => {
    labels.append(node("li", (i + 1) + " — " + marker.members.map(point => point.name).join("; ")));
  });
  panel.append(labels);
  let caption = "North is up. The scale measures distance between recorded points; " +
    "location accuracy is not implied. Shared coordinates use a single marker.";
  const shown = map.points.filter(point => !point.label).length;
  const total = Number.isInteger(item.neighbor_count) ? item.neighbor_count : shown;
  if (total) caption += " Showing " + shown + " of " + total + " nearby source records.";
  if (map.omitted_neighbors) caption += " " + map.omitted_neighbors +
    " invalid or distant neighboring points omitted.";
  if (map.polar_orientation) caption += " Near the pole, orientation follows the central meridian.";
  panel.append(node("p", caption, "map-caption"));
  const center = map.origin;
  const url = "https://www.openstreetmap.org/?mlat=" + encodeURIComponent(center.lat) +
    "&mlon=" + encodeURIComponent(center.lon) + "#map=12/" + center.lat + "/" + center.lon;
  const links = node("p", undefined, "map-caption");
  links.append(externalLink(url, "Open this area in OpenStreetMap")); panel.append(links);
  return panel;
}
function renderAssessment(item) {
  const panel = node("section", undefined, "assessment");
  panel.setAttribute("aria-label", "Automated triage evidence");
  const queues = {likely_match: "Likely match", likely_distinct: "Low-priority likely nonmatch",
    relationship: "Relationship or geographic scope",
    ambiguous: "Conflicting or incomplete evidence"};
  panel.append(node("h3", "Automated triage: " + (queues[item.queue] || "Needs review")));
  panel.append(node("p",
    "This is a routing suggestion, not a verified identity or match probability."));
  const reasons = node("ul");
  for (const reason of reasonText(item)) reasons.append(node("li", reason));
  panel.append(reasons);
  const details = node("details");
  details.append(node("summary", "Comparison details"));
  const values = node("dl");
  fact(values, "Signals", item.features);
  fact(values, "Routing reasons", item.reason_codes);
  details.append(values); panel.append(details);
  return panel;
}
function renderPrior(item) {
  const panel = byId("prior"); panel.replaceChildren();
  const prior = item.previous_decision;
  panel.hidden = !prior;
  if (!prior) return;
  panel.append(node("h3", "Your original decision is preserved"));
  panel.append(node("p", (LABEL_NAMES[prior.label] || prior.label) +
    (prior.reviewer ? " · " + prior.reviewer : "")));
  if (prior.notes) panel.append(node("blockquote", prior.notes));
  panel.append(node("p", "This pilot decision is separate. Your original CSV, label and notes " +
    "will remain unchanged.", "help"));
}
function updateProgress() {
  const reviewed = Object.values(state.decisions).filter(item => LABELS.has(item.label)).length;
  byId("progress-text").textContent = reviewed + " of " + cases.length + " reviewed";
  byId("progress").value = reviewed; byId("progress").max = cases.length;
  byId("export").disabled = !state.reviewer.trim() || reviewed === 0;
  byId("next-unreviewed").disabled = reviewed === cases.length;
  const decision = state.decisions[cases[index].pair_id];
  byId("print-decision").textContent = "Pilot decision: " +
    (LABEL_NAMES[decision.label] || "Unreviewed") + "\nReviewer: " +
    (state.reviewer || "Not supplied") + "\nNotes: " + (decision.notes || "None");
}
function persist() {
  if (storageAvailable) {
    try { localStorage.setItem(storageKey, JSON.stringify({schema_version:2,
      sample_sha256:sampleHash,reviewer:state.reviewer,decisions:state.decisions})); }
    catch { storageAvailable = false;
      byId("save-status").textContent = "Progress could not be saved. Export before closing."; }
  }
  updateProgress();
}
function render(focus = false) {
  const item = cases[index];
  byId("pair-position").textContent = "Case " + (index+1) + " of " + cases.length;
  byId("case-title").textContent = (item.left.display_name || "Record A") + " / " +
    (item.right.display_name || "Record B");
  byId("pair-id").textContent = item.pair_id;
  byId("pair-context").textContent = "Source points are " +
    item.distance_km.toLocaleString(undefined,{maximumFractionDigits:2}) + " km apart" +
    " · Shared commodities: " + display(item.shared_families);
  byId("assessment").replaceChildren(renderAssessment(item));
  byId("map").replaceChildren(renderMap(item));
  byId("records").replaceChildren(renderRecord(item.left,"A"),renderRecord(item.right,"B"));
  renderPrior(item);
  const decision = state.decisions[item.pair_id];
  for (const radio of document.querySelectorAll('input[name="decision"]')) {
    radio.checked = radio.value === decision.label;
  }
  byId("notes").value = decision.notes;
  byId("back").disabled = index === 0;
  byId("next").disabled = index === cases.length-1;
  byId("next-bottom").disabled = index === cases.length-1;
  updateProgress();
  if (focus) {
    byId("pair-position").focus();byId("pair-position").scrollIntoView({block:"start"});
  }
}
function move(offset) {
  const next = index+offset;
  if (next>=0 && next<cases.length) { index=next;render(true); }
}
byId("back").addEventListener("click",()=>move(-1));
byId("next").addEventListener("click",()=>move(1));
byId("next-bottom").addEventListener("click",()=>move(1));
byId("next-unreviewed").addEventListener("click",()=>{
  for(let offset=1;offset<=cases.length;offset++) {
    const next=(index+offset)%cases.length;
    if(!state.decisions[cases[next].pair_id].label) { index=next;render(true);return; }
  }
});
for(const radio of document.querySelectorAll('input[name="decision"]')) {
  radio.addEventListener("change",()=>{
    if(radio.checked && LABELS.has(radio.value)) {
      state.decisions[cases[index].pair_id].label=radio.value;persist();
    }
  });
}
byId("notes").addEventListener("input",event=>{
  state.decisions[cases[index].pair_id].notes=event.target.value;persist();
});
byId("reviewer").addEventListener("input",event=>{state.reviewer=event.target.value;persist();});
byId("clear").addEventListener("click",()=>{
  state.decisions[cases[index].pair_id].label="";persist();render();
});
document.addEventListener("keydown",event=>{
  if(event.altKey && !event.ctrlKey && !event.metaKey &&
    ["ArrowLeft","ArrowRight"].includes(event.key)) {
    event.preventDefault();move(event.key==="ArrowLeft" ? -1 : 1);
  }
});
byId("export").addEventListener("click",()=>{
  if(!state.reviewer.trim() || !Object.values(state.decisions).some(item=>item.label))return;
  const csv=buildCsv(cases,state.decisions,state.reviewer.trim(),sampleHash);
  const url=URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
  const anchor=node("a");anchor.href=url;
  anchor.download="atlas-triage-pilot-"+sampleHash.slice(0,12)+"-"+
    new Date().toISOString().replaceAll(":","-")+".csv";
  document.body.append(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
  byId("save-status").textContent=
    "New pilot CSV download requested. Original decisions are unchanged.";
});
byId("print").addEventListener("click",()=>window.print());
render();
"""

_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<title>Critical Minerals Atlas — focused review pilot</title><style>__STYLE__</style></head>
<body><a class="skip" href="#pair-position">Skip to the current case</a>
<header><p class="wordmark">Critical Minerals Atlas</p>
<h1 id="page-title">A more focused review</h1>
<p class="intro">Twenty cases with location, source classification
and the reason each needs attention.
Your first twenty decisions are preserved. This pilot tests the improved review process;
it is not the final accuracy benchmark.</p>
<details><summary>How to review these cases</summary>
<p>Use <strong>Same physical site</strong> for two descriptions of one physical deposit or mine.
A district, operation or geological complex may <strong>contain</strong> a smaller asset.
That relationship does not mean they are the same site. Select a direction only when the source
evidence supports it; otherwise choose <strong>Related, but scope is unclear</strong>.</p>
<p>Names, coordinates and commodity overlap are evidence, not proof. Status and ownership can
change over time. Choose <strong>Insufficient evidence</strong> if the supplied sources do not
support a decision. An automated triage suggestion is never a human label.</p>
<p>Use Alt + Left or Alt + Right to move between cases. Export partial progress regularly.</p>
</details></header>
<main><div class="toolbar"><div class="progress-wrap">
<p id="progress-text" role="status" aria-live="polite">No decisions yet</p>
<progress id="progress" value="0" max="20" aria-label="Cases reviewed"></progress></div>
<nav aria-label="Review navigation"><button id="back" type="button">Back</button>
<button id="next" type="button">Next</button>
<button id="next-unreviewed" type="button">Next unreviewed</button></nav></div>
<div class="pair-heading"><h2 id="pair-position" tabindex="-1">Current case</h2></div>
<h2 id="case-title" class="case-title"></h2><p id="pair-context" class="context-line"></p>
<div class="context-grid"><div class="context-evidence"><div id="assessment"></div>
<section id="prior" class="prior" aria-label="Preserved original decision" hidden></section>
</div><div id="map"></div></div>
<section id="records" class="comparison" aria-label="Source record comparison"></section>
<section class="decision" aria-label="Your pilot review">
<fieldset aria-describedby="decision-help"><legend>How are A and B related?</legend>
<p id="decision-help" class="help">Every pilot decision starts blank. Previous decisions remain
read-only above, and a new answer never overwrites them.</p><div class="choices">
<label class="choice"><input type="radio" name="decision" value="same_site">
<span>Same physical site<small>Two descriptions of one deposit or mine</small></span></label>
<label class="choice"><input type="radio" name="decision" value="different_sites">
<span>Distinct sites<small>Separate physical assets</small></span></label>
<label class="choice"><input type="radio" name="decision" value="left_contains_right">
<span>A contains B<small>B is a documented part of the broader A</small></span></label>
<label class="choice"><input type="radio" name="decision" value="right_contains_left">
<span>B contains A<small>A is a documented part of the broader B</small></span></label>
<label class="choice"><input type="radio" name="decision" value="related_scope_unclear">
<span>Related, but scope is unclear<small>Relationship is supported;
its exact scope is unresolved</small>
</span></label>
<label class="choice"><input type="radio" name="decision" value="insufficient_evidence">
<span>Insufficient evidence<small>The supplied facts do not settle the relationship</small>
</span></label>
</div></fieldset>
<label class="notes-label" for="notes">Notes <span class="help">(optional)</span></label>
<textarea id="notes" rows="3" maxlength="5000"
placeholder="What evidence settles the case, or what is still missing?"></textarea>
<div class="review-actions"><button id="clear" type="button">Clear pilot decision</button>
<button id="next-bottom" type="button">Next case</button>
<button id="print" type="button">Print this case</button></div></section>
<p id="print-decision" class="print-only"></p>
<section class="export" aria-label="Save your pilot review"><div>
<label for="reviewer">Reviewer name <span class="help">(required for export)</span></label>
<input id="reviewer" type="text" maxlength="120" autocomplete="off"></div>
<div class="export-actions"><button id="export" type="button" class="primary" disabled>
Export pilot decisions as CSV</button>
<p>Export after your first decision or when finished. Every export creates a new file;
unreviewed cases remain blank.</p></div></section>
<p id="save-status" role="status" aria-live="polite"></p>
<noscript>This local pilot needs JavaScript to show the map and source evidence.</noscript></main>
<footer><p>Private local review. No decisions or source data are sent to a server.</p>
<details><summary>About this pilot</summary>
<p>The reference maps use only supplied source coordinates. Opening a publisher or map link is
optional and opens an external website. Progress is saved only for this exact pilot sample.</p>
<p class="hash">Current pair: <span id="pair-id"></span></p>
<p class="hash">Sample SHA-256: <span id="sample-hash"></span></p></details></footer>
<script id="review-data" type="application/json">__DATA__</script><script>__SCRIPT__</script>
</body></html>
"""


def _digest(value: str) -> str:
    return base64.b64encode(hashlib.sha256(value.encode()).digest()).decode()


def render_pilot(cases: list[dict], metadata: dict) -> bytes:
    """Render a reproducible offline pilot; prior labels never initialize new answers."""
    if not isinstance(metadata, dict) or not re.fullmatch(
        r"[a-f0-9]{64}", str(metadata.get("sample_sha256", ""))
    ):
        raise ValueError("Pilot metadata requires a lowercase SHA-256 sample digest")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Pilot requires cases")
    if len(cases) != 20 and metadata.get("allow_test_sample") is not True:
        raise ValueError("The review pilot requires exactly 20 cases")
    seen = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Each pilot case must be an object")
        for field in ("pair_id", "left_record_id", "right_record_id"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError("Pilot pair and record IDs must be nonblank strings")
        if case["pair_id"] in seen or case["left_record_id"] == case["right_record_id"]:
            raise ValueError("Pilot cases require unique pairs of distinct records")
        seen.add(case["pair_id"])
        for side in ("left", "right"):
            record = case.get(side)
            if not isinstance(record, dict) or record.get("record_id") != case[f"{side}_record_id"]:
                raise ValueError("Pilot side record IDs must match the accepted pair")
        distance = case.get("distance_km")
        if (
            isinstance(distance, bool)
            or not isinstance(distance, (float, int))
            or (not math.isfinite(distance) or distance < 0)
        ):
            raise ValueError("Pilot distance must be finite and nonnegative")
    if "title" in metadata and not isinstance(metadata["title"], str):
        raise ValueError("Pilot title must be text")
    names = metadata.get("source_names", {})
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        raise ValueError("Source names must be a text dictionary")
    enriched = copy.deepcopy(cases)
    for case in enriched:
        case["review_map"] = map_data(case)
    data = json.dumps({"cases": enriched, "metadata": metadata}, ensure_ascii=True, allow_nan=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    csp = (
        "default-src 'none'; connect-src 'none'; font-src 'none'; img-src data:; "
        "base-uri 'none'; form-action 'none'; frame-src 'none'; "
        f"script-src 'sha256-{_digest(_SCRIPT)}'; style-src 'sha256-{_digest(_STYLE)}'"
    )
    replacements = {"__CSP__": csp, "__STYLE__": _STYLE, "__DATA__": data, "__SCRIPT__": _SCRIPT}
    return re.sub(r"__(?:CSP|STYLE|DATA|SCRIPT)__", lambda m: replacements[m[0]], _HTML).encode()
