"""Cited source research remains distinct from immutable human decisions."""

from __future__ import annotations

import copy
from collections import Counter
from urllib.parse import urlsplit

from pipeline.reconciliation.ledger import UNRESOLVED
from pipeline.triage.labels import LABELS


def reconcile_evidence(
    research: dict, latest: list[dict], records: dict, pairs: dict
) -> tuple[dict, dict]:
    if research.get("schema_version") != "source-research-v1" or not isinstance(
        research.get("cases"), list
    ):
        raise ValueError("Invalid source-research document")
    decisions = {row["pair_id"]: row for row in latest}
    cases, seen = [], set()
    for original in research["cases"]:
        case = copy.deepcopy(original)
        identity = case.get("pair_id")
        if identity in seen or identity not in decisions or identity not in pairs:
            raise ValueError("Research must identify a unique previously reviewed pair")
        seen.add(identity)
        row = pairs[identity]
        expected = [records[row[f"{side}_record_id"]]["display_name"] for side in ("left", "right")]
        if case.get("names") != expected:
            raise ValueError("Research names do not match their source record identities")
        if case.get("recommendation") not in LABELS - {""} or not case.get("confidence_reason"):
            raise ValueError("Research requires a supported proposal and its limitations")
        if not case.get("findings"):
            raise ValueError("Research requires at least one cited finding")
        for finding in case["findings"]:
            url = urlsplit(finding.get("url", ""))
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or not finding.get("claim")
                or not finding.get("title")
            ):
                raise ValueError("Research citations require HTTPS source, title and claim")
            if url.port is not None and not 1 <= url.port <= 65535:
                raise ValueError("Invalid source citation port")
        label = decisions[identity]["label"]
        proposal = case["recommendation"]
        if label == proposal:
            disposition = "research_open" if label in UNRESOLVED else "corroborated"
        elif label in UNRESOLVED and proposal in UNRESOLVED:
            disposition = "research_open"
        else:
            disposition = "human_adjudication"
        case.update(
            {
                "latest_human_label": label,
                "latest_event_id": decisions[identity]["latest_event_id"],
                "disposition": disposition,
                "recommendation_status": "agent_research_proposal_not_a_human_decision",
                "human_label_changed": False,
            }
        )
        cases.append(case)
    cases.sort(key=lambda case: case["pair_id"])
    result = {**copy.deepcopy(research), "cases": cases}
    summary = {
        "researched_cases": len(cases),
        "findings": sum(len(case["findings"]) for case in cases),
        "disposition_counts": dict(Counter(case["disposition"] for case in cases)),
        "proposal_counts": dict(Counter(case["recommendation"] for case in cases)),
        "human_labels_changed": 0,
        "scope": (
            "Source findings supplement review evidence; proposals never overwrite human decisions."
        ),
    }
    return result, summary
