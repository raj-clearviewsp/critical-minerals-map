"""Verify the materialized rows independently of the component construction."""

from itertools import combinations


def verify_rows(projected, records, decisions, registry):
    obs = {r["record_id"]: r for r in projected["observations"]}
    members = {r["record_id"]: r for r in projected["members"]}
    sites = {s["site_id"]: s for s in projected["sites"]}
    assigned = {r["record_id"]: r["current_site_id"] for r in registry}
    if (
        set(obs) != set(records)
        or len(obs) != len(projected["observations"])
        or len(sites) != len(projected["sites"])
        or len(members) != len(projected["members"])
    ):
        raise ValueError("Duplicate or missing observation, site or membership")
    positive = {
        tuple(sorted((d["left_record_id"], d["right_record_id"]))): d["pair_id"]
        for d in decisions
        if d["label"] == "same_site"
    }
    direct_edges = 0
    for site_id, site in sites.items():
        ids = site["record_ids"]
        anchor = site["coordinate_record_id"]
        if (
            len(ids) != len(set(ids))
            or anchor not in ids
            or any(members[r]["site_id"] != site_id or assigned[r] != site_id for r in ids)
        ):
            raise ValueError("Site members, coordinate anchor or registry disagrees")
        original = records[anchor]
        if (site["lat"], site["lon"], site["location_precision"]) != (
            original["lat"],
            original["lon"],
            original["location_precision"],
        ):
            raise ValueError("Site reference point differs from its source")
        explicit = {positive.get(tuple(sorted(pair))) for pair in combinations(ids, 2)}
        if None in explicit or explicit != set(site["decision_pair_ids"]):
            raise ValueError("Published site lacks every direct approved edge")
        if (len(ids) == 1) != (site["review_state"] == "single_source_provisional"):
            raise ValueError("Incorrect site review state")
        direct_edges += len(explicit)
    for record_id, row in obs.items():
        membership = members.get(record_id)
        if row["site_id"] != (membership["site_id"] if membership else None):
            raise ValueError("Observation and site membership disagree")
        if membership and record_id not in sites[membership["site_id"]]["record_ids"]:
            raise ValueError("Unlisted site member")
    return {
        "source_ids_reconciled": len(obs),
        "members_checked": len(members),
        "site_points_checked_against_source": len(sites),
        "joined_edges_checked_against_decisions": direct_edges,
    }
