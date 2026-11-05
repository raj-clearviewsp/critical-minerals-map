"""Case-specific physical-site readings from approved research. Never generalize an alias."""

# Case numbers address checksum-bound research artifacts. Each entry captures a specific
# physical-scope finding and selects one retained source observation as the map reference.
FINDINGS = {
    "development60-v1": {
        5: ("right", "Operator identifies the mine separately from its offsite mill."),
        6: ("left", "Operator resource report identifies the named mineral-sands deposit."),
        19: ("left", "Government inventory identifies this particular mineral-sands occurrence."),
        20: (
            "right",
            "Survey catalog identifies the individual deposit and its commodity association.",
        ),
        26: (
            "left",
            "Operator distinguishes the historic mine from its deeper target and regional project.",
        ),
        32: (
            "right",
            "Operator identifies the mine and places the separately named mill elsewhere.",
        ),
        51: ("right", "Operator reports the multiple workings under one named mine."),
        60: (
            "left",
            "Cited geological study identifies this nickel deposit across the variant names.",
        ),
    },
    "next60-v1": {
        4: (
            "left",
            "Official ore database confirms the historical mine and matches the left source point.",
        ),
        21: (
            "left",
            "Provincial mine register locates the named mine close to the left source point.",
        ),
    },
    "final47-v1": {
        4: (
            "left",
            "Original geological investigation identifies a single named laterite deposit.",
        ),
        14: (
            "left",
            "State mineral register identifies the deposit separately and matches the left point.",
        ),
        17: ("left", "Operator mine table distinguishes the mine from its processing destination."),
        28: (
            "left",
            "Geological assessment identifies the named occurrence within the wider project.",
        ),
        42: (
            "left",
            "Survey report connects the published point and name to the individual deposit.",
        ),
    },
}


def physical_findings(data, hashes, decisions):
    accepted = {d["pair_id"]: d for d in decisions}
    result = {}
    for folder, selections in FINDINGS.items():
        artifact = f"data/review/{folder}/research-input.json"
        cases = data[artifact]["cases"]
        for index, c in enumerate(cases):
            if c["case_number"] not in selections:
                continue
            d = accepted[c["pair_id"]]
            side, basis = selections[c["case_number"]]
            if (
                c["recommendation"] != "same_site"
                or d["label"] != "same_site"
                or not c.get("scope_analysis")
                or not any(
                    ref["access"] in {"full_text", "indexed_extract"} for ref in c["citations"]
                )
            ):
                raise ValueError("Physical-scope crosswalk lost its approved evidence")
            result[d["pair_id"]] = {
                "anchor_record_id": d[f"{side}_record_id"],
                "basis": basis,
                "research_artifact": artifact,
                "research_sha256": hashes[artifact],
                "json_pointer": f"/cases/{index}",
                "case_number": c["case_number"],
            }
    if len(result) != sum(map(len, FINDINGS.values())):
        raise ValueError("Missing reviewed physical-scope crosswalk")
    return result
