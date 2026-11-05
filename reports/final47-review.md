# Final 47: researched recommendations for approval

Raj changed the plan on 2026-11-04: research and recommend the final 47 instead of
keeping them blind, then test matching robustly. The exact instruction and revised
evaluation protocol are retained. This packet uses the original holdout positions
34–80 without reselection; its status is now **agent-assisted audit, not blind**.

The completed 27 development and 33 blind exports are accepted separately. All 153
human-reviewed formal units remain human decisions. These 47 proposals are not
human labels, do not complete the original 200-human-review requirement, and are
excluded from fitting the experimental matcher. That experiment evaluates the 33
blind human answers after freezing; agent-agreement is reported separately.

| Proposed relationship | Cases |
| --- | ---: |
| different_sites | 16 |
| insufficient_evidence | 2 |
| left_contains_right | 6 |
| related_scope_unclear | 6 |
| right_contains_left | 1 |
| same_site | 16 |

All 47 cases have primary-source investigation and a separate agent's second review.
The dossier contains 218 citation entries across
117 URLs, with access limits and shared provenance
disclosed. 3 relationship proposals
changed during review. Unresolved proposals remain explicit evidence limits.

Open `data/review/final47-v1/index.html` to approve or revise; `analysis.html` contains the full
printable dossier. Answers start blank and save in a new browser namespace. Export
a CSV with your reviewer name. No prior decision is overwritten and no proposal is
automatically accepted. The CSV's packet hash prevents import into another batch.

No canonical sites are merged and no observation data is published. Each local
rebuild must reproduce accepted bytes; CI verifies aggregate receipts only.
