import copy

from pipeline.preview.core import safe_url
from pipeline.research_review.core import LIST_FIELDS, TEXT_FIELDS
from pipeline.research_review.review_page import LABEL_NAMES


def validate_research(research: dict, selected: list) -> list:
    """Validate this batch without altering the historical fixed-size validator."""
    cases = research.get("cases", [])
    if (
        research.get("schema_version") != "researched-assisted47-v1"
        or len(cases) != 47
        or len(selected) != 47
        or [c.get("pair_id") for c in cases] != [c["pair_id"] for c in selected]
    ):
        raise ValueError("Research must cover exactly the final 47 assisted audit cases in order")
    for number, case in enumerate(cases, 1):
        if (
            case.get("case_number") != number
            or case.get("recommendation") not in LABEL_NAMES
            or case.get("initial_recommendation") not in LABEL_NAMES
            or case.get("confidence") not in {"high", "medium", "low"}
            or any(not isinstance(case.get(k), str) or not case[k].strip() for k in TEXT_FIELDS)
            or not isinstance(case.get("analyst"), str)
            or not case["analyst"].strip()
        ):
            raise ValueError("Each case needs complete, attributed research and a valid proposal")
        for key in LIST_FIELDS:
            if not isinstance(case.get(key), list) or not all(
                isinstance(value, str) and value.strip() for value in case[key]
            ):
                raise ValueError("Arguments and uncertainty must be text lists")
        if (
            len(case["rationale"]) < 2
            or not case["counterarguments"]
            or not case["what_would_change"]
        ):
            raise ValueError("Explain evidence, alternatives and revision conditions")
        citations = case.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ValueError("Every researched case requires citations")
        for citation in citations:
            if (
                not isinstance(citation, dict)
                or not safe_url(citation.get("url"))
                or any(
                    not isinstance(citation.get(k), str) or not citation[k].strip()
                    for k in ("title", "publisher", "locator", "claim", "independence_note")
                )
                or citation.get("access") not in {"full_text", "indexed_extract", "local_snapshot"}
                or (
                    citation.get("source_date") is not None
                    and not isinstance(citation["source_date"], str)
                )
            ):
                raise ValueError("Citations require safe URLs, locators and honest access details")
        if all(c["access"] == "local_snapshot" for c in citations):
            raise ValueError("Every case requires an external primary source check")
        peer = case.get("second_review", {})
        if (
            not isinstance(peer, dict)
            or not isinstance(peer.get("reviewer"), str)
            or not peer["reviewer"].strip()
            or peer["reviewer"] == case["analyst"]
            or peer.get("outcome") not in {"agree", "revise", "caution"}
            or not isinstance(peer.get("note"), str)
            or not peer["note"].strip()
            or not isinstance(peer.get("verifications"), list)
            or not peer["verifications"]
            or not all(isinstance(v, str) and v.strip() for v in peer["verifications"])
        ):
            raise ValueError("Every proposal requires a separate reviewer with substantive checks")
        if case["recommendation"] != case["initial_recommendation"] and not case.get(
            "editorial_review"
        ):
            raise ValueError("Changed recommendations require editorial explanation")
    return copy.deepcopy(cases)
