# Sixty researched development comparisons

**Status: researched recommendations ready for human approval. No new human labels created.**

This batch takes the first 60 of the 87 unreviewed development comparisons in their
frozen order. Three research agents investigated separate sets of 20; a different
agent challenged each case in a second pass. The final packet records the reasons,
competing interpretations, remaining uncertainty, conditions for changing the answer,
and explicit source-access and source-independence limitations.

| Proposed relationship | Cases |
| --- | ---: |
| Distinct sites | 17 |
| Insufficient evidence | 2 |
| A contains B | 2 |
| Related, but scope is unclear | 24 |
| B contains A | 3 |
| Same physical site | 12 |

There are 217 citation entries across 119
distinct URLs. All 60 cases received an independent second review;
5 recommendations changed after review.
Confidence labels are qualitative judgments, not calibrated probabilities. A
high-confidence recommendation to leave scope unresolved is not a resolved identity.

## Review files

Open `data/review/development60-v1/index.html` for the approval workspace, or
`analysis.html` in the same directory for the complete searchable and printable
60-case dossier. All detailed source data and research stay local and Git-ignored.
Each case shows a recommendation and starts with a blank human decision. “Use this
recommendation” fills only the current case. Alternative relationships and notes
remain available. Enter your reviewer name and export partial or complete CSV
progress; browser storage alone is not a backup.

Validate an export with:

```sh
uv run --locked python -m pipeline.research_review --labels /absolute/path/to/export.csv --complete
```

Omit `--complete` for partial progress. This verifies the exact batch and source
identities without importing or overwriting decisions. The submitted decisions
will later be appended as a separate human review round.

## Preserved boundaries

The earlier 47 review events, their 34 unique comparisons, and all accepted
source/preview artifacts remain unchanged. The 80-case holdout has not been
researched or given recommendations. The other 27 new development cases are
unchanged. Answering this batch will leave 107 new cases (27 development and 80
holdout), plus seven unresolved earlier relationships and any cases in this batch
approved as unresolved. Preparing suggestions does not create human decisions or
convert unresolved relationships into binary match/nonmatch labels.

These are research-assisted development labels after approval, not independent
blind annotations. Any evaluation must disclose that assistance. Holdout labels
remain separate and blind; no model has been fitted or threshold tuned. Same-site
identity, directed containment and unresolved association remain different outcomes.
No automatic merges, new canonical identities, data publication or source licenses
are inferred from these recommendations.

## Verification

The packet is bound to its frozen parent development sample, exact first-60 pair
order, unchanged source records, latest review receipt and research-input checksum.
The public receipt contains aggregate counts and checksums only. Local verification
rebuilds the full private package byte-for-byte; CI verifies code, receipt and report
without private files. Structured tests cover changed/reordered pairs, missing
research, second-review requirements, output conflicts, blank initial decisions,
CSV identity binding and HTML/script escaping.
