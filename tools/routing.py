import os

import requests

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/foot-hiking/geojson"
ORS_OPTIMIZATION_URL = "https://api.openrouteservice.org/optimization"


def _optimize_waypoint_order(start: dict, pois: list[dict], api_key: str) -> list[dict]:
    """
    Call the ORS Optimization API (Vroom) to find the optimal visiting order for
    the given POIs, starting and ending at `start`. Returns pois reordered.
    Falls back to original order if optimization fails.
    """
    if len(pois) <= 1:
        return pois

    jobs = [
        {"id": i + 1, "location": [p["lon"], p["lat"]]}
        for i, p in enumerate(pois)
    ]
    vehicle = {
        "id": 1,
        "profile": "foot-hiking",
        "start": [start["lon"], start["lat"]],
        "end": [start["lon"], start["lat"]],
    }

    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    body = {"jobs": jobs, "vehicles": [vehicle]}

    response = requests.post(ORS_OPTIMIZATION_URL, json=body, headers=headers, timeout=30)
    if not response.ok:
        # Optimization is best-effort — fall back to original order
        return pois

    data = response.json()
    # routes[0].steps contains the optimised visit sequence; skip start/end depot steps
    steps = data.get("routes", [{}])[0].get("steps", [])
    job_ids_in_order = [s["job"] for s in steps if s.get("type") == "job"]

    # Map job id back to poi (job id = index + 1)
    poi_by_id = {i + 1: p for i, p in enumerate(pois)}
    optimised = [poi_by_id[jid] for jid in job_ids_in_order if jid in poi_by_id]

    # Safety: if we lost any poi, fall back
    return optimised if len(optimised) == len(pois) else pois


def get_route(waypoints: list[dict]) -> dict:
    """
    `waypoints` is [start, poi1, poi2, ...]. The start point is also the finish.
    Intermediate POIs are optimised for shortest loop before routing.
    """
    api_key = os.environ.get("ORS_API_KEY", "")
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    start = waypoints[0]
    pois = waypoints[1:]

    # Optimise the visiting order of the intermediate POIs
    optimised_pois = _optimize_waypoint_order(start, pois, api_key)

    # Build coordinate list: start → optimised pois → start (closed loop)
    ordered = [start] + optimised_pois + [start]
    coords = [[w["lon"], w["lat"]] for w in ordered]

    body = {"coordinates": coords}
    response = requests.post(ORS_DIRECTIONS_URL, json=body, headers=headers, timeout=30)
    if not response.ok:
        raise RuntimeError(f"ORS {response.status_code}: {response.text}")

    data = response.json()
    feature = data["features"][0]
    geometry_coords = feature["geometry"]["coordinates"]  # [[lon, lat], ...]
    summary = feature["properties"]["summary"]

    return {
        "coordinates": geometry_coords,
        "distance_km": round(summary["distance"] / 1000, 2),
        "duration_min": round(summary["duration"] / 60, 1),
        # Return the optimised poi order so the caller can display them correctly
        "optimised_pois": optimised_pois,
    }
