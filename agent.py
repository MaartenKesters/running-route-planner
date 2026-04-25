import itertools
import json
import math
import os

from openai import OpenAI

from tools.routing import get_route

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


def _nearest_neighbor_tour_km(start_lat: float, start_lon: float, pois: list[dict]) -> float:
    """Estimate road distance of the best greedy loop through a set of POIs.

    Uses a nearest-neighbor heuristic: from the current position always visit
    the closest remaining POI, then return to start. Multiplies the straight-line
    total by _ROAD_FACTOR to approximate real road distance.
    """
    unvisited = list(pois)
    cur_lat, cur_lon = start_lat, start_lon
    total = 0.0
    while unvisited:
        nearest = min(unvisited, key=lambda p: _haversine_km(cur_lat, cur_lon, p["lat"], p["lon"]))
        total += _haversine_km(cur_lat, cur_lon, nearest["lat"], nearest["lon"])
        cur_lat, cur_lon = nearest["lat"], nearest["lon"]
        unvisited.remove(nearest)
    total += _haversine_km(cur_lat, cur_lon, start_lat, start_lon)
    return total * _ROAD_FACTOR


def _greedy_augment(
    start_lat: float,
    start_lon: float,
    current: list[dict],
    optional_candidates: list[dict],
    distance_km: float,
) -> list[dict]:
    """Add optional POIs one by one (highest score first) while tour stays within +20% of target."""
    for candidate in optional_candidates:
        trial = current + [candidate]
        if _nearest_neighbor_tour_km(start_lat, start_lon, trial) <= distance_km * 1.20:
            current = trial
    return current


def _auto_select(
    start_lat: float,
    start_lon: float,
    candidates: list[dict],
    distance_km: float,
) -> list[dict]:
    """Combinatorial search over 3–5 POI subsets; returns the combo with the most POIs within
    the distance budget. Score is the tiebreak when two combos have the same POI count."""
    best_combo: list[dict] | None = None
    best_key = (-1, -1)  # (poi_count, total_score)

    def _search(tolerance: float) -> list[dict] | None:
        nonlocal best_combo, best_key
        for n in (3, 4, 5):
            if n > len(candidates):
                continue
            for combo in itertools.combinations(candidates, n):
                est_km = _nearest_neighbor_tour_km(start_lat, start_lon, list(combo))
                if abs(est_km - distance_km) / distance_km <= tolerance:
                    combo_score = sum(p.get("_score", 0) for p in combo)
                    new_key = (len(combo), combo_score)
                    if new_key > best_key:
                        best_key = new_key
                        best_combo = list(combo)
        return best_combo

    if _search(0.20) is None:
        _search(0.35)

    if best_combo is not None:
        return best_combo

    # Fallback: size-3 combo closest to target distance.
    n = min(3, len(candidates))
    if len(candidates) < n:
        return candidates
    return list(min(
        itertools.combinations(candidates, n),
        key=lambda combo: abs(_nearest_neighbor_tour_km(start_lat, start_lon, list(combo)) - distance_km),
    ))


def select_waypoints(
    start_lat: float,
    start_lon: float,
    required_pois: list[dict],
    all_pois: list[dict],
    distance_km: float,
) -> tuple[list[dict], float]:
    """Select the final set of POIs for a route, returning (pois, estimated_road_km).

    - required_pois: user-selected POIs that must appear in the route (may be empty).
    - all_pois: full pool of available POIs (includes required ones).

    When required_pois is empty, runs the full combinatorial search over all_pois.
    When required_pois is non-empty, locks them in and greedily augments with the
    highest-scored optional POIs that keep the tour within +20% of distance_km.
    If the required set is already over budget, it is returned unchanged.
    """
    # Optional pool — candidates not already required, sorted by score.
    required_names = {p["name"] for p in required_pois}
    optional = sorted(
        [p for p in all_pois if p["name"] not in required_names],
        key=lambda p: p.get("_score", 0),
        reverse=True,
    )

    if not required_pois:
        # Pure auto mode.
        candidates = sorted(all_pois, key=lambda p: p.get("_score", 0), reverse=True)
        chosen = _auto_select(start_lat, start_lon, candidates, distance_km)
        return chosen, _nearest_neighbor_tour_km(start_lat, start_lon, chosen)

    # Required POIs locked in — check current estimate.
    est = _nearest_neighbor_tour_km(start_lat, start_lon, required_pois)

    if est > distance_km * 1.20:
        # Already over budget — honour the user's selection, caller will warn.
        return required_pois, est

    # Within budget or under — augment with optional POIs.
    augmented = _greedy_augment(start_lat, start_lon, required_pois, optional, distance_km)
    return augmented, _nearest_neighbor_tour_km(start_lat, start_lon, augmented)


_MAX_REFINEMENT_ITERATIONS = 3
_TOLERANCE = 0.10  # ±10% of target is "good enough"


def plan_route(
    start_lat: float,
    start_lon: float,
    required_pois: list[dict],
    all_pois: list[dict],
    distance_km: float,
) -> dict:
    """Select POIs, call ORS, and refine until the real road distance is within ±10%
    of distance_km or the iteration cap is reached.

    Required POIs (user-selected) are never removed during refinement.
    Returns the get_route() result dict extended with 'input_pois' (the final POI
    list before ORS optimisation, used as a display fallback in the UI).
    Always returns the route whose distance was closest to the target.
    """
    current_pois, _ = select_waypoints(start_lat, start_lon, required_pois, all_pois, distance_km)

    start = {"name": "Start", "lat": start_lat, "lon": start_lon}
    required_names = {p["name"] for p in required_pois}

    # Optional pool for augmentation: all_pois minus required, sorted by score desc.
    optional_pool = sorted(
        [p for p in all_pois if p["name"] not in required_names],
        key=lambda p: p.get("_score", 0),
        reverse=True,
    )

    best_route: dict | None = None
    best_deviation = float("inf")
    best_poi_count = -1
    tried_sets: set[frozenset] = set()

    for _ in range(_MAX_REFINEMENT_ITERATIONS):
        key = frozenset(p["name"] for p in current_pois)
        if key in tried_sets:
            break  # cycle: exact same POI set tried before
        tried_sets.add(key)

        route = get_route([start] + current_pois)
        route["input_pois"] = current_pois

        actual_km = route["distance_km"]
        deviation = abs(actual_km - distance_km) / distance_km
        poi_count = len(current_pois)

        # Prefer routes within tolerance over out-of-tolerance ones.
        # Among same-class routes, more POIs wins; deviation is the tiebreak.
        new_within = deviation <= _TOLERANCE
        best_within = best_deviation <= _TOLERANCE
        if (
            best_route is None
            or (new_within and not best_within)
            or (new_within == best_within and (
                poi_count > best_poi_count
                or (poi_count == best_poi_count and deviation < best_deviation)
            ))
        ):
            best_deviation = deviation
            best_poi_count = poi_count
            best_route = route

        current_names = {p["name"] for p in current_pois}

        if actual_km <= distance_km * (1 + _TOLERANCE):
            # Within tolerance or too short — try to add one more POI.
            to_add = next((p for p in optional_pool if p["name"] not in current_names), None)
            if to_add is None:
                return best_route  # can't add more; POI count is already maximised
            current_pois = current_pois + [to_add]
        else:
            # Too long — remove the lowest-scored non-required POI.
            removable = [p for p in current_pois if p["name"] not in required_names]
            if not removable:
                break  # only required POIs remain; cannot shorten
            worst = min(removable, key=lambda p: p.get("_score", 0))
            current_pois = [p for p in current_pois if p["name"] != worst["name"]]
            if not current_pois:
                break

    # Loop ended — return the route with the most POIs closest to the target distance.
    return best_route


def describe_route(waypoints: list[dict], start_location: str, route_type: str) -> dict:
    """Return a structured description dict with keys:
      - route_name: short punchy title
      - overview: 3-4 sentence enthusiastic narrative
      - waypoint_notes: list of {name, note} dicts
    Falls back to safe defaults if the LLM response cannot be parsed.
    """
    waypoint_list = "\n".join(f"- {w['name']} ({w['type']})" for w in waypoints)

    prompt = f"""You are an enthusiastic running coach and city guide writing for a runner's phone app.

Location: {start_location}
Route type: {route_type}
Waypoints (in order):
{waypoint_list}

Return JSON with exactly these keys:
- "route_name": a short punchy name for this route (4-6 words, no quotes inside the value)
- "overview": 3-4 sentences. Be upbeat and vivid — describe the vibe, what the runner will see, \
what makes this route special. Write for someone who is excited to lace up.
- "waypoint_notes": a list of objects, one per waypoint listed above, each with:
    - "name": the waypoint name exactly as given
    - "note": one punchy sentence — a fun fact, historical tidbit, or sensory detail \
that will delight a runner passing by"""

    response = _get_client().chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.7,
    )
    try:
        data = json.loads(response.choices[0].message.content)
        return {
            "route_name": data.get("route_name", "Your Running Route"),
            "overview": data.get("overview", ""),
            "waypoint_notes": data.get("waypoint_notes", []),
        }
    except Exception:
        return {"route_name": "Your Running Route", "overview": "", "waypoint_notes": []}
