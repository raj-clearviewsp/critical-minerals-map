# Next 60 comparisons: 27 researched and 33 blind

Raj approved all 60 previous final recommendations on 2026-11-04. Their accepted
round is bound by `reports/development60-accepted-audit.json`. The append-only history
now has 107 events and 94 unique comparisons; 33 relationships remain unresolved.

Raj selected **27 researched + 33 blind** to preserve the existing validation plan.
This batch takes development positions 61–87 and holdout positions 1–33 in their
original frozen orders. No cases were chosen by research outcome. The other 47
holdout cases remain outside this batch. No model has been fitted or thresholds tuned.

| Proposed development relationship | Cases |
| --- | ---: |
| Distinct sites | 9 |
| Insufficient evidence | 4 |
| Related, but scope is unclear | 8 |
| B contains A | 1 |
| Same physical site | 5 |

All 27 development cases received primary-source investigation and a separate
agent's second review. There are 110 citation entries across
66 distinct URLs. The 33 holdout cases have no research,
recommendations, prior answers or automated scores in either JSON or HTML payloads.
They are packaged separately so development research cannot enter the blind view.

Open `data/review/next60-v1/index.html` for both sections. `development.html` contains the
27 researched cases and `analysis.html` their full dossier. `holdout.html` contains
33 source-evidence-only comparisons. Each section begins blank, saves progress in
its own browser namespace and exports a separate sample-bound CSV.

```sh
uv run --locked python -m pipeline.review_next --labels /absolute/path/to/development.csv \
    --packet development --complete
uv run --locked python -m pipeline.review_next --labels /absolute/path/to/holdout.csv \
    --packet holdout --complete
```

Omit `--complete` for partial progress. Verification does not import or overwrite
answers. Research-assisted development approvals remain distinct from blind human
holdout annotations. Completing this batch leaves 47 new comparisons plus earlier
and newly unresolved relationships; unresolved answers are not binary gold labels.
There are no new human decisions, merges, canonical IDs or public data in this batch.

All earlier source, review and preview artifacts remain immutable. Local checks
rebuild this packet byte-for-byte. CI checks aggregate receipts without private
data. Tests verify exact subset selection, source preservation, separation of blind
payloads, blank initial decisions, approval provenance and sample-bound exports.
