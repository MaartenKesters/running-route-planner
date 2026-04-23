import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM tag filters per route type
_TAG_FILTERS: dict[str, list[str]] = {
    "Cultural": [
        '"tourism"~"museum|gallery|artwork|attraction|viewpoint|heritage"',
        '"historic"~"monument|memorial|castle|ruins|archaeological_site|building|church"',
        '"amenity"~"theatre|cinema|arts_centre|library"',
    ],
    "Nature": [
        '"leisure"~"park|nature_reserve|garden|marina"',
        '"natural"~"peak|viewpoint|beach|waterfall|cliff|wood|cave_entrance"',
        '"tourism"~"viewpoint|camp_site|picnic_site"',
        '"landuse"~"forest|meadow"',
    ],
    "Mixed": [
        '"tourism"~"museum|gallery|artwork|attraction|viewpoint|heritage"',
        '"historic"~"monument|memorial|castle|ruins|archaeological_site|building|church"',
        '"amenity"~"theatre|cinema|arts_centre|library"',
        '"leisure"~"park|nature_reserve|garden"',
        '"natural"~"peak|viewpoint|beach|waterfall|cliff|cave_entrance"',
    ],
}

def _interest_score(tags: dict) -> int:
    """Higher = more notable. Based on OSM metadata richness."""
    score = 0
    if "wikipedia" in tags:   score += 4
    if "wikidata" in tags:    score += 3
    if "wikimedia_commons" in tags: score += 2
    if "image" in tags:       score += 2
    if "website" in tags:     score += 1
    if "description" in tags: score += 1
    # Prefer specific high-value tourism/historic values
    high_value = {"museum", "castle", "monument", "ruins", "archaeological_site",
                  "theatre", "viewpoint", "attraction", "nature_reserve"}
    for key in ("tourism", "historic", "amenity", "leisure"):
        if tags.get(key) in high_value:
            score += 2
            break
    return score


_TYPE_LABEL: dict[str, str] = {
    "tourism": "tourism",
    "historic": "historic",
    "amenity": "culture",
    "leisure": "leisure",
    "natural": "nature",
    "landuse": "nature",
}


def _build_overpass_query(lat: float, lon: float, radius_m: int, route_type: str) -> str:
    filters = _TAG_FILTERS.get(route_type, _TAG_FILTERS["Mixed"])
    union_parts = []
    for f in filters:
        union_parts.append(f'node[{f}](around:{radius_m},{lat},{lon});')
        union_parts.append(f'way[{f}](around:{radius_m},{lat},{lon});')
    union = "\n  ".join(union_parts)
    return f"[out:json][timeout:30];\n(\n  {union}\n);\nout center tags;"


def get_pois(lat: float, lon: float, radius_m: int, route_type: str) -> list[dict]:
    query = _build_overpass_query(lat, lon, radius_m, route_type)
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": "running-route-planner/1.0", "Accept": "*/*"},
        timeout=45,
    )
    if not response.ok:
        raise RuntimeError(f"Overpass API error {response.status_code}: {response.text[:200]}")

    elements = response.json().get("elements", [])

    seen: set[str] = set()
    candidates: list[dict] = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name:en") or tags.get("int_name") or tags.get("name")
        if not name or name in seen:
            continue
        seen.add(name)

        if el["type"] == "node":
            poi_lat, poi_lon = el["lat"], el["lon"]
        else:
            center = el.get("center", {})
            poi_lat = center.get("lat")
            poi_lon = center.get("lon")
            if poi_lat is None:
                continue

        poi_type = "poi"
        for key in ("tourism", "historic", "amenity", "leisure", "natural", "landuse"):
            if key in tags:
                poi_type = _TYPE_LABEL.get(key, key)
                break

        candidates.append({
            "name": name,
            "lat": float(poi_lat),
            "lon": float(poi_lon),
            "type": poi_type,
            "tags": tags,
            "_score": _interest_score(tags),
        })

    # Return the 25 most interesting POIs, score descending
    candidates.sort(key=lambda p: p["_score"], reverse=True)
    for c in candidates[:10]:
        del c["_score"]
    return candidates[:10]
