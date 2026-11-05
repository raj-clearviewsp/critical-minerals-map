"""Check reported points against the reported country, allowing 25 km at the border."""

import gzip
import json
import math

from pyproj import CRS, Transformer
from shapely.geometry import Point, box, shape
from shapely.ops import transform, unary_union

from .common import sha


def boundaries(root):
    directory = root / "sources/boundaries"
    raw = (directory / "countries-50m.geojson.gz").read_bytes()
    meta = json.loads((directory / "manifest.json").read_bytes())
    if sha(raw) != meta["geometry_sha256"]:
        raise ValueError("Country boundaries differ from their source receipt")
    grouped = {}
    for feature in json.loads(gzip.decompress(raw))["features"]:
        geom = shape(feature["geometry"])
        if not geom.is_valid:
            from shapely import make_valid

            geom = make_valid(geom)
        for code in feature["properties"]["iso3"]:
            grouped.setdefault(code, []).append(geom)
    return {code: unary_union(parts) for code, parts in grouped.items()}


def check_point(lon, lat, country, shapes):
    if (
        not all(
            isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)
            for v in (lon, lat)
        )
        or not (-180 <= lon <= 180 and -90 <= lat <= 90)
        or (lon, lat) == (0, 0)
    ):
        return "invalid_coordinate"
    geom = shapes.get(country)
    if geom is None:
        return "country_unverified"
    point = Point(lon, lat)
    if geom.covers(point):
        return "inside_country"
    # A local azimuthal equidistant projection measures radial distance from the source point.
    # Clip to a generous neighborhood first, including both sides of the date line.
    dx = min(180, 1.0 / max(0.001, math.cos(math.radians(lat))))
    projector = Transformer.from_crs(
        4326,
        CRS.from_proj4(f"+proj=aeqd +lat_0={lat} +lon_0={lon} +ellps=WGS84 +units=m"),
        always_xy=True,
    )
    for center in (lon, lon - 360, lon + 360):
        clipped = geom.intersection(box(center - dx, lat - 1, center + dx, lat + 1))
        if (
            not clipped.is_empty
            and transform(projector.transform, clipped).distance(Point(0, 0)) <= 25000
        ):
            return "within_25_km"
    return "outside_tolerance"
