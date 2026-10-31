"""Aggregate-only report; source names, record details and reviewer notes stay local."""

# ruff: noqa: E501 -- Markdown prose/table template retains its paragraph formatting.


def render_report(receipt: dict) -> bytes:
    queues = receipt["queue_counts"]
    selection, preserved = receipt["selection"], receipt["import"]
    unit_counts = receipt.get("workload", {}).get("unit_queue_counts", {})
    names = {
        "likely_match": "Likely matches — audit required",
        "likely_distinct": "Likely distinct — lower-priority audit",
        "relationship": "District/complex relationship review",
        "ambiguous": "Identity or source-scope clarification",
    }
    table = "\n".join(
        f"| {names[key]} | {queues.get(key, 0):,} | {unit_counts.get(key, 0):,} |" for key in names
    )
    levels = "\n".join(
        f"| {key.replace('_', ' ')} | {value:,} |"
        for key, value in sorted(receipt["entity_levels"].items())
    )
    workload = receipt.get("workload", {})
    text = f"""# Critical Minerals Atlas — triage revision and focused review pilot

**Status: the triage revision and 20-case pilot are ready. Entity resolution remains incomplete.**

## What changed

The original **{receipt["candidate_pairs"]:,} pairs** remain intact and traceable. Every pair now has an explained routing decision based on source granularity, names, distance, geographic context and available classification. No pair has been silently discarded, relabelled as human truth or merged into a canonical site.

| Routing category | Original candidate pairs | Distinct review units |
| --- | ---: | ---: |
{table}
| **Total** | **{receipt["candidate_pairs"]:,}** | **{workload.get("review_units", 0):,}** |

The original-pair likely-match count includes **{receipt["source_view_pairs"]:,} verified GA workbook/API correspondences**. These represent the same publisher release in two formats, with matching name/classification signatures, country, state, status, mineral codes and coordinates. Both provenance trails survive; these are not independent corroboration or new site merges. Direct source-view pairs do not need an identity-review vote. Comparing both views with the same third record is consolidated into one review unit: **{workload.get("duplicate_comparisons_collapsed", 0):,} redundant comparisons** are collapsed for reviewer workload only. Each unit retains all its original pair IDs. Conflicting routes within a unit remain ambiguous.

Likely-distinct pairs are a lower-priority audit pool, not confirmed nonmatches. Likely matches remain provisional. The relationship and ambiguous queues deserve focused review; their size is not a request to manually label every pair.

## Your first decisions are preserved

The imported CSV contains **{preserved["reviewed_rows"]} original decisions**: {preserved["counts"]["same_site"]} same-site, {preserved["counts"]["different_sites"]} different-site and {preserved["counts"]["uncertain"]} uncertain. Its complete 200-row bytes are preserved locally. Labels, reviewer names, notes, record IDs and the original sample hash are unchanged. The remaining blank rows remain blank.

Original CSV SHA-256: `{preserved["original_csv_sha256"]}`.

The separate clarification check flags **{receipt["prior_clarification_flags"]}** original decisions because new scope, identifier or contextual evidence may affect interpretation. A machine flag never changes your decision. The pilot shows **{selection["previous_decisions_shown"]}** of these prior cases alongside their exact earlier answers, plus **{selection["new_cases"]} new cases**. Every new pilot decision starts blank and exports to a separate CSV.

Local preserved files: `data/review/triage-v2/original-decisions.csv`, `preserved-decisions.json`, and `prior-decision-checks.json`. The original 200-pair sample and raw/staging data remain unchanged.

## More useful source interpretation

| Evidence-backed entity level | Eligible records |
| --- | ---: |
{levels}

USGS record-type fields distinguish individual sites, districts/areas and intrusions/complexes. A geological complex is not automatically an operating mining complex. Jasansky subsites are displayed and compared by their explicit component names, with their parent operation shown separately. ICMM Group Names are contributing cluster names, not unrestricted identity aliases; its Contains values remain lineage evidence without an invented WA/JA crosswalk. MRDS alternate names remain unverified where site versus regional meaning is unclear.

Placeholder names do not support identity. Numbered identifiers and directional qualifiers are preserved and conflicting values are highlighted. Potential processing or broad-area names trigger a scope check rather than an asserted reclassification. Unknown source granularity stays unknown.

Informative mapped geological types exist for **{receipt["informative_deposit_type_records"]:,} of {receipt["eligible_records"]:,} records**. Missing geology is neutral. Different reported geology is a signal to investigate; source status and historical operating labels are not identity vetoes.

## Routing rules and geographic evidence

The unchanged candidate universe uses the original shared-mineral/distance block. This revision does not claim to recover matches outside that block. Unknown coordinate precision remains unknown.

- Explicit broad records go to relationship review. Containment or its direction is never inferred from proximity alone.
- Number/direction conflicts, publisher clusters and processing/broad-name warnings trigger clarification.
- Meaningful primary names and trusted aliases receive distinct roles. Shared area/component or unverified names cannot establish a strong identity match. Generic mineral and asset words are downweighted; weighted token overlap and character similarity are descriptive features, not calibrated probabilities.
- A likely match requires a meaningful exact name within 250 m, compatible individual-site scope and no checked conflict or reused-coordinate warning. It still requires validation.
- Over 2 km with no meaningful name agreement, no contextual/unverified exact-name rescue and no close text variant can enter the lower-priority likely-distinct pool. Missing classification alone does not force these cases back into expensive review.
- Coincident points are checked for reuse by differently named assets. Co-location does not establish identity.

## The improved 20-case pilot

Open **`data/review/triage-v2/index.html`** in a browser. It contains 3 likely-match audits, 3 likely-distinct audits, 7 relationship cases and 7 ambiguous cases. Verified duplicate source views are excluded from the pilot so they do not consume review effort. Source and problem diversity guide selection; it is intentionally informative rather than statistically representative.

Each case has a local geographic reference map with A/B markers, nearby records, north and a scale. Original coordinates are preserved; shared coordinates share a marker. The map has no terrain basemap and does not imply positional accuracy. Record-level citations are preferred where available, and source notes, classifications and provenance are expandable.

Choose **same physical site**, **distinct sites**, **A contains B**, **B contains A**, **related scope unclear**, or **insufficient evidence**. Earlier decisions are shown read-only. Add your reviewer name and export progress regularly; browser storage is not a backup. Exported decisions are validated against exact case IDs and the sample hash. An uncertain/relationship answer is not silently converted to a binary gold label.

Pilot SHA-256: `{receipt["sample_sha256"]}`.

## Validation and remaining work

The original inputs and prior review receipt are verified before preparation. Source-level files, your notes and detailed pair outputs remain local and Git-ignored. The committed receipt and this report contain aggregates and checksums. Reruns are deterministic and refuse changed existing outputs; a deliberate future revision requires a separate version.

Local verification rebuilds and compares the full triage packet, preservation artifacts, review units and pilot. CI validates code/tests, previous receipts, aggregate reconciliations and the regenerated report; it does not pretend to rebuild private source data.

There are **no fitted models, automatic merges, new canonical IDs or published source observations**. The pilot tests whether the revised evidence and questions are useful. The formal 200-pair validation, separate tuning/evaluation, precision/recall measurement and cluster checks remain necessary before Step 6 acceptance and later numbered steps.
"""
    return text.encode()
