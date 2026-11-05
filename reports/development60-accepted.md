# First 60 researched recommendations accepted

Raj explicitly approved all final recommendations in chat on 2026-11-04.
The private approval record preserves the exact message, reviewer, sample checksum,
research checksum and every approved pair/label. The completed CSV is an administrative
transcription of that approval, not a browser export or independently blind annotation.

| Accepted relationship | Cases |
| --- | ---: |
| different_sites | 17 |
| insufficient_evidence | 2 |
| left_contains_right | 2 |
| related_scope_unclear | 24 |
| right_contains_left | 3 |
| same_site | 12 |

The new round adds 60 decisions to the earlier 47 events. History now contains
107 events across 94 unique comparisons. All earlier events and source artifacts
are preserved. The latest view has 33 unresolved relationships:
seven earlier cases plus 26 approved as unresolved in this batch. Those answers do
not become binary match/nonmatch labels or automatic merges.

There are 107 new comparisons still unreviewed: 27 development and 80 holdout cases.
The frozen validation design contains 200 units, including 33 prior comparisons;
one additional historical publisher-view duplicate stays in history but outside it.
No model fitting, precision/recall claim, canonical identity creation or publication
is authorized by this approval. Formal validation remains incomplete.

Private accepted outputs are in `data/review/development60-approved-v1/`. The append-only history and
latest-decision view there supersede earlier views for subsequent review work.
Earlier receipts remain immutable snapshots. CI verifies this aggregate receipt;
local checking rebuilds the private acceptance artifacts byte-for-byte.
