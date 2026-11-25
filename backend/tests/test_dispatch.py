from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from dispatch import demo, operations, views
from dispatch.allocator import allocate
from dispatch.auth import resolve
from dispatch.config import now, settings
from dispatch.db import Session
from dispatch.errors import Problem
from dispatch.models import Capacity, Incident, Mission, Unit, uid
from dispatch.routing import RouteUnavailable, fixture_route
from dispatch.schemas import (
    SOS,
    CapacityReport,
    DemoEntry,
    Destination,
    DestinationSelection,
    IntakeSubmission,
    Transition,
    UnitUpdate,
)
from fixtures.alandi import PICKUP, POSITIONS


class Clock:
    def __init__(self):
        self.at = now()

    def __call__(self):
        return self.at

    def advance(self, seconds):
        self.at += timedelta(seconds=seconds)


@pytest.fixture
def group():
    code = demo.create_group()["code"]
    with Session() as db:
        return {
            role: resolve(db, demo.enter(DemoEntry(code=code, role=role))[0]["access_token"])
            for role in ("patient", "dispatcher", "driver", "hospital", "nurse")
        }


def request(actor, clock, key="case-sos-1", priority=None, pickup_name="Example pickup"):
    address = SOS(lat=PICKUP[0], lng=PICKUP[1], pickup_name=pickup_name,
                  source="alandi" if actor.role == "patient" else "phone",
                  priority=priority, reported_urgency="critical")
    return operations.submit_sos(actor, address, key, clock)[0]


def assignments(actor, clock):
    return views.snapshot(actor, clock)


def down(actor, keep, clock):
    for unit in assignments(actor, clock)["units"]:
        if unit["name"] not in keep:
            operations.update_unit(actor, unit["id"], UnitUpdate(status="offline", checked_in=True,
                                    reason="Training outage"), clock)


def test_default_fastest_and_idempotent_sos(group):
    patient, dispatcher = group["patient"], group["dispatcher"]
    clock = Clock()
    first = request(patient, clock)
    assert request(patient, clock) == first
    with pytest.raises(Problem, match="already an active SOS"):
        request(patient, clock, "different-key")
    with pytest.raises(Problem, match="bound to different input"):
        request(patient, clock, "case-sos-1", pickup_name="Different body")
    result, _ = allocate(patient.workspace_id, clock=clock)
    case = assignments(dispatcher, clock)["incidents"][0]
    assert case["priority"] is None and case["reported_urgency"] == "critical"
    assert result["id"] == first["id"]
    assert case["mission"]["unit"] == "AMB3"
    assert [row["eta_seconds"] for row in case["candidates"]] == [2640, 2940, 300]
    assert case["mission"]["dispatching_hospital_id"] == case["mission"]["receiving_hospital_id"]
    assert "shortest road ETA" in case["dispatch_note"]


def test_faster_changed_eta_and_deterministic_tie(group):
    dispatcher = group["dispatcher"]
    clock = Clock()

    class Faster:
        def routes(self, _mode, origin, destination, at, alternate=False):
            route = fixture_route(origin, destination, at, alternate)
            if destination == PICKUP and origin in [POSITIONS["RUBY"], POSITIONS["SASSOON"]]:
                route = replace(route, eta_seconds=120)
            return [route]

    request(dispatcher, clock)
    allocate(dispatcher.workspace_id, clock=clock, service=Faster())
    case = assignments(dispatcher, clock)["incidents"][0]
    assert case["mission"]["unit"] == "AMB1"
    assert [row["eta_seconds"] for row in case["candidates"]] == [120, 120, 300]


def test_busy_ambulance_goes_to_next_largest_fleet(group):
    dispatcher = group["dispatcher"]
    clock = Clock()
    first, second = request(dispatcher, clock, "case-first"), request(dispatcher, clock, "case-second")
    allocate(dispatcher.workspace_id, clock=clock)
    allocate(dispatcher.workspace_id, clock=clock)
    cases = {i["id"]: i for i in assignments(dispatcher, clock)["incidents"]}
    assert cases[first["id"]]["mission"]["unit"] == "AMB3"
    assert cases[second["id"]]["mission"]["unit"] == "AMB1"
    assert next(c["reason"] for c in cases[second["id"]]["candidates"] if c["name"] == "AMB3") == (
        "Already assigned to another mission"
    )


def test_concurrent_allocators_respect_priority_and_age(group):
    dispatcher = group["dispatcher"]
    clock = Clock()
    down(dispatcher, {"AMB3"}, clock)
    normal = request(dispatcher, clock, "normal-01")
    clock.advance(2)
    older = request(dispatcher, clock, "critical-older", priority="critical")
    clock.advance(2)
    newer = request(dispatcher, clock, "critical-newer", priority="critical")
    barrier = Barrier(2)

    class ConcurrentRoutes:
        def routes(self, _mode, origin, target, at, alternate=False):
            if origin == POSITIONS["ALANDI"] and target == PICKUP:
                barrier.wait(timeout=6)
            return [fixture_route(origin, target, at, alternate)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(allocate, dispatcher.workspace_id, clock=clock,
                            service=ConcurrentRoutes()) for _ in range(2)]
        outcomes = [future.result()[0]["status"] for future in jobs]
    assert sorted(outcomes) == ["assigned", "queued"]
    snap = assignments(dispatcher, clock)
    assert snap["queue"] == [newer["id"], normal["id"]]
    assert next(i for i in snap["incidents"] if i["mission"])["id"] == older["id"]
    assert snap["metrics"]["active_units"] == 1

    with Session() as db:
        elected = db.scalar(select(Mission).where(Mission.incident_id == older["id"]))
        for unit_id, incident_id in [(elected.unit_id, newer["id"]), (uid(), older["id"])]:
            db.add(Mission(workspace_id=dispatcher.workspace_id, incident_id=incident_id,
                           unit_id=unit_id if incident_id == newer["id"] else
                           db.scalar(select(Unit.id).where(Unit.workspace_id == dispatcher.workspace_id,
                                                           Unit.name == "AMB1")),
                           sending_id=elected.sending_id, status="assigned"))
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_sending_full_er_and_decline_keep_active(group):
    dispatcher, hospital = group["dispatcher"], group["hospital"]
    clock = Clock()
    h = assignments(hospital, clock)["hospitals"][0]
    full = CapacityReport(total=7, occupied=7, accepting=True, source="Test feed", reported_at=clock())
    response, _ = operations.report_capacity(hospital, h["id"], full, "full-bed-command", clock=clock)
    assert operations.report_capacity(hospital, h["id"], full, "full-bed-command", clock=clock)[0] == response
    request(group["patient"], clock)
    allocate(dispatcher.workspace_id, clock=clock)
    mission = assignments(dispatcher, clock)["incidents"][0]["mission"]
    assert mission["unit"] == "AMB3"
    assert mission["receiving_hospital_id"] != mission["dispatching_hospital_id"]
    assert mission["destination_status"] == "accepted"
    with Session() as db:
        origin = db.get(Capacity, mission["dispatching_hospital_id"])
        assert origin.occupied == origin.total and origin.reserved == 0

    code = demo.create_group()["code"]
    clock = Clock()
    with Session() as db:
        def actor(role, **kw):
            return resolve(db, demo.enter(DemoEntry(code=code, role=role, **kw))[0]["access_token"])
        dispatch2, alandi = actor("dispatcher"), actor("hospital")
        ruby, sassoon = actor("hospital", hospital="RUBY"), actor("hospital", hospital="SASSOON")
    for receiver in (ruby, sassoon):
        operations.report_capacity(receiver, receiver.hospital_id,
                                   CapacityReport(total=1, occupied=1, accepting=True, source="Full fixture"),
                                   "other-full", clock=clock)
    request(dispatch2, clock, "off-mission")
    allocate(dispatch2.workspace_id, clock=clock)
    case = assignments(dispatch2, clock)["incidents"][0]
    assert case["mission"]["receiving_hospital_id"] == alandi.hospital_id
    operations.decide_destination(alandi, case["mission"]["id"], Destination(decision="decline",
                                  reason="Example hospital refusal"), clock)
    after = assignments(dispatch2, clock)["incidents"][0]
    assert after["status"] == "assigned" and after["mission"]["destination_status"] == "coordination"
    assert assignments(alandi, clock)["hospitals"][0]["capacity"]["reserved"] == 0
    operations.choose_destination(dispatch2, case["mission"]["id"],
                                  DestinationSelection(reason="Retry receiving hospitals"), clock)
    assert assignments(dispatch2, clock)["incidents"][0]["mission"]["receiving_hospital_id"] is None


def test_restart_clock_traffic_intake_and_handover_each_record_once(group):
    clock = Clock()
    dispatcher, patient, nurse, hospital = (group[role] for role in ("dispatcher", "patient", "nurse", "hospital"))
    request(patient, clock)
    allocate(dispatcher.workspace_id, clock=clock)
    first = assignments(dispatcher, clock)["incidents"][0]
    mission = first["mission"]
    changed, _ = operations.reroute(dispatcher, mission["id"], True, clock)
    assert changed["changed"]
    assert not operations.reroute(dispatcher, mission["id"], True, clock)[0]["changed"]
    traffic = assignments(dispatcher, clock)["incidents"][0]["mission"]
    assert traffic["route"]["points"] != mission["route"]["points"]
    assert traffic["route"]["eta_seconds"] < mission["route"]["eta_seconds"]
    assert traffic["route"]["source"] == "Simulated traffic"
    assert traffic["route_version"] == mission["route_version"] + 1
    clock.advance(180)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: operations.tick(nurse, clock), range(2)))
    picked = assignments(nurse, clock)["incidents"][0]
    assert picked["status"] == "at_patient"
    assert sum(e["kind"] == "mission.at_patient" for e in picked["timeline"]) == 1
    body = IntakeSubmission(alias="Fictional alias test", issue="accident", age=None)
    saved, _ = operations.save_intake(nurse, mission["id"], body, "save-intake", clock)
    assert operations.save_intake(nurse, mission["id"], body, "save-intake", clock)[0] == saved
    with pytest.raises(Problem, match="once"):
        operations.save_intake(nurse, mission["id"], body, "new-intake", clock)
    assert saved["status"] == "transporting"
    assert assignments(dispatcher, clock)["incidents"][0]["mission"]["intake"] is None
    assert "Fictional alias test" not in str(views.event_replay(dispatcher, 0))
    clock.advance(180)
    operations.tick(hospital, clock)
    transition = Transition(action="handover")
    done, _ = operations.transition(hospital, mission["id"], transition, "handover-1", clock)
    assert operations.transition(hospital, mission["id"], transition, "handover-1", clock)[0] == done
    cap = assignments(hospital, clock)["hospitals"][0]["capacity"]
    assert (cap["occupied"], cap["reserved"]) == (8, 0)
    assert assignments(dispatcher, clock)["metrics"]["available_units"] == 3
    with Session() as db:
        matches = db.scalar(select(func.count()).select_from(Incident).where(Incident.patient_id == patient.id))
    assert matches == 1


def test_reassignment_failure_preserves_current_mission(group):
    dispatcher = group["dispatcher"]
    clock = Clock()
    case = request(dispatcher, clock)
    allocate(dispatcher.workspace_id, clock=clock)
    before = assignments(dispatcher, clock)
    target = next(unit["id"] for unit in before["units"] if unit["name"] == "AMB1")

    class Down:
        def routes(self, *_args, **_kwargs):
            raise RouteUnavailable("Test coverage failure")

    with pytest.raises(Problem, match="estimated travel time"):
        allocate(dispatcher.workspace_id, requested_id=case["id"], preferred_id=target, reason="Training trial",
                 service=Down(), clock=clock, actor=dispatcher)
    assert assignments(dispatcher, clock)["incidents"][0]["mission"]["unit"] == "AMB3"
    allocate(dispatcher.workspace_id, requested_id=case["id"], preferred_id=target, reason="Training trial",
             clock=clock, actor=dispatcher)
    after = assignments(dispatcher, clock)["incidents"][0]
    assert after["mission"]["unit"] == "AMB1" and after["id"] == case["id"]
    assert sum(e["kind"] == "mission.reassigned" for e in after["timeline"]) == 1


def test_stale_destination_reports_are_excluded(group):
    dispatcher = group["dispatcher"]
    clock = Clock()
    clock.advance(settings.capacity_ttl + 1)
    request(dispatcher, clock)
    allocate(dispatcher.workspace_id, clock=clock)
    mission = assignments(dispatcher, clock)["incidents"][0]["mission"]
    assert mission["unit"] == "AMB3" and mission["destination_status"] == "coordination"
    assert {row["reason"] for row in mission["destination_candidates"]} == {"Capacity report is stale"}


def test_concurrent_sos_retries_share_the_original_event(group):
    actor = group["patient"]
    clock = Clock()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: request(actor, clock, "same-sos-key"), range(2)))
    assert result[0] == result[1]
    snapshot = assignments(actor, clock)
    assert len(snapshot["incidents"]) == 1
    assert [e["id"] for e in snapshot["incidents"][0]["timeline"]] == [result[0]["event_id"]]
