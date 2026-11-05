"""Audit comparisons through shared publisher views without assigning those views to sites."""

from collections import defaultdict


def conflicts(decisions, groups, views):
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def join(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    for ids in [*views, *(g["record_ids"] for g in groups)]:
        for member in ids[1:]:
            join(ids[0], member)
    issues = []
    containment = defaultdict(set)
    directed = []
    for d in decisions:
        a, b = find(d["left_record_id"]), find(d["right_record_id"])
        if d["label"] != "same_site" and a == b:
            issues.append((d, "conflicts_with_identity_or_publisher_view"))
        if d["label"] in ("left_contains_right", "right_contains_left") and a != b:
            if d["label"] == "right_contains_left":
                a, b = b, a
            directed.append((a, b, d))
            containment[a].add(b)
    for a, b, d in directed:
        pending = [b]
        seen = set()
        while pending:
            current = pending.pop()
            if current == a:
                issues.append((d, "containment_cycle"))
                break
            if current not in seen:
                seen.add(current)
                pending.extend(containment[current] - seen)
    findings = []
    hold = set()
    for d, reason in issues:
        roots = {find(d["left_record_id"]), find(d["right_record_id"])}
        ids = sorted(n for n in list(parent) if find(n) in roots)
        hold.update(ids)
        findings.append(
            {"pair_id": d["pair_id"], "label": d["label"], "reason": reason, "record_ids": ids}
        )
    return findings, hold
