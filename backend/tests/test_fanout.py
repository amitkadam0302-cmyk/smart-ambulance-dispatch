import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import redis
from websockets.sync.client import connect

from dispatch.config import settings
from fixtures.alandi import PICKUP

ORIGIN = "http://localhost:3000"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def api_process(number):
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(number), "--no-access-log"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with httpx.Client(base_url=f"http://127.0.0.1:{number}", headers={"Origin": ORIGIN},
                      timeout=4, trust_env=False) as client:
        try:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert process.poll() is None, "API process stopped"
                time.sleep(.08)
            else:
                raise AssertionError("API did not become healthy")
            yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def token(client, code, role):
    response = client.post("/api/auth/demo", json={"code": code, "role": role})
    assert response.status_code == 200
    return response.json()["access_token"]


def websocket(port_number):
    return connect(
        f"ws://127.0.0.1:{port_number}/api/socket.io?EIO=4&transport=websocket",
        origin=ORIGIN, open_timeout=5, close_timeout=1, proxy=None,
    )


def listen(ws, token_value):
    assert ws.recv(timeout=4).startswith("0")
    ws.send("40" + json.dumps({"token": token_value}))
    assert ws.recv(timeout=4).startswith("40")


def submit(client, patient):
    response = client.post("/api/incidents", headers={
        "Authorization": "Bearer " + patient, "Idempotency-Key": "fan-out-first-sos",
    }, json={"lat": PICKUP[0], "lng": PICKUP[1], "pickup_name": "Fixture college", "source": "alandi"})
    assert response.status_code == 201, response.text
    return response.json()


def test_postgres_replay_and_cross_process_redis_websocket_fanout():
    a, b = port(), port()
    with api_process(a) as api_a:
        group = api_a.post("/api/demo").json()["code"]
        dispatcher = token(api_a, group, "dispatcher")
        patient = token(api_a, group, "patient")
        h = {"Authorization": "Bearer " + dispatcher}
        with api_process(b) as api_b:
            with websocket(b) as ws:
                listen(ws, dispatcher)
                ws.send('421["subscribe",{"room":"self"}]')
                assert json.loads(ws.recv(timeout=4)[3:]) == [{"ok": True}]
                ws.send('422["subscribe",{"room":"another-membership"}]')
                assert json.loads(ws.recv(timeout=4)[3:])[0]["code"] == "forbidden"
                with redis.Redis.from_url(settings.redis_url) as connection:
                    for _ in range(40):
                        if connection.pubsub_numsub(settings.namespace + ":sio")[0][1] >= 1:
                            break
                        time.sleep(.05)
                    else:
                        raise AssertionError("Redis subscriber did not start")
                # The client stays connected to B. All writes and the publisher are on A.
                case = submit(api_a, patient)
                messages = set()
                for _ in range(10):
                    packet = ws.recv(timeout=5)
                    if packet.startswith("42"):
                        evt, data = json.loads(packet[2:])
                        if evt == "domain_event":
                            messages.add(data["id"])
                    if case["event_id"] in messages:
                        break
                assert case["event_id"] in messages
            before = api_b.get("/api/dashboard", headers=h).json()
            assert before["incidents"][0]["mission"]["unit"] == "AMB3"
        second = api_a.post("/api/incidents", headers={**h, "Idempotency-Key": "fan-out-second-sos"},
                            json={"lat": PICKUP[0], "lng": PICKUP[1], "pickup_name": "Second training case",
                                  "source": "phone"})
        assert second.status_code == 201
        with api_process(b) as restarted:
            after = restarted.get("/api/dashboard", headers=h).json()
            assert after["sequence"] > before["sequence"]
            assert {i["code"] for i in after["incidents"]} == {"ALD-0001", "ALD-0002"}
            replay = restarted.get(f"/api/events?after={before['sequence']}", headers=h).json()
            assert any(e["id"] == second.json()["event_id"] for e in replay["events"])
            sequences = [e["seq"] for e in replay["events"]]
            assert sequences == sorted(set(sequences))
            assert replay["next"] == after["sequence"]
            with websocket(b) as ws:
                listen(ws, dispatcher)
            with websocket(b) as unauthorized:
                assert unauthorized.recv(timeout=4).startswith("0")
                unauthorized.send('40{"token":"invalid"}')
                assert unauthorized.recv(timeout=4).startswith("44")
