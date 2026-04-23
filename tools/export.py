import gpxpy
import gpxpy.gpx


def to_gpx(route_coords: list, waypoints: list[dict], route_name: str) -> bytes:
    gpx = gpxpy.gpx.GPX()
    gpx.name = route_name

    track = gpxpy.gpx.GPXTrack(name=route_name)
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

    for lon, lat in route_coords:
        segment.points.append(gpxpy.gpx.GPXTrackPoint(latitude=lat, longitude=lon))

    for wp in waypoints:
        gpx.waypoints.append(
            gpxpy.gpx.GPXWaypoint(latitude=wp["lat"], longitude=wp["lon"], name=wp["name"])
        )

    return gpx.to_xml().encode("utf-8")
