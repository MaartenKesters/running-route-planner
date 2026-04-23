import requests


def geocode(location: str) -> tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "json", "limit": 1}
    headers = {"User-Agent": "RunningRoutePlanner/1.0"}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    results = response.json()
    if not results:
        raise ValueError(f"Could not geocode: {location}")
    return float(results[0]["lat"]), float(results[0]["lon"])
