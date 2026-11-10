#!/usr/bin/env python3
"""Static safety checks for optional RPG external-scene handoffs; no scene creation."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / "rpg" / "intimacy_handoff"
def load(name):
    return json.loads((FOLDER / name).read_text(encoding="utf-8"))
def validate():
    p = load("SCENE_PACKET.json")
    k = load("CHARACTER_KNOWLEDGE.json")
    assert p["schema_version"] == 2
    assert p["status"] in {"NOT_ACTIVE","PROPOSED","ACTIVE","RETURN_PROPOSED","RECONCILED","ARCHIVED","CANCELLED"}
    assert isinstance(p["established_context"], list) and isinstance(p["possible_developments"], list)
    assert isinstance(p["occurred_events"], list)
    assert isinstance(p["return"]["applied_report_ids"],list)
    ids = p["return"]["applied_report_ids"]
    assert len(ids) == len(set(ids)), "Duplicate applied report ID"
    if p["status"] not in ("NOT_ACTIVE","PROPOSED","CANCELLED"):
        for v in (p["scene_id"],p["source"]["base_commit"],p["start"]["date"],p["start"]["time"],p["start"]["location"],p["scene_type"]):
            assert v, "Active/returned packet is missing a required checkpoint field"
    if p["status"] == "NOT_ACTIVE":
        assert p["scene_id"] is None, "Template must not masquerade as active handoff"
        assert not p["occurred_events"], "Template must not create canonical events"
    if p["return"]["reconciled"]:
        assert p["status"] in ("RECONCILED","ARCHIVED")
        assert p["return"]["summary_received"]
    if p["return"]["summary_received"]:
        assert p["scene_id"] and p["return"]["terminal_reason"]
    assert k["schema_version"] == 1
    seen=set()
    for f in k["facts"]:
        assert f["id"] not in seen
        seen.add(f["id"])
        assert len(f["known_by"]) == len(set(f["known_by"]))
        assert not set(f["known_by"]) & set(f.get("not_known_by",[])), f["id"]
    facts = {f["id"]:f for f in k["facts"]}
    assert "Мэри" in facts["ellie_loves_stas"]["known_by"]
    assert "Стас" in facts["ellie_loves_stas"]["not_known_by"]
    assert p["source"]["save_chapter"] <= p["source"]["chapter"]
    print("Handoff V2 schema and baseline continuity: OK")
if __name__ == "__main__":
    validate()
