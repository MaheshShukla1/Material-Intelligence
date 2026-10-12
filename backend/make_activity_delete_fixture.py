"""Captures REAL backend responses for the activity-delete dialog test (frontend/activity_delete_fixture.json).
Run:  python3 -m pytest backend/make_activity_delete_fixture.py -q   (writes the file; fails if it cannot)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.test_activity_delete import scene, activity, SP, B, SLUG  # noqa: F401
from backend.test_billing import env  # noqa: F401


def test_capture(scene):
    client, d = scene
    out = {"slug": SLUG}
    out["project"] = client.get(SP).json()
    out["view"] = client.get(f"{SP}/service/Fire").json()
    out["impact_pumps"] = client.get(f"{B}/activity-impact", params={"service": "Fire", "activity": "Pumps"}).json()
    out["impact_pipes"] = client.get(f"{B}/activity-impact", params={"service": "Fire", "activity": "Pipes"}).json()
    activity(client, "create", "Empty")
    out["view_with_empty"] = client.get(f"{SP}/service/Fire").json()
    out["pnl"] = client.get(f"{SP}/pnl/Fire").json()
    out["projects"] = client.get("/api/projects").json() if False else [{"slug": SLUG, "project": "Fixture"}]
    Path(__file__).resolve().parent.parent.joinpath("frontend", "activity_delete_fixture.json").write_text(json.dumps(out, indent=1, default=str))
