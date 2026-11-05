"""Build and verify the immutable first reviewed release and its aggregate public receipt."""

from __future__ import annotations

import csv
import fcntl
import io
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

import yaml

from pipeline.final_acceptance.core import RECEIPT as APPROVAL_RECEIPT
from pipeline.final_acceptance.core import check as check_approval
from pipeline.final_acceptance.core import latest_view
from pipeline.normalize.core import accept_outputs, sha256
from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.release_identity.identity import project
from pipeline.release_identity.registry import parquet_bytes, preserves_ids, read_registry
from pipeline.release_identity.scope_evidence import physical_findings
from pipeline.release_identity.validation import verify_rows
from pipeline.review_acceptance.core import bound
from pipeline.scaffold import digest, json_bytes

RELEASE = "2026.11.04-v0"
DAY = "2026-11-04"
DIRECTORY = f"data/releases/{RELEASE}/identity"
POLICY = "overrides/reviewed_release_v0.yaml"
REGISTRY = "overrides/id_registry.parquet"
BASELINE = f"{DIRECTORY}/registry-input.parquet"
REPORT = "reports/phase0-step06-reviewed.md"
RECEIPT = "reports/phase0-step06-reviewed-audit.json"
SOURCES = {
    APPROVAL_RECEIPT: (
        "data/review/final47-approved-v1/latest-decisions.json",
        "data/review/final47-approved-v1/decision-history.json",
    ),
    "reports/triage-audit.json": (
        "data/review/triage-v2/records.json",
        "data/review/triage-v2/triage-pairs.json",
        "data/review/triage-v2/source-views.json",
    ),
    "reports/private-preview-audit.json": ("data/preview/site-v1/dataset.json",),
    "reports/development60-audit.json": ("data/review/development60-v1/research-input.json",),
    "reports/next60-audit.json": ("data/review/next60-v1/research-input.json",),
    "reports/final47-audit.json": ("data/review/final47-v1/research-input.json",),
    "reports/reconciliation-audit.json": ("data/review/reconciliation-v1/source-research.json",),
}


def implementation(root):
    return {
        p.relative_to(root).as_posix(): sha256(p)
        for p in [*sorted((root / "pipeline/release_identity").glob("*.py")), root / "uv.lock"]
    }


def approved_policy(root):
    payload = (root / POLICY).read_bytes()
    p = yaml.safe_load(payload)
    expected = {
        "release": RELEASE,
        "approved_by": "Raj Sawhney",
        "approved_on": DAY,
        "identity_method": "explicitly_reviewed",
        "basis": APPROVAL_RECEIPT,
        "future_automatic_precision_minimum": 0.97,
    }
    if (
        any(p.get(k) != v for k, v in expected.items())
        or p.get("automatic_merging") is not False
        or not p.get("approval")
        or not p.get("scope")
    ):
        raise ValueError(
            "Owner amendment must require reviewed identity and keep the future 97% automatic gate"
        )
    return {
        "path": POLICY,
        "sha256": digest(payload),
        "basis": APPROVAL_RECEIPT,
        "basis_sha256": sha256(root / APPROVAL_RECEIPT),
        "automatic_merging": False,
        "future_automatic_precision_minimum": 0.97,
    }


def read_inputs(root):
    check_approval(root)
    data, hashes = {}, {}
    for receipt_name, names in SOURCES.items():
        receipt = json.loads((root / receipt_name).read_bytes())
        for name in names:
            raw = bound(root, receipt, name)
            hashes[name] = digest(raw)
            data[name] = json.loads(raw)
    return data, hashes


def relationships(decisions, data):
    research = {}
    for name, d in data.items():
        if name.endswith("research-input.json") or name.endswith("/source-research.json"):
            for i, c in enumerate(d["cases"]):
                research[c["pair_id"]] = {
                    "artifact": name,
                    "json_pointer": f"/cases/{i}",
                    "sha256": None,
                    "analysis_summary": c.get("summary") or c.get("confidence_reason"),
                    "proposal": c.get("recommendation"),
                }
    return [
        {
            **d,
            "research_reference": research.get(d["pair_id"]),
            "decision_history": "data/review/final47-approved-v1/decision-history.json",
        }
        for d in decisions
    ]


def queue_csv(pairs, decisions, groups, views, constraint_ids=()):
    """Account for each candidate, omitting resolved direct decisions from the queue."""
    decisions_by_id = {d["pair_id"]: d for d in decisions}
    quarantined = {
        p for g in groups if g["disposition"] == "quarantine" for p in g["decision_pair_ids"]
    }
    mirrors = {tuple(sorted(p)) for p in views}
    counts = Counter()
    neighbors = Counter()
    out = io.StringIO(newline="")
    headers = [
        "pair_id",
        "state",
        "left_record_id",
        "right_record_id",
        "left_name",
        "right_name",
        "distance_km",
        "triage_queue",
        "label",
        "review_event_id",
        "reason",
    ]
    writer = csv.DictWriter(out, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    seen = set()
    for pair in pairs:
        pid = pair["pair_id"]
        a, b = pair["left_record_id"], pair["right_record_id"]
        d = decisions_by_id.get(pid)
        if pid in seen or (d and (a, b) != (d["left_record_id"], d["right_record_id"])):
            raise ValueError("Duplicate or inconsistent source comparison")
        seen.add(pid)
        if pid in constraint_ids:
            state = "reviewed_constraint_conflict"
            reason = "Preserve conflicting decision pending adjudication."
        elif pid in quarantined:
            state = "reviewed_component_quarantined"
            reason = (
                "Reviewed identity component requires adjudication of a conflict or missing edge."
            )
        elif d and d["label"] in UNRESOLVED:
            state = "reviewed_unresolved"
            reason = "Preserve the explicit human abstention."
        elif d:
            counts["resolved_direct_decision"] += 1
            continue
        elif tuple(sorted((a, b))) in mirrors:
            state = "unreviewed_publisher_view"
            reason = (
                "Verified alternate publisher view. "
                "Source correspondence recorded; physical identity unadjudicated."
            )
        else:
            state = "unreviewed"
            reason = pair.get("explanation", "")
        counts[state] += 1
        if not d:
            neighbors.update((a, b))
        row = {
            "pair_id": pid,
            "state": state,
            "left_record_id": a,
            "right_record_id": b,
            "left_name": pair["left_name"],
            "right_name": pair["right_name"],
            "distance_km": pair["distance_km"],
            "triage_queue": pair["queue"],
            "label": d["label"] if d else "",
            "review_event_id": d["latest_event_id"] if d else "",
            "reason": reason,
        }
        writer.writerow(
            {
                k: (
                    "'" + v
                    if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else v
                )
                for k, v in row.items()
            }
        )
    if not decisions_by_id.keys() <= seen:
        raise ValueError("Reviewed decision is outside candidate inventory")
    return out.getvalue().encode(), dict(sorted(counts.items())), neighbors


def render(receipt):
    c = receipt["counts"]
    q = c["candidate_dispositions"]
    s = c["site_states"]
    g = c["group_dispositions"]
    return f"""# Phase 0 Step 6 · Reviewed identity release

The private reviewed release accounts for all **{c["located_observations"]:,} located source
observations**. The site table has **{c["site_entries"]:,} entries**:
{s.get("single_source_provisional", 0):,} positively identified single-source provisional
entries and {s.get("reviewed_physical_site", 0):,} reviewed physical-site groups. The count is
an inventory of entries. Outstanding duplicates can make counts of globally unique physical
deposits differ. Every observation stays available, including
{c["source_only_observations"]:,} outside the site table.

The register uses the exact approved human history (214 events, 201 latest comparisons, 200
formal units). The additional historical comparison links two formats of a publisher
observation. All six relationship types and legacy uncertain decisions retain their
directions, original source IDs, cited research where present and event history. The release
preserves 73 unresolved decisions. The 33 blind and 47 assisted final cases retain their
original evaluation roles.

## Identity and map eligibility

Only approved same-site decisions create evidence of shared identity. The
{c["identity_groups"]} groups are classified separately from map eligibility:
{g.get("reviewed_physical_site", 0)} support a jointly represented individual site,
{g.get("reviewed_named_identity_only", 0)} retain equivalence at a broader or uncertain scope,
and {g.get("quarantine", 0)} are held for incomplete internal comparisons. These two chains
retain all four approved direct edges. Each lacks a decision on the third comparison. A
group needs every internal pair approved and compatible country evidence to become a joined
site. Source-view flags preserve both records and avoid independent corroboration. A
positively identified individual source observation may have a provisional ID even when its
wider named relationship is held. Unknown, qualified, broad, processing or mixed records stay
available in the source layer.

Coordinates come unmodified from one eligible source record. Fifteen case-specific crosswalks
in approved research provide physical-scope evidence and select a sourced point. Other
selections prefer documented precision, then stronger evidence grade, then a recorded
retrieval time and finally the stable record ID. No centroid, precision estimate or average is
supplied. All {c["unknown_site_precision"]:,} entries without stated precision retain null.
Resource, status and commodity interpretation remain separate evidence tasks.

## Queue and stable identifiers

All {c["candidate_pairs"]:,} candidate comparisons reconcile with the source inventory. The
private queue includes {c["queue_rows"]:,} rows: {q.get("unreviewed", 0):,} unreviewed
ordinary candidates, {q.get("unreviewed_publisher_view", 0):,} alternate source views,
{q.get("reviewed_unresolved", 0):,} explicit abstentions and
{q.get("reviewed_component_quarantined", 0):,} held reviewed edges. The other
{q.get("resolved_direct_decision", 0):,} have resolved direct decisions. This queue preserves
work for future evidence updates; it is not an immediate review assignment.

Each site ID is a ULID minted from the release date and stable source identity. The persistent
registry retains original IDs and maps retired IDs to the oldest surviving ID when a later
approved union joins existing sites. A split requires explicit adjudication. Reordering and
reruns preserve IDs. The registry is stored privately in `overrides/id_registry.parquet` and
in the immutable release snapshot.

## Verification and limits

The receipt binds the owner’s November 4 amendment and its final approval receipt. The
amendment requires explicitly reviewed identity, disables automatic merging and preserves
0.97 as the minimum future automatic precision. A separate row check reconciled every
observation and site membership, compared all {c["site_entries"]:,} site points directly to
the original source values, and checked every joined record pair against a direct approval.
An independent constraint pass found {c["constraint_conflicts"]} contradictions or
containment-cycle decisions through approved identities and verified publisher views.

`python -m pipeline.release_identity --check --local` reproduces the private release from its
frozen inputs. `python -m pipeline.release_identity --check` validates the aggregate receipt
without private data. Input receipts, release files, implementation and the report are
checksummed. The machine matcher made zero merges. The separately frozen experiment found zero
eligible automatic predictions on the blind 33, leaving automatic precision undefined. The
original 97% automation gate stays closed. This step uses the approved manual-release
amendment. Data and source evidence remain private pending downstream maturity, publication
and release QA.
""".encode()


def build(root):
    data, hashes = read_inputs(root)

    def contents(suffix):
        return next(d for n, d in data.items() if n.endswith("/" + suffix))

    latest = contents("latest-decisions.json")
    history = contents("decision-history.json")
    if latest != latest_view(history):
        raise ValueError("Mismatch between accepted decisions and history")
    decisions = latest["decisions"]
    records = contents("records.json")
    preview_rows = contents("dataset.json")["records"]
    previews = {p["record_id"]: p for p in preview_rows}
    if len(previews) != len(preview_rows):
        raise ValueError("Duplicate preview observation")
    views = contents("source-views.json")["groups"]
    pairs = contents("triage-pairs.json")
    baseline = root / BASELINE if (root / BASELINE).is_file() else root / REGISTRY
    baseline_raw = baseline.read_bytes() if baseline.exists() else parquet_bytes([])
    projected, registry = project(
        records,
        previews,
        decisions,
        views,
        read_registry(baseline_raw),
        DAY,
        physical_findings(data, hashes, decisions),
    )
    direct = relationships(decisions, data)
    for d in direct:
        if ref := d["research_reference"]:
            ref["sha256"] = hashes[ref["artifact"]]
    queue, queue_counts, neighbors = queue_csv(
        pairs,
        decisions,
        projected["identity-groups"],
        views,
        {i["pair_id"] for i in projected["constraint-conflicts"]},
    )
    for row in projected["observations"]:
        row["unreviewed_candidate_count"] = neighbors[row["record_id"]]
    for row in projected["sites"]:
        row["has_unreviewed_candidates"] = any(neighbors[r] for r in row["record_ids"])
    projected["relationships"] = direct
    outputs = {f"{DIRECTORY}/{k}.json": json_bytes(v) for k, v in projected.items()}
    verification = verify_rows(projected, records, decisions, registry)
    reg = parquet_bytes(registry)
    outputs[f"{DIRECTORY}/id_registry.parquet"] = reg
    outputs[BASELINE] = baseline_raw
    outputs[f"{DIRECTORY}/review-queue.csv"] = queue
    counts = {
        "located_observations": len(records),
        "identity_groups": len(projected["identity-groups"]),
        "constraint_conflicts": len(projected["constraint-conflicts"]),
        "approved_identity_edges": latest["counts"]["same_site"],
        "candidate_pairs": len(pairs),
        "candidate_dispositions": queue_counts,
        "queue_rows": len(pairs) - queue_counts.get("resolved_direct_decision", 0),
        "site_entries": len(projected["sites"]),
        "site_members": len(projected["members"]),
        "source_only_observations": len(records) - len(projected["members"]),
        "unknown_site_precision": sum(s["location_precision"] is None for s in projected["sites"]),
        "site_states": dict(Counter(s["review_state"] for s in projected["sites"])),
        "group_dispositions": dict(Counter(g["disposition"] for g in projected["identity-groups"])),
        "latest_labels": latest["counts"],
        "review_events": len(history["events"]),
    }
    receipt = {
        "schema_version": "reviewed-identity-release-v1",
        "release": RELEASE,
        "status": "reviewed_manual_release",
        "owner_amendment": approved_policy(root),
        "row_verification": verification,
        "auto_merges": 0,
        "automatic_precision_gate_demonstrated": False,
        "counts": counts,
        "inputs": hashes,
        "parents": {n: sha256(root / n) for n in SOURCES},
        "implementation": implementation(root),
    }
    outputs[REPORT] = render(receipt)
    receipt["artifacts"] = {
        n: {"sha256": digest(raw), "bytes": len(raw)} for n, raw in outputs.items()
    }
    outputs[RECEIPT] = json_bytes(receipt)
    return outputs, receipt


def write(root):
    registry = root / REGISTRY
    registry.parent.mkdir(parents=True, exist_ok=True)
    lock = root / "data/releases/.id-registry.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as guard, tempfile.TemporaryDirectory(dir=root) as temporary:
        fcntl.flock(guard, fcntl.LOCK_EX)
        outputs, receipt = build(root)
        staged = []
        for i, (name, raw) in enumerate(outputs.items()):
            temp = Path(temporary) / str(i)
            temp.write_bytes(raw)
            staged.append((temp, root / name))
        desired = outputs[f"{DIRECTORY}/id_registry.parquet"]
        baseline = outputs[BASELINE]
        current = registry.read_bytes() if registry.exists() else None
        updating = current is None or current == baseline
        if not updating and not preserves_ids(desired, current):
            raise ValueError("Registry diverged from the reviewed release")
        for temp, target in staged:
            if target.exists() and target.read_bytes() != temp.read_bytes():
                raise ValueError(f"Conflicting immutable identity output: {target.name}")
        accept_outputs(staged[:-1])
        if updating and current != desired:
            new = Path(temporary) / "registry-update"
            new.write_bytes(desired)
            os.replace(new, registry)
        accept_outputs(staged[-1:])
    return receipt


def check(root, *, local=False):
    check_approval(root)
    receipt = json.loads((root / RECEIPT).read_bytes())
    if receipt.get("owner_amendment") != approved_policy(root):
        raise ValueError("Owner amendment changed")
    c = receipt["counts"]
    if (
        receipt["status"] != "reviewed_manual_release"
        or receipt["release"] != RELEASE
        or receipt["auto_merges"] != 0
        or receipt["automatic_precision_gate_demonstrated"] is not False
    ):
        raise ValueError("Invalid release policy")
    if (
        receipt["implementation"] != implementation(root)
        or receipt["parents"] != {n: sha256(root / n) for n in SOURCES}
        or bound(root, receipt, REPORT) != render(receipt)
    ):
        raise ValueError("Identity implementation, source receipt or report changed")
    for parent, files in SOURCES.items():
        expected = json.loads((root / parent).read_bytes())["artifacts"]
        if any(receipt["inputs"].get(n) != expected[n]["sha256"] for n in files):
            raise ValueError("Unbound parent input")
    expected_names = {
        f"{DIRECTORY}/{n}"
        for n in (
            "sites.json",
            "members.json",
            "observations.json",
            "relationships.json",
            "identity-groups.json",
            "id_registry.parquet",
            "registry-input.parquet",
            "constraint-conflicts.json",
            "review-queue.csv",
        )
    } | {REPORT}
    if (
        set(receipt["artifacts"]) != expected_names
        or c["located_observations"] != 10788
        or c["candidate_pairs"] != 39148
        or c["site_members"] + c["source_only_observations"] != c["located_observations"]
        or sum(c["candidate_dispositions"].values()) != c["candidate_pairs"]
        or sum(c["site_states"].values()) != c["site_entries"]
        or sum(c["group_dispositions"].values()) != c["identity_groups"]
        or receipt["row_verification"]["source_ids_reconciled"] != c["located_observations"]
        or receipt["row_verification"]["site_points_checked_against_source"] != c["site_entries"]
        or receipt["row_verification"]["members_checked"] != c["site_members"]
    ):
        raise ValueError("Reviewed release counts or outputs do not reconcile")
    if local:
        outputs, rebuilt = build(root)
        if not preserves_ids(
            outputs[f"{DIRECTORY}/id_registry.parquet"], (root / REGISTRY).read_bytes()
        ):
            raise ValueError("Persistent registry lost assigned site IDs")
        if rebuilt != receipt or any((root / n).read_bytes() != raw for n, raw in outputs.items()):
            raise ValueError("Private identity release differs from reproducible outputs")
    return receipt
