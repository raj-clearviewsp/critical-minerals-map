"""Capture ArcGIS metadata and all records in bounded, verified ID batches."""

import json
from urllib.parse import urlencode

from pipeline.fetch.core import FetchError


def fetch_arcgis(config: dict, http, on_payload=None) -> dict:
    endpoint = config["url"].rstrip("/")
    if config.get("layers"):
        combined = {}
        if len(config["layers"]) != len(set(config["layers"])):
            raise FetchError("Duplicate ArcGIS layer IDs")
        for layer in config["layers"]:
            if type(layer) is not int or layer < 0:
                raise FetchError("Invalid ArcGIS layer ID")
            subconfig = {**config, "url": f"{endpoint}/{layer}", "layers": None}

            def capture_layer(name, payload, layer_id=layer):
                key = f"layer_{layer_id}_{name}"
                if on_payload:
                    on_payload(key, payload)
                else:
                    combined[key] = payload

            fetch_arcgis(subconfig, http, on_payload=capture_layer)
        return combined
    payloads = {}

    def request(name, path, parameters):
        payload = http.get(f"{endpoint}{path}?{urlencode(parameters)}")
        try:
            value = json.loads(payload.content)
        except (ValueError, UnicodeDecodeError) as error:
            raise FetchError("ArcGIS returned invalid JSON") from error
        if "error" in value:
            raise FetchError(f"ArcGIS service error: {value['error']}")
        if on_payload:
            on_payload(name, payload)
        else:
            payloads[name] = payload
        return value

    metadata = request("service.json", "", {"f": "json"})
    id_response = request(
        "object_ids.json",
        "/query",
        {
            "where": config.get("where", "1=1"),
            "returnIdsOnly": "true",
            "f": "json",
        },
    )
    ids = id_response.get("objectIds")
    field = id_response.get("objectIdFieldName") or metadata.get("objectIdField")
    if not isinstance(ids, list) or not field or id_response.get("exceededTransferLimit"):
        raise FetchError("ArcGIS did not return a complete unique object-ID list")
    if not all(type(value) is int for value in ids):
        raise FetchError("Invalid ArcGIS object IDs")
    if len(set(ids)) != len(ids):
        raise FetchError("ArcGIS returned duplicate object IDs")
    cap = min(1000, int(metadata.get("maxRecordCount", 1000)), int(config.get("page_size", 1000)))
    if cap < 1:
        raise FetchError("Invalid ArcGIS record cap")
    ids.sort()
    for offset in range(0, len(ids), cap):
        batch = ids[offset : offset + cap]
        page = request(
            f"page_{offset // cap:05d}.json",
            "/query",
            {
                "objectIds": ",".join(str(value) for value in batch),
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "f": "json",
            },
        )
        features = page.get("features")
        if page.get("exceededTransferLimit") or not isinstance(features, list):
            raise FetchError("ArcGIS truncated a result page")
        observed = [feature.get("attributes", {}).get(field) for feature in features]
        if len(observed) != len(batch) or set(observed) != set(batch):
            raise FetchError("ArcGIS page IDs do not match requested IDs")
    return payloads
