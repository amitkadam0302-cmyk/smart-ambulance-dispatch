
import httpx
import pytest

from dispatch.config import now, settings
from dispatch.routing import (
    Router,
    RouteUnavailable,
    fixture_route,
    progress_on_route,
    simulate_position,
)
from fixtures.alandi import PICKUP, POSITIONS


class Cache:
    def __init__(self):
        self.items = {}

    def get(self, key):
        return self.items.get(key)

    def setex(self, key, ttl, value):
        assert ttl == 45
        self.items[key] = value


def test_tomtom_parse_partial_routes_cache_alternatives(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "tomtom_key", "test-routing-key")
    origin, target = POSITIONS["ALANDI"], PICKUP
    geometry = [
        {"latitude": origin[0], "longitude": origin[1]},
        {"latitude": 18.672, "longitude": 73.895},
        {"latitude": target[0], "longitude": target[1]},
    ]

    def request(req):
        calls.append(req)
        assert req.url.host == "api.tomtom.com"
        assert req.url.params["traffic"] == "true" and req.url.params["departAt"] == "now"
        assert req.url.params["maxAlternatives"] == "2"
        assert set(req.url.params.keys()) == {
            "key", "traffic", "travelMode", "routeType", "maxAlternatives", "instructionsType",
            "language", "routeRepresentation", "departAt",
        }
        summary = {"lengthInMeters": 810, "travelTimeInSeconds": 200, "trafficDelayInSeconds": 31}
        return httpx.Response(200, json={"routes": [
            {"bad": True},
            {"summary": summary, "legs": [{"points": geometry}],
             "guidance": {"instructions": [{"message": "Continue along the road"}]}},
            {"summary": {**summary, "travelTimeInSeconds": 155}, "legs": [{"points": geometry[::2]}]},
        ]})

    router = Router(httpx.Client(transport=httpx.MockTransport(request)), Cache())
    at = now()
    routes = router.routes("connected", origin, target, at, alternate=True)
    assert [r.eta_seconds for r in routes] == [155, 200]
    assert routes[1].delay_seconds == 31 and routes[1].directions == ["Continue along the road"]
    assert routes[1].points[1] == [18.672, 73.895]
    assert router.routes("connected", origin, target, at, alternate=True) == routes
    assert len(calls) == 1


@pytest.mark.parametrize(("status", "message"), [(429, "quota"), (400, "HTTP 400"), (200, "no road geometry")])
def test_provider_failures_are_readable(monkeypatch, status, message):
    monkeypatch.setattr(settings, "tomtom_key", "example")
    router = Router(httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(status, json={"routes": []}),
    )), Cache())
    with pytest.raises(RouteUnavailable, match=message):
        router.routes("connected", POSITIONS["ALANDI"], PICKUP, now())


def test_provider_timeout_and_missing_key(monkeypatch):
    def timeout(req):
        raise httpx.ReadTimeout("test only", request=req)
    router = Router(httpx.Client(transport=httpx.MockTransport(timeout)), Cache())
    monkeypatch.setattr(settings, "tomtom_key", "")
    with pytest.raises(RouteUnavailable, match="no routing key"):
        router.routes("connected", POSITIONS["ALANDI"], PICKUP, now())
    monkeypatch.setattr(settings, "tomtom_key", "routing-fixture")
    with pytest.raises(RouteUnavailable, match="timed out"):
        router.routes("connected", POSITIONS["ALANDI"], PICKUP, now())


def test_simulation_interpolation_uses_distance():
    route = fixture_route(POSITIONS["ALANDI"], PICKUP, now())
    at_half = simulate_position(route.points, .5)
    assert at_half not in [list(route.points[0]), list(route.points[-1])]
    assert progress_on_route(at_half, route.points) == pytest.approx(.5, abs=.002)
    assert simulate_position(route.points, 2) == list(PICKUP)
