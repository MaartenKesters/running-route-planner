import math
import os
import random

import folium
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

from agent import plan_route, describe_route, estimate_loop_km, translate_poi_names
from tools.export import to_gpx
from tools.geocoding import geocode
from tools.poi import get_pois


def _load_secrets():
    for key in ("OPENAI_API_KEY", "ORS_API_KEY"):
        if key not in os.environ:
            try:
                os.environ[key] = st.secrets[key]
            except (KeyError, FileNotFoundError):
                pass


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _get_poi_image_url(poi: dict) -> str | None:
    """Return a thumbnail image URL for a POI if OSM tags contain one, else None."""
    tags = poi.get("tags", {})
    if url := tags.get("image"):
        return url
    if fname := tags.get("wikimedia_commons"):
        fname = fname.removeprefix("File:")
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{fname}?width=120"
    return None


def _build_poi_map(
    pois: list[dict],
    selected_names: set[str],
    start_lat: float,
    start_lon: float,
) -> folium.Map:
    """Map showing the start point and all available POIs (green = selected, grey = not)."""
    m = folium.Map(location=[start_lat, start_lon], zoom_start=14)

    folium.Marker(
        [start_lat, start_lon],
        popup=folium.Popup("Start", max_width=200),
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(m)

    for poi in pois:
        selected = poi["name"] in selected_names
        bg = "#2E7D32" if selected else "#757575"
        border = "3px solid #81C784" if selected else "2px solid white"
        symbol = "✓" if selected else "·"
        folium.Marker(
            [poi["lat"], poi["lon"]],
            popup=folium.Popup(f"<b>{poi['name']}</b><br><i>{poi['type']}</i><br><small>Click to toggle</small>", max_width=200),
            tooltip=poi["name"],
            icon=folium.DivIcon(
                html=(
                    f'<div style="background:{bg};color:white;border:{border};'
                    f'border-radius:50%;width:22px;height:22px;display:flex;'
                    f'align-items:center;justify-content:center;font-size:13px;'
                    f'box-shadow:0 1px 3px rgba(0,0,0,.4);cursor:pointer;">{symbol}</div>'
                ),
                icon_size=(22, 22),
                icon_anchor=(11, 11),
            ),
        ).add_to(m)

    return m


def _build_map(
    route_coords: list,
    waypoints: list[dict],
    start_lat: float,
    start_lon: float,
) -> folium.Map:
    m = folium.Map(location=[start_lat, start_lon], zoom_start=14)
    # Route polyline — ORS returns [lon, lat], folium wants [lat, lon]
    path = [[lat, lon] for lon, lat in route_coords]
    folium.PolyLine(path, color="#2E7D32", weight=4, opacity=0.8).add_to(m)

    folium.Marker(
        [start_lat, start_lon],
        popup="Start / Finish",
        icon=folium.Icon(color="red", icon="flag"),
    ).add_to(m)

    for i, wp in enumerate(waypoints, start=1):
        folium.Marker(
            [wp["lat"], wp["lon"]],
            popup=f"{i}. {wp['name']}",
            icon=folium.DivIcon(
                html=f'<div style="background:#2E7D32;color:white;border-radius:50%;width:24px;height:24px;'
                     f'display:flex;align-items:center;justify-content:center;font-weight:bold;font-size:12px;">'
                     f'{i}</div>',
                icon_size=(24, 24),
                icon_anchor=(12, 12),
            ),
        ).add_to(m)

    return m


_SPINNER_MSGS = [
    "Plotting your adventure…",
    "Finding the best path through the city…",
    "Lacing up the route for you…",
    "Scouting the streets…",
    "Mapping out your next great run…",
]

_TYPE_EMOJI = {
    "tourism": "🗺️",
    "historic": "🏛️",
    "culture": "🎭",
    "leisure": "🌳",
    "nature": "🌿",
    "poi": "📍",
}


def _distance_fun_fact(km: float) -> str:
    if km < 6:
        return f"That's {km / 0.4:.0f}× around a standard 400 m track. Not bad!"
    if km < 12:
        return f"You'll cover about {km / 1.609:.1f} miles — solid work!"
    if km < 21:
        return "Getting into half-marathon territory. Your legs will know it tomorrow."
    if km < 42.2:
        return f"That's {km / 42.195 * 100:.0f}% of a full marathon. Respect."
    return "You're basically running a marathon. Absolute legend."


def _distance_indicator(estimated_km: float, target_km: float) -> str:
    """Return a colored HTML badge showing estimated vs target distance."""
    if estimated_km == 0:
        return ""
    pct = abs(estimated_km - target_km) / target_km * 100
    if pct <= 10:
        color, label = "#2E7D32", "✓ Good fit"
    elif pct <= 25:
        color, label = "#F57C00", "~ Approximate"
    else:
        color, label = "#C62828", "✗ Too far off"
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.85em;">{label}</span>'
        f" &nbsp; Est. {estimated_km:.1f} km (target {target_km} km)"
    )


def main():
    _load_secrets()

    st.set_page_config(page_title="Running Route Planner", page_icon="🏃", layout="centered")
    st.markdown(
        """
        <style>
        .footer {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            background: var(--background-color);
            border-top: 1px solid rgba(128,128,128,0.2);
            text-align: center;
            padding: 6px 0;
            font-size: 0.8em;
            color: gray;
            z-index: 999;
        }
        </style>
        <div class="footer">Made by Maarten Kesters</div>
        """,
        unsafe_allow_html=True,
    )
    st.title("🏃 Running Route Planner")
    st.caption("Generate your personalized running route through the interesting parts of any city.")

    # ── Step 1: search form ────────────────────────────────────────────────
    with st.form("search_form"):
        location = st.text_input("Start location", placeholder="e.g. Myeongdong, Seoul",
                                 value=st.session_state.get("location", ""))
        distance_km = st.slider("Target distance (km)", min_value=5, max_value=30,
                                value=st.session_state.get("distance_km", 10), step=1)
        route_type = st.radio(
            "Route type",
            options=["Cultural", "Nature", "Mixed"],
            index=["Cultural", "Nature", "Mixed"].index(
                st.session_state.get("route_type", "Cultural")
            ),
            horizontal=True,
        )
        find_pois = st.form_submit_button("Find Places")

    if find_pois:
        if not location.strip():
            st.error("Please enter a start location.")
            return

        missing_keys = [k for k in ("OPENAI_API_KEY", "ORS_API_KEY") if not os.environ.get(k)]
        if missing_keys:
            st.error(f"Missing API keys: {', '.join(missing_keys)}. Add them to .streamlit/secrets.toml.")
            return

        with st.spinner("Searching for places…"):
            try:
                start_lat, start_lon = geocode(location)
            except ValueError:
                st.error(f"Could not find '{location}'. Try a more specific address.")
                return
            except Exception as e:
                st.error(f"Geocoding error: {e}")
                return

            radius_m = int((distance_km / 2) * 1000)
            raw_pois = get_pois(start_lat, start_lon, radius_m, route_type)
            raw_pois = translate_poi_names(raw_pois)

            # Keep POIs within the search radius (a POI at radius = straight out-and-back = target distance)
            pois = [
                p for p in raw_pois
                if _haversine_km(start_lat, start_lon, p["lat"], p["lon"]) <= radius_m / 1000
            ]

            if not pois:
                st.warning(
                    "No nearby points of interest found for this distance and route type. "
                    "Try 'Mixed', a longer distance, or a different location."
                )
                return

        st.session_state["pois"] = pois
        st.session_state["start_lat"] = start_lat
        st.session_state["start_lon"] = start_lon
        st.session_state["location"] = location
        st.session_state["distance_km"] = distance_km
        st.session_state["route_type"] = route_type
        st.session_state.pop("route", None)
        st.session_state.pop("last_map_click", None)
        st.session_state.pop("pending_poi_click", None)

    # ── Step 2: POI selection ──────────────────────────────────────────────
    if "pois" not in st.session_state:
        return

    pois = st.session_state["pois"]
    start_lat = st.session_state["start_lat"]
    start_lon = st.session_state["start_lon"]
    target_km = st.session_state["distance_km"]
    location = st.session_state["location"]
    route_type = st.session_state["route_type"]

    # Apply any pending map click (toggle the matching POI checkbox)
    if "pending_poi_click" in st.session_state:
        click_lat, click_lon = st.session_state.pop("pending_poi_click")
        for poi in pois:
            if abs(poi["lat"] - click_lat) < 1e-5 and abs(poi["lon"] - click_lon) < 1e-5:
                key = f"poi__{poi['name']}__{poi['lat']}__{poi['lon']}"
                st.session_state[key] = not st.session_state.get(key, False)
                break

    st.markdown("---")
    st.markdown(f"### Select places to visit &nbsp; <small>({len(pois)} found within range)</small>",
                unsafe_allow_html=True)
    st.caption(
        "Check the places you want to run past, or click the dots on the map to toggle them. "
        "Green markers are selected, grey are not. Aim for the green distance indicator."
    )

    # ── Two-column layout: checkboxes left, map right ──────────────────────
    left_col, right_col = st.columns([2, 3])

    selected_pois = []
    with left_col:
        for poi in pois:
            dist_from_start = _haversine_km(start_lat, start_lon, poi["lat"], poi["lon"])
            key = f"poi__{poi['name']}__{poi['lat']}__{poi['lon']}"
            image_url = _get_poi_image_url(poi)

            if image_url:
                img_col, cb_col = st.columns([1, 4])
                with img_col:
                    try:
                        st.image(image_url, width=60)
                    except Exception:
                        pass
                with cb_col:
                    label = f"**{poi['name']}**  \n{poi['type']} · {dist_from_start:.1f} km"
                    if st.checkbox(label, key=key, value=False):
                        selected_pois.append(poi)
            else:
                label = f"**{poi['name']}** — {poi['type']} · {dist_from_start:.1f} km"
                if st.checkbox(label, key=key, value=False):
                    selected_pois.append(poi)

        if selected_pois:
            est_km = estimate_loop_km(start_lat, start_lon, selected_pois)
            st.markdown(_distance_indicator(est_km, target_km), unsafe_allow_html=True)

        st.markdown("")
        st.markdown(
            "<style>div[data-testid='stButton'] button { white-space: nowrap; }</style>",
            unsafe_allow_html=True,
        )
        col_gen, col_reset = st.columns([3, 1])
        with col_gen:
            generate = st.button("Generate Route", type="primary", use_container_width=True)
        with col_reset:
            if st.button("Reset", use_container_width=True):
                for k in ("pois", "start_lat", "start_lon", "location", "distance_km", "route_type", "route",
                          "last_map_click", "pending_poi_click"):
                    st.session_state.pop(k, None)
                st.rerun()

    with right_col:
        poi_map = _build_poi_map(
            pois,
            selected_names={p["name"] for p in selected_pois},
            start_lat=start_lat,
            start_lon=start_lon,
        )
        map_data = st_folium(poi_map, height=480, use_container_width=True, key="poi_map")

        # Handle marker clicks — toggle the corresponding POI
        if map_data and map_data.get("last_object_clicked"):
            clicked = map_data["last_object_clicked"]
            click_lat = clicked.get("lat")
            click_lon = clicked.get("lng")
            if click_lat is not None and click_lon is not None:
                click_key = (round(click_lat, 6), round(click_lon, 6))
                # Ignore start marker and already-processed clicks
                is_start = abs(click_lat - start_lat) < 1e-5 and abs(click_lon - start_lon) < 1e-5
                if not is_start and click_key != st.session_state.get("last_map_click"):
                    st.session_state["last_map_click"] = click_key
                    st.session_state["pending_poi_click"] = (click_lat, click_lon)
                    st.rerun()

    if not generate:
        return

    # ── Step 3: route generation ───────────────────────────────────────────
    missing_keys = [k for k in ("OPENAI_API_KEY", "ORS_API_KEY") if not os.environ.get(k)]
    if missing_keys:
        st.error(f"Missing API keys: {', '.join(missing_keys)}. Add them to .streamlit/secrets.toml.")
        return

    with st.spinner(random.choice(_SPINNER_MSGS)):
        try:
            route = plan_route(start_lat, start_lon, selected_pois, pois, target_km)
        except Exception as e:
            st.error(f"Routing failed: {e}")
            return
        if not route:
            st.warning("Could not generate a route. Try increasing the distance or switching route type.")
            return

        waypoints = route.get("optimised_pois") or route.get("input_pois") or selected_pois

        try:
            description = describe_route(waypoints, location, route_type)
        except Exception as e:
            description = f"(Could not generate description: {e})"

    # ── Display results ────────────────────────────────────────────────────
    actual_km = route["distance_km"]
    deviation_pct = abs(actual_km - target_km) / target_km * 100

    if deviation_pct > 10:
        st.warning(
            f"Route is **{actual_km} km** — {deviation_pct:.0f}% from your target of {target_km} km. "
            "Consider adjusting your POI selection to get closer to the target distance."
        )
    else:
        st.success("Route generated!")

    st.markdown(f"## {description['route_name']}")

    col1, col2 = st.columns(2)
    col1.metric("Distance", f"{actual_km} km")
    col2.metric("Waypoints", len(waypoints))
    st.caption(_distance_fun_fact(actual_km))

    fmap = _build_map(route["coordinates"], waypoints, start_lat, start_lon)
    components.html(fmap._repr_html_(), height=420)

    st.markdown("### Route overview")
    st.write(description["overview"])

    st.markdown("### Waypoints")
    notes = {wn["name"]: wn["note"] for wn in description.get("waypoint_notes", [])}
    for i, wp in enumerate(waypoints, start=1):
        emoji = _TYPE_EMOJI.get(wp["type"], "📍")
        note = notes.get(wp["name"], "")
        with st.expander(f"{emoji} **{i}. {wp['name']}** — {wp['type']}"):
            if note:
                st.write(note)

    gpx_bytes = to_gpx(route["coordinates"], waypoints, f"{route_type} run in {location}")
    st.download_button(
        label="Download GPX",
        data=gpx_bytes,
        file_name="running_route.gpx",
        mime="application/gpx+xml",
    )


if __name__ == "__main__":
    main()

