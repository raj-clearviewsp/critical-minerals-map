# Step and review gates

The implementation spec is the authority. This file makes the immediate dependencies explicit without substituting a new plan.

| Gate | Required evidence | Current state |
| --- | --- | --- |
| Phase 0 Step 1 | `make all`, passing tests, run report, changelog, pushed commit, green GitHub CI | Complete; CI run 40796696656 passed all three jobs |
| Phase 0 Step 2 | All source registrations, eligible fetchers, immutable manifests, no-op rerun | Acquisition checkpoint:9 sources/39 files; manual queue pending |
| Phase 0 Step 6 | Human-labelled 200-pair gold set, >=97% precision, reported recall | Not started |
| Phase 0 Step 7 | Human spot-check of 30 sites, at least 27 agreements | Not started |
| Phase 0 Step 12 | Five timed questions, protected preview, Raj's sign-off | Not started |
| Phase 1 | Raj's Phase 0 sign-off and approved schema change | Not authorized yet |
| Public launch | Raj's explicit approval | Not authorized |

Before fetching a source, its registry must identify the publisher, URL, license, redistribution decision, cadence and parser. The supplied spec contains both an instruction to ingest unconfirmed sources privately and a stronger stop-and-ask rule before **using** unclear-license sources. Apply the stricter rule: inspect public license metadata, but obtain Raj's decision before acquiring dataset values if terms remain unclear.

Before any download, use an owner-approved contact User-Agent. Do not infer the contact from local Git identity. Automated messages to publishers are not authorized by a checklist to decide whether to write to them.

The staged schemas and empty publish contract in Step 1 are plumbing, not production QA. The empty-only gate must remain until actual parsing, provenance, license checks and review requirements are implemented and tested at the specified steps.
