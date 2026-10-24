"""Declarative assembly metadata; source objects are never moved or reparented."""

import copy
import hashlib
import json
import math
import re
from collections import Counter

from build123d import Compound, Location
from OCP.gp import gp_Quaternion, gp_Trsf, gp_Vec
from OCP.TopLoc import TopLoc_Location

from build123d_mcp.tools.export_freecad import _extract_assembly_joints

IDENTITY = {"pos": [0, 0, 0], "quat": [0, 0, 0, 1]}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError("Assembly IDs must be letters/digits/underscores and start with a letter")
    return value


def transform(spec):
    pos, quat = spec.get("pos"), spec.get("quat")
    if not isinstance(pos, list) or not isinstance(quat, list) or len(pos) != 3 or len(quat) != 4:
        raise ValueError("Placement requires pos[3], quat[4]")
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in pos + quat):
        raise ValueError("Placement must contain finite numbers")
    if abs(sum(v * v for v in quat) - 1) > 1e-6:
        raise ValueError("Placement quaternion must have unit length")
    t = gp_Trsf()
    t.SetRotation(gp_Quaternion(*quat))
    t.SetTranslationPart(gp_Vec(*pos))
    return Location(TopLoc_Location(t))


def prepare_assembly(session, spec_json: str = "@assembly_spec") -> str:
    spec = (
        copy.deepcopy(session.namespace.get("assembly_spec"))
        if spec_json == "@assembly_spec"
        else json.loads(spec_json)
    )
    if not isinstance(spec, dict) or spec.get("schema") != "cad-assembly/1":
        raise ValueError("Expected cad-assembly/1 (or define assembly_spec in the CAD source)")
    name = identifier(spec["id"])
    components = spec["components"]
    if not isinstance(components, dict) or not components:
        raise ValueError("Components must be a nonempty dictionary")
    groups = {identifier(g): [] for g in spec["groups"]}
    if len(groups) != len(spec["groups"]) or not groups or spec["ground"] not in groups:
        raise ValueError("Groups must be unique and include the ground group")
    instances = spec["instances"]
    if not instances:
        raise ValueError("Assembly needs instances")
    ids = set()
    counts = Counter()
    membership = {g: [] for g in groups}
    for item in instances:
        iid = identifier(item["id"])
        if iid in ids:
            raise ValueError("Duplicate instance: " + iid)
        ids.add(iid)
        component = item["component"]
        definition = components[component]
        identifier(component)
        shape = session.objects[definition["object"]]
        group = item["group"]
        if group not in groups:
            raise ValueError("Unknown rigid group: " + group)
        placed = copy.deepcopy(shape)
        placed = placed.moved(transform(item.get("placement", IDENTITY)))
        placed.label = iid
        groups[group].append(placed)
        membership[group].append(iid)
        counts[component] += 1
    if any(not shapes for shapes in groups.values()):
        raise ValueError("Empty rigid groups are not allowed")
    unused = set(components) - set(counts)
    if unused:
        raise ValueError("Unused components: " + ", ".join(sorted(unused)))
    # All joint connectors are in the rigid group's common assembly/world frame.
    joints = _extract_assembly_joints(groups, spec.get("joints", []))
    if len({j["name"] for j in joints}) != len(joints):
        raise ValueError("Joint names must be unique")
    for joint in joints:
        transform(joint["placement1"])
        transform(joint["placement2"])
        if joint["part1"] == joint["part2"]:
            raise ValueError("A mate must connect different rigid groups")
    names = {g: f"asm_{name}_{g}" for g in groups}
    prepared = {names[g]: Compound(children=shapes) for g, shapes in groups.items()}
    for joint in joints:
        joint["part1"] = names[joint["part1"]]
        joint["part2"] = names[joint["part2"]]
    bom = [
        {
            "id": c,
            "quantity": counts[c],
            **{k: v for k, v in components[c].items() if k not in ("object", "id", "quantity")},
        }
        for c in sorted(counts)
    ]
    report = {
        "schema": "cad-assembly-receipt/1",
        "id": name,
        "fingerprint": hashlib.sha256(
            json.dumps(spec, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "object_names": ",".join(names.values()),
        "ground_part": names[spec["ground"]],
        "joints": joints,
        "groups": membership,
        "bom": bom,
        "scope": "Declared membership and mates; no fit, collision or physical validation",
    }
    # Commit only after all references and geometry were successfully prepared.
    session.objects.update(prepared)
    return json.dumps(report, indent=2)
