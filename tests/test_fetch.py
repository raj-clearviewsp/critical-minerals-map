"""Synthetic HTTP checks for license gates, immutable downloads and ArcGIS paging."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
import yaml

from pipeline.fetch.arcgis import fetch_arcgis
from pipeline.fetch.core import FetchError, PoliteHTTP, fetch_source, verify_snapshot

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
DATA_URL = "https://source.example.test/data.csv"
ARCGIS = "https://gis.example.test/FeatureServer/0"


def registry(root: Path, **changes: object) -> dict:
    source = {
        "source_id": "test_source",
        "name": "Synthetic test fixture",
        "publisher": "Test publisher",
        "url": "https://source.example.test/",
        "license": "CC0-1.0",
        "redistribution_ok": True,
        "license_status": "confirmed_open",
        "license_evidence_url": "https://source.example.test/license",
        "access_status": "automated",
        "cadence": "manual",
        "parser": "not_implemented",
        "fetcher": {"kind": "files", "files": [{"filename": "data.csv", "url": DATA_URL}]},
    }
    source.update(changes)
    (root / "sources").mkdir(exist_ok=True)
    (root / "sources/test_source.yaml").write_text(yaml.safe_dump(source))
    return source


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.fixture
def http_factory():
    clients = []

    def make(handler):
        requests = []
        sleeps = []

        def record(request):
            requests.append(request)
            return handler(request)

        client = PoliteHTTP(
            "raj@satnamenergy.com", transport=httpx.MockTransport(record), sleep=sleeps.append
        )
        clients.append(client)
        return client, requests, sleeps

    yield make
    for client in clients:
        client.close()


def file_handler(content=b"id,name\n1,Synthetic fixture\n", robots="User-agent: *\nAllow: /\n"):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return httpx.Response(200, content=content, headers={"Content-Type": "text/csv"})

    return handle


@pytest.mark.parametrize(
    "change",
    [
        {"license_status": "unconfirmed"},
        {"redistribution_ok": False},
        {"license_evidence_url": ""},
        {"access_status": "manual_only"},
    ],
)
def test_uncleared_source_makes_zero_requests(tmp_path, http_factory, change):
    registry(tmp_path, **change)
    client, requests, _ = http_factory(file_handler())
    with pytest.raises(FetchError):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert requests == []
    assert not (tmp_path / "data/raw").exists()


def test_robots_denial_blocks_dataset_request(tmp_path, http_factory):
    registry(tmp_path)
    client, requests, _ = http_factory(file_handler(robots="User-agent: *\nDisallow: /\n"))
    with pytest.raises(FetchError, match="robots"):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert [request.url.path for request in requests] == ["/robots.txt"]
    assert not (tmp_path / "data/raw").exists()


@pytest.mark.parametrize("robots_status", [500])
def test_unavailable_robots_never_defaults_to_allow(tmp_path, http_factory, robots_status):
    registry(tmp_path)
    client, requests, _ = http_factory(lambda _: httpx.Response(robots_status))
    with pytest.raises(FetchError):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert all(request.url.path == "/robots.txt" for request in requests)


def test_redirect_checks_destination_robots_before_dataset(http_factory):
    def handle(request):
        if request.url.path == "/robots.txt":
            rule = "Disallow" if request.url.host == "blocked.example.test" else "Allow"
            return httpx.Response(200, text=f"User-agent: *\n{rule}: /\n")
        return httpx.Response(302, headers={"Location": "https://blocked.example.test/data.csv"})

    client, requests, _ = http_factory(handle)
    with pytest.raises(FetchError, match="robots"):
        client.get(DATA_URL)
    assert [(r.url.host, r.url.path) for r in requests] == [
        ("source.example.test", "/robots.txt"),
        ("source.example.test", "/data.csv"),
        ("blocked.example.test", "/robots.txt"),
    ]


@pytest.mark.parametrize("retry_after", ["7", "http-date"])
def test_retry_after_is_respected(http_factory, retry_after):
    attempts = 0
    delay = 7
    header = retry_after
    if retry_after == "http-date":
        delay = 25
        header = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)

    def handle(request):
        nonlocal attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": header})
        return httpx.Response(200, content=b"synthetic")

    client, _, sleeps = http_factory(handle)
    assert client.get(DATA_URL).content == b"synthetic"
    assert attempts == 2
    assert max(sleeps) >= delay


def test_retry_does_not_undercut_robots_crawl_delay(http_factory):
    attempts = 0

    def handle(request):
        nonlocal attempts
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\nCrawl-delay: 5\n")
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, content=b"synthetic")

    client, _, sleeps = http_factory(handle)
    client.get(DATA_URL)
    assert sleeps[-1] >= 5


def test_long_retry_after_stops_without_immediate_retry(http_factory):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(429, headers={"Retry-After": "120"})

    client, requests, _ = http_factory(handle)
    with pytest.raises(FetchError, match="longer wait"):
        client.get(DATA_URL)
    assert len([r for r in requests if r.url.path == "/data.csv"]) == 1


def test_manifest_covers_raw_bytes_and_cache_is_noop(tmp_path, http_factory):
    registry(tmp_path)
    client, requests, _ = http_factory(file_handler())
    result = fetch_source(tmp_path, "test_source", client, now=NOW)
    directory = Path(result["snapshot"])
    manifest = verify_snapshot(directory)
    assert directory == tmp_path / "data/raw/test_source/2026-10-28"
    assert result["status"] == "downloaded"
    assert manifest["source_id"] == "test_source"
    assert datetime.fromisoformat(manifest["retrieved_at"]).tzinfo is not None
    assert {entry["path"] for entry in manifest["files"]} == {"data.csv"}
    for entry in manifest["files"]:
        payload = (directory / entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["bytes"] == len(payload)
        assert entry["url"] == entry["final_url"] == DATA_URL
    assert all("raj@satnamenergy.com" in r.headers["User-Agent"] for r in requests)
    before = snapshot(tmp_path)
    request_count = len(requests)
    rerun = fetch_source(tmp_path, "test_source", client, now=NOW + timedelta(days=1))
    assert rerun["status"] == "cached_verified"
    assert len(requests) == request_count
    assert snapshot(tmp_path) == before


def test_refresh_identical_bytes_is_noop(tmp_path, http_factory):
    registry(tmp_path)
    client, requests, _ = http_factory(file_handler())
    first = fetch_source(tmp_path, "test_source", client, now=NOW)
    before = snapshot(tmp_path)
    count = len(requests)
    result = fetch_source(
        tmp_path, "test_source", client, now=NOW + timedelta(days=1), refresh=True
    )
    assert result["status"] == "unchanged"
    assert result["snapshot"] == first["snapshot"]
    assert len(requests) > count
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("next_day", [False, True])
def test_changed_bytes_never_replace_prior_snapshot(tmp_path, http_factory, next_day):
    registry(tmp_path)
    first, _, _ = http_factory(file_handler(b"old synthetic data"))
    original = fetch_source(tmp_path, "test_source", first, now=NOW)
    before = snapshot(Path(original["snapshot"]))
    second, _, _ = http_factory(file_handler(b"changed synthetic data"))
    if next_day:
        result = fetch_source(
            tmp_path, "test_source", second, now=NOW + timedelta(days=1), refresh=True
        )
        assert result["status"] == "downloaded"
        assert Path(result["snapshot"]).name == "2026-10-29"
    else:
        with pytest.raises(FetchError, match="existing daily snapshot"):
            fetch_source(tmp_path, "test_source", second, now=NOW, refresh=True)
    assert snapshot(Path(original["snapshot"])) == before


@pytest.mark.parametrize("corruption", ["changed-bytes", "extra-file", "missing-file"])
def test_corrupt_cached_snapshot_fails_before_network(tmp_path, http_factory, corruption):
    registry(tmp_path)
    client, requests, _ = http_factory(file_handler())
    result = fetch_source(tmp_path, "test_source", client, now=NOW)
    directory = Path(result["snapshot"])
    if corruption == "changed-bytes":
        (directory / "data.csv").write_bytes(b"changed")
    elif corruption == "extra-file":
        (directory / "unmanifested.csv").write_bytes(b"unexpected")
    else:
        (directory / "data.csv").unlink()
    count = len(requests)
    before = snapshot(tmp_path)
    with pytest.raises(FetchError):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert len(requests) == count
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("corruption", ["missing-url", "wrong-source", "naive-time"])
def test_corrupt_provenance_is_not_accepted_as_verified_cache(tmp_path, http_factory, corruption):
    registry(tmp_path)
    client, requests, _ = http_factory(file_handler())
    result = fetch_source(tmp_path, "test_source", client, now=NOW)
    path = Path(result["snapshot"]) / "manifest.json"
    manifest = json.loads(path.read_text())
    if corruption == "missing-url":
        del manifest["files"][0]["url"]
    elif corruption == "wrong-source":
        manifest["source_id"] = "unrelated_source"
    else:
        manifest["retrieved_at"] = "2026-10-28T12:00:00"
    path.write_text(json.dumps(manifest))
    before = snapshot(tmp_path)
    count = len(requests)
    with pytest.raises(FetchError):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert snapshot(tmp_path) == before
    assert len(requests) == count


def test_failed_multifile_acquisition_leaves_no_snapshot(tmp_path, http_factory):
    registry(
        tmp_path,
        fetcher={
            "kind": "files",
            "files": [
                {"filename": "a.csv", "url": DATA_URL},
                {"filename": "b.csv", "url": "https://source.example.test/missing.csv"},
            ],
        },
    )

    def handle(request):
        if request.url.path == "/missing.csv":
            return httpx.Response(404)
        return file_handler()(request)

    client, _, _ = http_factory(handle)
    with pytest.raises(FetchError):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert not (tmp_path / "data/raw").exists()


def test_download_byte_limit_is_enforced(http_factory):
    client, _, _ = http_factory(file_handler(b"0123456789"))
    with pytest.raises(FetchError, match="exceeds"):
        client.get(DATA_URL, limit=5)


def arcgis_handler(ids=(5, 1, 4, 3, 2), cap=2, fault=None):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path.endswith("/0"):
            return httpx.Response(200, json={"objectIdField": "OID", "maxRecordCount": cap})
        if request.url.params.get("returnIdsOnly"):
            response = {"objectIdFieldName": "OID", "objectIds": list(ids)}
            if fault == "truncated-ids":
                response["exceededTransferLimit"] = True
            return httpx.Response(200, json=response)
        batch = [int(item) for item in request.url.params["objectIds"].split(",")]
        if fault == "missing-id":
            batch.pop()
        if fault == "wrong-id":
            batch[0] = 999
        if fault == "duplicate-id" and len(batch) > 1:
            batch[1] = batch[0]
        response = {"features": [{"attributes": {"OID": item}} for item in reversed(batch)]}
        if fault == "truncated-page":
            response["exceededTransferLimit"] = True
        return httpx.Response(200, json=response)

    return handle


def test_arcgis_respects_service_cap_and_captures_every_id(http_factory):
    client, requests, _ = http_factory(arcgis_handler())
    payloads = fetch_arcgis({"url": ARCGIS, "page_size": 1000}, client)
    assert set(payloads) == {
        "service.json",
        "object_ids.json",
        "page_00000.json",
        "page_00001.json",
        "page_00002.json",
    }
    batches = [r.url.params["objectIds"] for r in requests if r.url.params.get("objectIds")]
    assert batches == ["1,2", "3,4", "5"]
    observed = []
    for name, payload in payloads.items():
        if name.startswith("page_"):
            observed.extend(f["attributes"]["OID"] for f in json.loads(payload.content)["features"])
    assert sorted(observed) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "fault", ["truncated-ids", "truncated-page", "missing-id", "wrong-id", "duplicate-id"]
)
def test_arcgis_never_accepts_truncated_or_mismatched_batches(http_factory, fault):
    client, _, _ = http_factory(arcgis_handler(fault=fault))
    with pytest.raises(FetchError):
        fetch_arcgis({"url": ARCGIS}, client)


@pytest.mark.parametrize("ids", [(1, 1), (True,), ("1",), ({"bad": 1},)])
def test_arcgis_rejects_invalid_object_ids(http_factory, ids):
    client, _, _ = http_factory(arcgis_handler(ids=ids))
    with pytest.raises(FetchError):
        fetch_arcgis({"url": ARCGIS}, client)


def test_arcgis_empty_layer_has_metadata_but_no_data_pages(http_factory):
    client, requests, _ = http_factory(arcgis_handler(ids=()))
    payloads = fetch_arcgis({"url": ARCGIS}, client)
    assert set(payloads) == {"service.json", "object_ids.json"}
    assert not any(r.url.params.get("objectIds") for r in requests)


def test_arcgis_multiple_layers_keep_separate_raw_pages(tmp_path, http_factory):
    def handle(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path.endswith(("/0", "/10")):
            return httpx.Response(200, json={"objectIdField": "OID", "maxRecordCount": 1})
        if request.url.params.get("returnIdsOnly"):
            return httpx.Response(200, json={"objectIdFieldName": "OID", "objectIds": [1, 2]})
        batch = request.url.params["objectIds"].split(",")
        return httpx.Response(
            200, json={"features": [{"attributes": {"OID": int(value)}} for value in batch]}
        )

    registry(
        tmp_path,
        fetcher={"kind": "arcgis", "url": ARCGIS.removesuffix("/0"), "layers": [0, 10]},
    )
    client, requests, _ = http_factory(handle)
    result = fetch_source(tmp_path, "test_source", client, now=NOW)
    manifest = verify_snapshot(Path(result["snapshot"]))
    assert {entry["path"] for entry in manifest["files"]} == {
        f"layer_{layer}_{name}"
        for layer in (0, 10)
        for name in ("service.json", "object_ids.json", "page_00000.json", "page_00001.json")
    }
    assert len([r for r in requests if r.url.params.get("objectIds")]) == 4


def acquisition_approval(root: Path, **changes: object) -> dict:
    approval = {
        "approved_by": "Raj Sawhney",
        "approved_at": NOW.isoformat(),
        "decision": "allow_acquisition",
        "reason": "Test approval for acquisition only; redistribution remains restricted.",
    }
    approval.update(changes)
    (root / "overrides").mkdir(exist_ok=True)
    (root / "overrides/source_acquisition_approvals.yaml").write_text(
        yaml.safe_dump({"approvals": {"test_source": approval}})
    )
    return approval


def test_restricted_source_without_acquisition_override_is_denied(tmp_path, http_factory):
    registry(tmp_path, license="CC-BY-NC-4.0", license_status="restricted", redistribution_ok=False)
    client, requests, _ = http_factory(file_handler())
    with pytest.raises(FetchError, match="license not cleared"):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert requests == []
    assert not (tmp_path / "data/raw").exists()


def test_acquisition_override_preserves_restriction_and_approval(tmp_path, http_factory):
    registry(tmp_path, license="CC-BY-NC-4.0", license_status="restricted", redistribution_ok=False)
    approval = acquisition_approval(tmp_path)
    client, requests, _ = http_factory(file_handler())
    result = fetch_source(tmp_path, "test_source", client, now=NOW)
    manifest = verify_snapshot(Path(result["snapshot"]))
    assert result["status"] == "downloaded"
    assert manifest["redistribution_ok"] is False
    assert manifest["license"] == "CC-BY-NC-4.0"
    assert manifest["acquisition_approval"] == approval
    assert any(request.url.path == "/data.csv" for request in requests)


def test_incomplete_acquisition_override_does_not_authorize_requests(tmp_path, http_factory):
    registry(tmp_path, license_status="unconfirmed", redistribution_ok=False)
    acquisition_approval(tmp_path, approved_by="")
    client, requests, _ = http_factory(file_handler())
    with pytest.raises(FetchError, match="license not cleared"):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert requests == []


def test_publisher_checksum_mismatch_rejects_before_snapshot(tmp_path, http_factory):
    registry(
        tmp_path,
        fetcher={
            "kind": "files",
            "files": [
                {
                    "filename": "data.csv",
                    "url": DATA_URL,
                    "publisher_checksum": "sha256:" + "0" * 64,
                }
            ],
        },
    )
    client, _, _ = http_factory(file_handler())
    with pytest.raises(FetchError, match="Publisher checksum mismatch"):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert not (tmp_path / "data/raw").exists()
    assert not (tmp_path / "data/.incoming").exists()


def test_acquisition_override_cannot_bypass_robots_denial(tmp_path, http_factory):
    registry(tmp_path, license_status="unconfirmed", redistribution_ok=False)
    acquisition_approval(tmp_path)
    client, requests, _ = http_factory(file_handler(robots="User-agent: *\nDisallow: /\n"))
    with pytest.raises(FetchError, match="robots"):
        fetch_source(tmp_path, "test_source", client, now=NOW)
    assert [request.url.path for request in requests] == ["/robots.txt"]


def test_robots_longest_matching_wildcard_allow_is_honored(http_factory):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /api\n"
                "Allow: /api/records/*/files\nCrawl-delay: 10\n",
            )
        return httpx.Response(200, content=b"permitted file")

    client, calls, sleeps = http_factory(handler)
    result = client.get("https://source.example/api/records/123/files/data.csv")
    assert result.content == b"permitted file"
    assert any(delay >= 9 for delay in sleeps)
    with pytest.raises(FetchError, match="robots"):
        client.get("https://source.example/api/private")


def test_metadata_change_with_identical_download_keeps_existing_snapshot(tmp_path, http_factory):
    registry(tmp_path)
    client, _, _ = http_factory(file_handler())
    first = fetch_source(tmp_path, "test_source", client, now=NOW)
    snapshot = Path(first["snapshot"])
    manifest_before = (snapshot / "manifest.json").read_bytes()
    source_file = tmp_path / "sources/test_source.yaml"
    source = yaml.safe_load(source_file.read_text())
    source["notes"] = ["License evidence clarified without changing downloaded bytes."]
    source_file.write_text(yaml.safe_dump(source))
    second = fetch_source(tmp_path, "test_source", client, now=NOW)
    assert second["status"] == "unchanged"
    assert (snapshot / "manifest.json").read_bytes() == manifest_before


@pytest.mark.parametrize("robots_status", [401, 403, 404])
def test_unavailable_robots_does_not_bypass_dataset_access_control(http_factory, robots_status):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(robots_status)
        return httpx.Response(403)

    client, calls, _ = http_factory(handler)
    with pytest.raises(FetchError, match="403"):
        client.get(DATA_URL)
    assert len(calls) == 2


def test_unavailable_robots_allows_registered_public_api(http_factory):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(403)
        return httpx.Response(200, content=b"public api response")

    client, _, _ = http_factory(handler)
    assert client.get(DATA_URL).content == b"public api response"
