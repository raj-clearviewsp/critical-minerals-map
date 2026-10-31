"""Exact, source-aware terminology resolution; never infer records or maturity."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import jsonschema
import pycountry
import yaml


class VocabularyError(ValueError):
    """A definition or source term needs explicit review."""


def key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


class Vocabulary:
    def __init__(self, root: Path):
        self.root = root
        self.documents = {}
        self.entries = {}
        self.aliases = {}
        directory = root / "sources/vocab"
        schema = json.loads(
            (Path(__file__).resolve().parents[2] / "schemas/vocabulary.schema.json").read_text()
        )
        for kind in ("commodities", "deposit_types", "statuses"):
            doc = yaml.safe_load((directory / f"{kind}.yaml").read_text())
            jsonschema.validate(doc, schema)
            if doc.get("schema_version") != "vocab-v1" or doc.get("kind") != kind:
                raise VocabularyError(f"Invalid {kind} document")
            self.documents[kind] = doc
            self.entries[kind] = {}
            self.aliases[kind] = {}
            for entry in doc["entries"]:
                code = entry["code"]
                if code in self.entries[kind] or not entry.get("name"):
                    raise VocabularyError(f"Duplicate code or absent name in {kind}: {code}")
                self.entries[kind][code] = entry
                for source, terms in entry["synonyms"].items():
                    for term in terms:
                        identity = (source, key(term))
                        prior = self.aliases[kind].get(identity)
                        if prior and prior != code:
                            raise VocabularyError(f"Conflicting {kind} alias: {identity}")
                        self.aliases[kind][identity] = code
        self.pilot_codes = {
            code for code, entry in self.entries["commodities"].items() if entry["pilot"]
        }
        self.rules = yaml.safe_load((directory / "field_rules.yaml").read_text())
        self.overrides = yaml.safe_load((directory / "country_overrides.yaml").read_text())
        jsonschema.validate(self.rules, schema["$defs"]["rules"])
        jsonschema.validate(self.overrides, schema["$defs"]["overrides"])
        self.country_aliases = {}
        for entry in self.overrides["entries"]:
            code = entry["code"]
            if code is not None and pycountry.countries.get(alpha_3=code) is None:
                raise VocabularyError(f"Country override is not ISO 3166-1: {code}")
            if not entry.get("reason"):
                raise VocabularyError("Country override needs an explanation")
            for alias in entry["aliases"]:
                identity = (entry["source_id"], key(alias))
                if identity in self.country_aliases:
                    raise VocabularyError(f"Duplicate country override: {identity}")
                self.country_aliases[identity] = entry
        countries = yaml.safe_load((directory / "countries.yaml").read_text())
        jsonschema.validate(countries, schema["$defs"]["countries"])
        expected = {c.alpha_3 for c in pycountry.countries}
        if {e["code"] for e in countries["entries"]} != expected:
            raise VocabularyError("Country vocabulary does not match pinned pycountry")
        for entry in countries["entries"]:
            country = pycountry.countries.get(alpha_3=entry["code"])
            if (entry["name"], entry["alpha_2"], entry["numeric"]) != (
                country.name,
                country.alpha_2,
                country.numeric,
            ):
                raise VocabularyError("Country names or codes differ from pinned pycountry")

    def lookup(self, kind: str, source: str, term: str) -> str | None:
        aliases = self.aliases[kind]
        return aliases.get((source, key(term)), aliases.get(("*", key(term))))

    def country(self, source: str, field: str, raw: str) -> dict:
        policy = self.rules.get("country_fields", {}).get(source, {}).get(field, "iso_name_or_code")
        if policy == "non_iso_source_code":
            return {"codes": [], "unmapped": [raw], "reason": "Source code is not ISO 3166-1"}
        entry = self.country_aliases.get(
            (source, key(raw)), self.country_aliases.get(("*", key(raw)))
        )
        if entry is not None:
            return {
                "codes": [entry["code"]] if entry["code"] else [],
                "unmapped": [] if entry["code"] else [raw],
                "reason": entry["reason"],
            }
        try:
            country = pycountry.countries.lookup(raw.strip())
        except LookupError:
            return {
                "codes": [],
                "unmapped": [raw],
                "reason": "No exact ISO name/code or reviewed override",
            }
        return {"codes": [country.alpha_3], "unmapped": [], "reason": "Exact pycountry lookup"}

    def resolve(self, kind: str, source: str, field: str, raw: str) -> dict:
        if kind == "country":
            return self.country(source, field, raw)
        name = {"commodity": "commodities", "deposit_type": "deposit_types", "status": "statuses"}[
            kind
        ]
        policy = None
        if kind == "commodity":
            policy = self.rules["commodity_fields"].get(source, {}).get(field)
            if policy is None:
                raise VocabularyError(f"No commodity field policy: {source}/{field}")
            explicit = policy.get("values", {}).get(raw)
            if explicit is not None:
                return {
                    "codes": explicit,
                    "unmapped": [],
                    "reason": "Reviewed composite or source code",
                }
        direct = self.lookup(name, source, raw)
        if direct:
            return {"codes": [direct], "unmapped": [], "reason": "Exact reviewed alias"}
        if kind != "commodity":
            return {"codes": [], "unmapped": [raw], "reason": "Unmapped source term"}
        parts = re.split(policy["split"], raw) if policy.get("split") else [raw]
        codes, unknown = set(), []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part in policy.get("values", {}):
                codes.update(policy["values"][part])
                continue
            code = self.lookup("commodities", source, part)
            if code:
                codes.add(code)
            else:
                unknown.append(part)
        return {"codes": sorted(codes), "unmapped": unknown, "reason": "Source-field tokenization"}

    def validate_rules(self) -> None:
        for source, fields in self.rules["commodity_fields"].items():
            for field, policy in fields.items():
                if policy.get("split"):
                    re.compile(policy["split"])
                for raw, codes in policy.get("values", {}).items():
                    if not codes or set(codes) - self.entries["commodities"].keys():
                        raise VocabularyError(f"Invalid explicit mapping: {source}/{field}/{raw}")
