import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256

import httpx
import redis

from dispatch.config import settings
from fixtures.alandi import ALANDI_MAIN, PICKUP, PICKUP_TIMES, POSITIONS

Point = tuple[float, float]
TOMTOM_URL = "https://api.tomtom.com/routing/1/calculateRoute"
# HTTPX's request log includes the provider key and coordinates in the URL.
logging.getLogger("httpx").setLevel(logging.WARNING)


class RouteUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Estimate:
    points: list
    directions: list
    eta_seconds: int
    distance_m: int
    delay_seconds: int | None
    source: str
    calculated_at: str


def distance(a, b):
    lat1, lat2 = map(math.radians, (a[0], b[0]))
    d_lat = lat2 - lat1
    d_lng = math.radians(b[1] - a[1])
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def geometry_length(points):
    return sum(distance(a, b) for a, b in zip(points, points[1:]))


def progress_on_route(position, points):
    """Project a GPS fix onto the nearest road segment and return distance fraction."""
    closest, travelled, best = float("inf"), 0.0, 0.0
    scale = math.cos(math.radians(position[0]))
    for a, b in zip(points, points[1:]):
        x, y = b[0] - a[0], (b[1] - a[1]) * scale
        px, py = position[0] - a[0], (position[1] - a[1]) * scale
        squared = x * x + y * y
        fraction = min(1, max(0, (px * x + py * y) / squared)) if squared else 0
        offset = (px - x * fraction) ** 2 + (py - y * fraction) ** 2
        length = distance(a, b)
        if offset < closest:
            closest, best = offset, travelled + fraction * length
        travelled += length
    return best / travelled if travelled else 0


def simulate_position(points, progress: float):
    remaining = max(0, min(1, progress)) * geometry_length(points)
    for a, b in zip(points, points[1:]):
        length = distance(a, b)
        if length and remaining <= length:
            f = remaining / length
            return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]
        remaining -= length
    return list(points[-1])


def fixture_route(origin: Point, target: Point, at: datetime, traffic=False):
    if (origin, target) == (POSITIONS["ALANDI"], PICKUP) and not traffic:
        points = list(ALANDI_MAIN)
    elif (origin, target) == (PICKUP, POSITIONS["ALANDI"]) and not traffic:
        points = list(reversed(ALANDI_MAIN))
    else:
        y, x = target[0] - origin[0], target[1] - origin[1]
        offset = .0010 if traffic else -.00045
        points = [origin, (origin[0] + .23 * y + offset, origin[1] + .27 * x),
                  (origin[0] + .60 * y + offset, origin[1] + .64 * x),
                  (origin[0] + .82 * y, origin[1] + .86 * x), target]
    length = round(geometry_length(points))
    eta = max(60, round(length / 7))
    if target == PICKUP:
        eta = PICKUP_TIMES.get(origin, eta)
    if (origin, target) == (PICKUP, POSITIONS["ALANDI"]):
        eta = 240
    if traffic:
        eta = max(20, round(eta * .56))
    return Estimate(points, ["Follow the schematic road", "Continue to the marked destination"],
                    eta, length, 0 if traffic else None,
                    "Simulated traffic" if traffic else "Alandi route fixture", at.isoformat())


class Router:
    def __init__(self, client=None, cache=None):
        self.client = client or httpx.Client(timeout=httpx.Timeout(3, connect=1.5))
        self.cache = cache or redis.Redis.from_url(
            settings.redis_url, socket_timeout=1, socket_connect_timeout=1, max_connections=6,
        )

    def routes(self, mode: str, origin: Point, target: Point, at: datetime,
               alternate=False) -> list[Estimate]:
        if mode == "demo":
            return [fixture_route(origin, target, at, alternate)]
        if not settings.tomtom_key:
            raise RouteUnavailable("TomTom routing is unavailable because no routing key is set.")
        options = {
            "traffic": "true", "travelMode": "car", "routeType": "fastest",
            "maxAlternatives": 2 if alternate else 0, "instructionsType": "text",
            "language": "en-GB", "routeRepresentation": "polyline",
        }
        signature = json.dumps([origin, target, int(at.timestamp()) // 30, options], sort_keys=True)
        key = f"{settings.namespace}:route:{sha256(signature.encode()).hexdigest()}"
        try:
            cached = self.cache.get(key)
            if cached:
                return [Estimate(**value) for value in json.loads(cached)]
        except redis.RedisError:
            pass
        path = f"{origin[0]:.6f},{origin[1]:.6f}:{target[0]:.6f},{target[1]:.6f}"
        try:
            response = self.client.get(f"{TOMTOM_URL}/{path}/json", params={
                **options, "key": settings.tomtom_key,
                "departAt": "now",
            })
        except httpx.TimeoutException as exc:
            raise RouteUnavailable("TomTom timed out. Retry routing or assign manually.") from exc
        except httpx.HTTPError as exc:
            raise RouteUnavailable("TomTom is unreachable.") from exc
        if response.status_code in (429, 403, 401):
            raise RouteUnavailable("TomTom rejected access or exceeded its quota.")
        if not response.is_success:
            raise RouteUnavailable(f"TomTom returned no usable route (HTTP {response.status_code}).")
        try:
            items = response.json().get("routes", [])
        except (ValueError, AttributeError) as exc:
            raise RouteUnavailable("TomTom returned an unreadable response.") from exc
        estimates = []
        if not isinstance(items, list):
            items = []
        for item in items:
            try:
                summary = item["summary"]
                points = [[float(p["latitude"]), float(p["longitude"])]
                          for leg in item["legs"] for p in leg["points"]]
                if len(points) < 2 or any(not (-90 <= y <= 90 and -180 <= x <= 180) for y, x in points):
                    continue
                directions = [str(i["message"]) for i in item.get("guidance", {}).get("instructions", [])
                              if i.get("message")]
                delay = summary.get("trafficDelayInSeconds")
                estimates.append(Estimate(points, directions, max(1, int(summary["travelTimeInSeconds"])),
                                          max(0, int(summary["lengthInMeters"])),
                                          max(0, int(delay)) if delay is not None else None,
                                          "TomTom provider estimate", at.isoformat()))
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                continue
        if not estimates:
            raise RouteUnavailable("TomTom returned no road geometry for these coordinates.")
        estimates.sort(key=lambda item: item.eta_seconds)
        try:
            self.cache.setex(key, 45, json.dumps([asdict(item) for item in estimates]))
        except redis.RedisError:
            pass
        return estimates
