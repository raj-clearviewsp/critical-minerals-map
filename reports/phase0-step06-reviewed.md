# Phase 0 Step 6 · Reviewed identity release

The private reviewed release accounts for all **10,788 located source
observations**. The site table has **1,222 entries**:
1,205 positively identified single-source provisional
entries and 17 reviewed physical-site groups. The count is
an inventory of entries. Outstanding duplicates can make counts of globally unique physical
deposits differ. Every observation stays available, including
9,549 outside the site table.

The register uses the exact approved human history (214 events, 201 latest comparisons, 200
formal units). The additional historical comparison links two formats of a publisher
observation. All six relationship types and legacy uncertain decisions retain their
directions, original source IDs, cited research where present and event history. The release
preserves 73 unresolved decisions. The 33 blind and 47 assisted final cases retain their
original evaluation roles.

## Identity and map eligibility

Only approved same-site decisions create evidence of shared identity. The
56 groups are classified separately from map eligibility:
17 support a jointly represented individual site,
37 retain equivalence at a broader or uncertain scope,
and 2 are held for incomplete internal comparisons. These two chains
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
supplied. All 1,222 entries without stated precision retain null.
Resource, status and commodity interpretation remain separate evidence tasks.

## Queue and stable identifiers

All 39,148 candidate comparisons reconcile with the source inventory. The
private queue includes 39,024 rows: 38,702 unreviewed
ordinary candidates, 245 alternate source views,
73 explicit abstentions and
4 held reviewed edges. The other
124 have resolved direct decisions. This queue preserves
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
observation and site membership, compared all 1,222 site points directly to
the original source values, and checked every joined record pair against a direct approval.
An independent constraint pass found 0 contradictions or
containment-cycle decisions through approved identities and verified publisher views.

`python -m pipeline.release_identity --check --local` reproduces the private release from its
frozen inputs. `python -m pipeline.release_identity --check` validates the aggregate receipt
without private data. Input receipts, release files, implementation and the report are
checksummed. The machine matcher made zero merges. The separately frozen experiment found zero
eligible automatic predictions on the blind 33, leaving automatic precision undefined. The
original 97% automation gate stays closed. This step uses the approved manual-release
amendment. Data and source evidence remain private pending downstream maturity, publication
and release QA.
