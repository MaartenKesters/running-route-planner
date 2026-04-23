import json
import math
import os

from openai import OpenAI

_client = None

# Roads are typically ~35% longer than straight-line distances
_ROAD_FACTOR = 1.35


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def estimate_loop_km(start_lat: float, start_lon: float, pois: list[dict]) -> float:
    """Estimate road distance of a loop: start → poi[0] → ... → poi[-1] → start."""
    if not pois:
        return 0.0
    points = [{"lat": start_lat, "lon": start_lon}] + pois + [{"lat": start_lat, "lon": start_lon}]
    total = sum(
        _haversine_km(points[i]["lat"], points[i]["lon"], points[i + 1]["lat"], points[i + 1]["lon"])
        for i in range(len(points) - 1)
    )
    return round(total * _ROAD_FACTOR, 2)


def translate_poi_names(pois: list[dict]) -> list[dict]:
    """Translate non-ASCII POI names to English in a single OpenAI call."""
    non_english = [(i, p["name"]) for i, p in enumerate(pois) if not p["name"].isascii()]
    if not non_english:
        return pois

    numbered = "\n".join(f"{seq}: {name}" for seq, (_, name) in enumerate(non_english))
    response = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content":
            f"Translate these place names to English. "
            f"Return JSON with key 'translations' — an array of translated names in the same order.\n{numbered}"}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    translations = json.loads(response.choices[0].message.content).get("translations", [])

    result = [dict(p) for p in pois]
    for seq, (poi_idx, _) in enumerate(non_english):
        if seq < len(translations) and translations[seq]:
            result[poi_idx]["name"] = translations[seq]
    return result


def auto_select_waypoints(
    start_lat: float,
    start_lon: float,
    pois: list[dict],
    distance_km: float,
    route_type: str,
) -> list[dict]:
    """AI-driven waypoint selection used when the user has not manually chosen any POIs.

    Returns the selected POIs in an arbitrary order — ORS will optimise the final
    visiting order when the route is generated.
    """
    target_haversine = distance_km / _ROAD_FACTOR

    poi_lines = []
    for i, p in enumerate(pois):
        dist = _haversine_km(start_lat, start_lon, p["lat"], p["lon"])
        poi_lines.append(f"{i}: {p['name']} ({p['type']}, {dist:.2f} km from start)")

    prompt = f"""You are a running route planner.

Start: ({start_lat:.4f}, {start_lon:.4f})
Target road distance: {distance_km} km
Route type: {route_type}

Roads are ~35% longer than straight-line distances, so the total straight-line loop
distance (start → wp1 → … → wpN → start) should be approximately {target_haversine:.2f} km.

Available POIs (index: name, type, straight-line km from start):
{chr(10).join(poi_lines)}

Select 3-5 POIs such that:
1. The total straight-line loop distance is close to {target_haversine:.2f} km.
2. POIs span different compass directions around the start for a genuine loop.

The routing engine will determine the optimal visiting order — just pick the right POIs.
Return JSON: {{"selected": [<indices>]}}"""

    response = _get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    data = json.loads(response.choices[0].message.content)
    indices = data.get("selected", [])
    return [pois[i] for i in indices if 0 <= i < len(pois)]


def describe_route(waypoints: list[dict], start_location: str, route_type: str) -> str:
    waypoint_list = "\n".join(f"- {w['name']} ({w['type']})" for w in waypoints)

    prompt = f"""You are a running route guide.

Location: {start_location}
Route type: {route_type}
Waypoints (in order):
{waypoint_list}

Write:
1. A 3-4 sentence overview of the route (mood, highlights, what to expect).
2. Then for each waypoint, one line: "• [Name]: [brief cultural or nature note]"

Keep it concise and practical for a runner checking this on their phone."""

    response = _get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()
