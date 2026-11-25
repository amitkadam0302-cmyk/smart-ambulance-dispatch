import json
import math
from datetime import UTC, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select

from dispatch.auth import check_role
from dispatch.config import now
from dispatch.db import Session
from dispatch.models import Incident
from ml.train import IST, feature_ids


@lru_cache(maxsize=1)
def artifact():
    return json.loads(Path(__file__).with_name("model.json").read_text(encoding="utf-8"))


def dashboard_forecast(actor, clock=now):
    check_role(actor, "dispatcher")
    model = artifact()
    local = clock().astimezone(IST)
    this_hour = local.replace(minute=0, second=0, microsecond=0)
    next_hour = this_hour + timedelta(hours=1)
    with Session() as db:
        count = dict(db.execute(select(Incident.zone, func.count(Incident.id)).where(
            Incident.workspace_id == actor.workspace_id, Incident.created_at >= this_hour - timedelta(hours=1),
            Incident.created_at < this_hour,
        ).group_by(Incident.zone)).all())
    weights = model["weights"]
    return {
        "version": model["version"], "label": model["provenance"], "mae": model["mae"],
        "baseline_mae": model["baseline_mae"], "next_hour": next_hour.astimezone(UTC).isoformat(),
        "observed_window": [(this_hour - timedelta(hours=1)).astimezone(UTC).isoformat(),
                            this_hour.astimezone(UTC).isoformat()],
        "zones": [
            {"zone": name, "expected": round(math.exp(sum(
                weights[i] for i in feature_ids(zone, next_hour.hour, next_hour.weekday())
            )), 4), "observed": count.get(name, 0)}
            for zone, name in enumerate(model["zones"])
        ],
    }
