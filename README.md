# Running Route Planner

A Streamlit app that plans scenic running routes with points of interest (POIs) along the way.

## Features

- Set a starting location by address or by clicking on the map
- Choose a target distance and route type (loop, out-and-back, or point-to-point)
- Discover the top 10 most notable POIs near your route, ranked by notability (Wikipedia/Wikidata presence, tourism tags, etc.)
- Select POIs to include as waypoints — via the sidebar list or by clicking markers on the map
- Route is automatically optimised for the best visiting order using the ORS Optimization API (Vroom)
- Export your route as a GPX file

## APIs used

| Service | Purpose |
|---|---|
| [OpenRouteService](https://openrouteservice.org/) | Routing, waypoint optimisation |
| [Overpass / OpenStreetMap](https://overpass-api.de/) | POI discovery |
| [Nominatim](https://nominatim.org/) | Address geocoding |

## Setup

1. Clone the repo and create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Create a `.env` file with your API key:
   ```
   ORS_API_KEY=your_openrouteservice_api_key
   ```
   Get a free key at [openrouteservice.org](https://openrouteservice.org/dev/#/signup).

3. Run the app:
   ```bash
   streamlit run app.py
   ```
