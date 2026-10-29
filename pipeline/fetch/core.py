"""License-gated, polite downloads into immutable daily source snapshots."""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import jsonschema
import yaml
from protego import Protego

from pipeline.scaffold import digest, json_bytes

SOURCE_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
MAX_BYTES = 150 * 1024 * 1024
SCHEMAS = Path(__file__).resolve().parents[2] / "schemas"


class FetchError(RuntimeError):
    """Download is blocked, inconsistent, oversized or would alter a raw snapshot."""


def load_source(root: Path, source_id: str) -> dict:
    if not SOURCE_ID.fullmatch(source_id):
        raise FetchError("Invalid source ID")
    try:
        value = yaml.safe_load((root / "sources" / f"{source_id}.yaml").read_text())
    except (OSError, yaml.YAMLError) as error:
        raise FetchError(f"Cannot load source registry: {source_id}") from error
    required = {
        "source_id",
        "name",
        "publisher",
        "url",
        "license",
        "redistribution_ok",
        "license_status",
        "license_evidence_url",
        "access_status",
        "cadence",
        "parser",
        "fetcher",
    }
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value["source_id"] != source_id
    ):
        raise FetchError(f"Incomplete source registry: {source_id}")
    try:
        jsonschema.validate(value, json.loads((SCHEMAS / "source.schema.json").read_text()))
    except jsonschema.ValidationError as error:
        raise FetchError(f"Invalid source registry: {source_id}") from error
    return value


def validate_source(source: dict, acquisition_approved: bool = False) -> None:
    if not acquisition_approved and (
        source["license_status"] != "confirmed_open" or source["redistribution_ok"] is not True
    ):
        raise FetchError(f"{source['source_id']}: license not cleared; no dataset request made")
    if not source["license_evidence_url"]:
        raise FetchError("License decision lacks evidence")
    if source["access_status"] != "automated":
        raise FetchError(f"{source['source_id']}: automated access not approved")
    fetcher = source["fetcher"]
    if fetcher.get("kind") not in ("files", "arcgis"):
        raise FetchError("No implemented fetcher for this source")
    if fetcher["kind"] == "files":
        if not fetcher.get("files"):
            raise FetchError("No verified download URLs in the registry")
        names = [item.get("filename", "") for item in fetcher["files"]]
        if len(set(names)) != len(names) or any(not FILENAME.fullmatch(name) for name in names):
            raise FetchError("Invalid or duplicate raw filenames")
        if "manifest.json" in names:
            raise FetchError("manifest.json is reserved")
        for item in fetcher["files"]:
            validate_url(item["url"])
    else:
        validate_url(fetcher["url"])


def validate_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise FetchError("Downloads require an HTTPS URL without embedded credentials")


def user_agent(contact: str) -> str:
    if not contact or any(char.isspace() or ord(char) < 32 for char in contact):
        raise FetchError(
            "ATLAS_CONTACT must be a public contact email or HTTPS project contact URL"
        )
    if "@" in contact and not contact.startswith("https://"):
        if not re.fullmatch(r"[^@/]+@[^@/]+\.[^@/]+", contact):
            raise FetchError("Invalid contact email")
        contact = f"mailto:{contact}"
    elif not contact.startswith("https://"):
        raise FetchError("ATLAS_CONTACT must be a contact email or HTTPS URL")
    return f"CriticalMineralsAtlas/0.1 (+{contact})"


@dataclass
class Payload:
    content: bytes
    requested_url: str
    final_url: str
    headers: dict


class PoliteHTTP:
    """Single-client host pacing, robots checks, bounded streams and safe redirects."""

    def __init__(self, contact: str, *, transport=None, sleep=time.sleep, interval=1.0):
        self.agent = user_agent(contact)
        self.client = httpx.Client(
            headers={"User-Agent": self.agent},
            timeout=60,
            follow_redirects=False,
            transport=transport,
        )
        self.sleep = sleep
        self.interval = max(1.0, interval)
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, Protego] = {}

    def close(self) -> None:
        self.client.close()

    def _request(self, url: str, limit: int, accept: str | None = None) -> Payload:
        origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
        elapsed = time.monotonic() - self.last_request.get(origin, 0)
        if elapsed < self.interval:
            self.sleep(self.interval - elapsed)
        self.last_request[origin] = time.monotonic()
        for attempt in range(3):
            try:
                with self.client.stream(
                    "GET", url, headers={"Accept": accept} if accept else None
                ) as response:
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        retry = response.headers.get("Retry-After", "")
                        if retry.isdigit():
                            retry_wait = float(retry)
                        elif retry:
                            try:
                                retry_wait = max(
                                    0,
                                    (
                                        parsedate_to_datetime(retry) - datetime.now(UTC)
                                    ).total_seconds(),
                                )
                            except (ValueError, TypeError) as error:
                                raise FetchError(
                                    "Invalid Retry-After header; retry this run later"
                                ) from error
                        else:
                            retry_wait = 2 ** (attempt + 1)
                        if max(retry_wait, self.interval) > 60:
                            raise FetchError("Server requests a longer wait; retry this run later")
                        self.sleep(max(retry_wait, self.interval))
                        self.last_request[origin] = time.monotonic()
                        continue
                    headers = dict(response.headers)
                    if response.is_redirect:
                        return Payload(b"", url, url, {**headers, "_status": response.status_code})
                    if (
                        400 <= response.status_code < 500
                        and response.status_code != 429
                        and urlsplit(url).path == "/robots.txt"
                    ):
                        # RFC9309 2.3.1.3: unavailable robots is not an explicit deny.
                        # This applies only to robots; dataset 401/403 still fail below.
                        return Payload(b"", url, url, {"_status": response.status_code})
                    response.raise_for_status()
                    if int(headers.get("content-length", 0)) > limit:
                        raise FetchError(f"Download exceeds {limit} bytes")
                    buffer = io.BytesIO()
                    for chunk in response.iter_bytes():
                        if buffer.tell() + len(chunk) > limit:
                            raise FetchError(f"Download exceeds {limit} bytes")
                        buffer.write(chunk)
                    return Payload(buffer.getvalue(), url, url, headers)
            except (httpx.HTTPError, ValueError) as error:
                raise FetchError(f"HTTP request failed for {url}: {error}") from error
        raise FetchError(f"Retry limit exceeded: {url}")

    def _robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            robot_url = origin + "/robots.txt"
            response = self._request(robot_url, 2 * 1024 * 1024)
            for _ in range(5):
                if "location" not in response.headers:
                    break
                robot_url = urljoin(robot_url, response.headers["location"])
                validate_url(robot_url)
                response = self._request(robot_url, 2 * 1024 * 1024)
            else:
                raise FetchError("Too many robots.txt redirects")
            text = response.content.decode("utf-8", errors="replace")
            if text.lstrip().lower().startswith(("<!doctype html", "<html")):
                raise FetchError("robots.txt returned HTML; automated access remains unverified")
            parser = Protego.parse(text)
            self.robots[origin] = parser
        parser = self.robots[origin]
        if not parser.can_fetch(url, self.agent):
            raise FetchError(f"robots.txt disallows automated access: {url}")
        delay = parser.crawl_delay(self.agent)
        if delay:
            self.interval = max(self.interval, delay)
        rate = parser.request_rate(self.agent)
        if rate and rate.requests:
            self.interval = max(self.interval, rate.seconds / rate.requests)

    def get(self, url: str, limit: int = MAX_BYTES, accept: str | None = None) -> Payload:
        validate_url(url)
        requested = url
        for _ in range(6):
            self._robots(url)
            response = self._request(url, limit, accept)
            if "location" not in response.headers:
                response.requested_url = requested
                response.final_url = url
                return response
            url = urljoin(url, response.headers["location"])
            validate_url(url)
        raise FetchError("Too many download redirects")


def file_entry(name: str, payload: Payload) -> dict:
    return {
        "path": name,
        "url": payload.requested_url,
        "final_url": payload.final_url,
        "bytes": len(payload.content),
        "sha256": digest(payload.content),
        "content_type": payload.headers.get("content-type"),
        "etag": payload.headers.get("etag"),
        "last_modified": payload.headers.get("last-modified"),
    }


def verify_snapshot(directory: Path) -> dict:
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        jsonschema.validate(
            manifest,
            json.loads((SCHEMAS / "raw-manifest.schema.json").read_text()),
            format_checker=jsonschema.FormatChecker(),
        )
        if manifest.get("schema_version") != "raw-v1" or not manifest.get("files"):
            raise FetchError("Missing raw manifest version or files")
        names = [entry["path"] for entry in manifest["files"]]
        if len(names) != len(set(names)) or any(not FILENAME.fullmatch(name) for name in names):
            raise FetchError("Invalid manifest file names")
        actual = {path.name for path in directory.iterdir() if path.name != "manifest.json"}
        if actual != set(names):
            raise FetchError("Manifest does not cover every raw file")
        for entry in manifest["files"]:
            validate_url(entry["url"])
            validate_url(entry["final_url"])
            data = (directory / entry["path"]).read_bytes()
            if len(data) != entry["bytes"] or digest(data) != entry["sha256"]:
                raise FetchError(f"Raw file checksum or size mismatch: {entry['path']}")
        if datetime.fromisoformat(manifest["retrieved_at"]).tzinfo is None:
            raise FetchError("Raw retrieval timestamp must include a timezone")
        if not SOURCE_ID.fullmatch(manifest["source_id"]):
            raise FetchError("Invalid manifest source ID")
        validate_url(manifest["source_url"])
        validate_url(manifest["license_evidence_url"])
        if not manifest["license"] or not re.fullmatch(
            r"[0-9a-f]{64}", manifest["registry_sha256"]
        ):
            raise FetchError("Missing source license or registry checksum")
        return manifest
    except FetchError:
        raise
    except Exception as error:
        raise FetchError(f"Invalid raw snapshot: {directory}") from error


def fetch_source(root: Path, source_id: str, http: PoliteHTTP, *, now=None, refresh=False) -> dict:
    source = load_source(root, source_id)
    approval_path = root / "overrides/source_acquisition_approvals.yaml"
    approval = None
    if approval_path.exists():
        approvals = yaml.safe_load(approval_path.read_text()) or {}
        approval = approvals.get("approvals", {}).get(source_id)
    acquisition_approved = bool(
        approval
        and approval.get("approved_by")
        and approval.get("decision") == "allow_acquisition"
        and approval.get("reason")
        and approval.get("approved_at")
    )
    validate_source(source, acquisition_approved=acquisition_approved)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise FetchError("Retrieval timestamp must be timezone-aware")
    now = now.astimezone(UTC)
    registry_hash = hashlib.sha256(json_bytes(source)).hexdigest()
    base = root / "data/raw" / source_id
    existing = sorted(base.glob("????-??-??"), reverse=True)
    if existing:
        latest = verify_snapshot(existing[0])
        if latest["source_id"] != source_id:
            raise FetchError("Raw manifest source identity does not match the requested source")
        if latest.get("registry_sha256") == registry_hash and not refresh:
            return {"status": "cached_verified", "snapshot": str(existing[0]), "manifest": latest}
    incoming = root / "data/.incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=source_id + "-", dir=incoming))
    entries = []

    def capture(name: str, payload: Payload) -> None:
        if not FILENAME.fullmatch(name) or (temporary / name).exists():
            raise FetchError("Invalid or duplicate raw filename")
        (temporary / name).write_bytes(payload.content)
        entries.append(file_entry(name, payload))

    try:
        if source["fetcher"]["kind"] == "files":
            for item in source["fetcher"]["files"]:
                payload = (
                    http.get(item["url"], accept=item["accept_header"])
                    if item.get("accept_header")
                    else http.get(item["url"])
                )
                if item["filename"].split(".")[-1].lower() in ("zip", "xlsx", "xls", "csv", "pdf"):
                    if "text/html" in payload.headers.get("content-type", ""):
                        raise FetchError(f"Unexpected HTML at download URL: {item['url']}")
                if item.get("publisher_checksum"):
                    algorithm, expected = item["publisher_checksum"].split(":", 1)
                    algorithm = algorithm.lower().replace("-", "")
                    if algorithm not in ("md5", "sha256"):
                        raise FetchError("Unsupported publisher checksum algorithm")
                    if hashlib.new(algorithm, payload.content).hexdigest() != expected.lower():
                        raise FetchError(f"Publisher checksum mismatch: {item['filename']}")
                capture(item["filename"], payload)
        else:
            from pipeline.fetch.arcgis import fetch_arcgis

            fetch_arcgis(source["fetcher"], http, on_payload=capture)
        if existing:
            old = [(row["path"], row["sha256"]) for row in latest["files"]]
            new = [(row["path"], row["sha256"]) for row in entries]
            if old == new:
                return {"status": "unchanged", "snapshot": str(existing[0]), "manifest": latest}
        target = base / now.date().isoformat()
        if target.exists():
            raise FetchError(
                "Source changed within an existing daily snapshot; preserve it for review"
            )
        manifest = {
            "schema_version": "raw-v1",
            "source_id": source_id,
            "retrieved_at": now.isoformat(),
            "registry_sha256": registry_hash,
            "source_url": source["url"],
            "license": source["license"],
            "license_evidence_url": source["license_evidence_url"],
            "redistribution_ok": source["redistribution_ok"],
            "acquisition_approval": approval if acquisition_approved else None,
            "files": entries,
        }
        (temporary / "manifest.json").write_bytes(json_bytes(manifest))
        verify_snapshot(temporary)
        base.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FetchError("Snapshot already created by another run")
        temporary.rename(target)
        return {"status": "downloaded", "snapshot": str(target), "manifest": manifest}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        with suppress(OSError):
            incoming.rmdir()
