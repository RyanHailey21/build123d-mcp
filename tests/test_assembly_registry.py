import json

import pytest

from build123d_mcp.session import Session
from build123d_mcp.tools.assembly_registry import prepare_assembly


def fixture():
    s = Session()
    s.execute("from build123d import *\nshow(Box(2,3,4), 'block')")
    spec = {
        "schema": "cad-assembly/1",
        "id": "Demo",
        "components": {"Block": {"object": "block", "kind": "printed"}},
        "groups": ["Base", "Arm"],
        "ground": "Base",
        "instances": [
            {"id": "b", "component": "Block", "group": "Base"},
            {
                "id": "a",
                "component": "Block",
                "group": "Arm",
                "placement": {"pos": [10, 0, 0], "quat": [0, 0, 0, 1]},
            },
        ],
        "joints": [{"name": "Pivot", "type": "Revolute", "part1": "Base", "part2": "Arm"}],
    }
    return s, spec


def test_instance_transform_bom_and_source_preserved():
    s, spec = fixture()
    source = s.objects["block"]
    original = tuple(source.location.position)
    report = json.loads(prepare_assembly(s, json.dumps(spec)))
    assert report["bom"][0]["quantity"] == 2
    assert report["ground_part"] == "asm_Demo_Base"
    assert report["joints"][0]["part2"] == "asm_Demo_Arm"
    assert tuple(source.location.position) == original
    assert source.parent is None
    assert s.objects["asm_Demo_Arm"].bounding_box().center().X == pytest.approx(10)
    s.namespace["assembly_spec"] = spec
    assert json.loads(prepare_assembly(s))["fingerprint"] == report["fingerprint"]


@pytest.mark.parametrize(
    "defect", ["duplicate", "missing_component", "empty_group", "bad_quaternion", "unknown_joint"]
)
def test_invalid_registry_never_registers_partial_groups(defect):
    s, spec = fixture()
    if defect == "duplicate":
        spec["instances"][1]["id"] = "b"
    if defect == "missing_component":
        spec["instances"][1]["component"] = "absent"
    if defect == "empty_group":
        spec["groups"].append("Empty")
    if defect == "bad_quaternion":
        spec["instances"][1]["placement"]["quat"] = [0, 0, 0, 0]
    if defect == "unknown_joint":
        spec["joints"][0]["part2"] = "Absent"
    before = set(s.objects)
    with pytest.raises((ValueError, KeyError)):
        prepare_assembly(s, json.dumps(spec))
    assert set(s.objects) == before


def test_declared_quantity_cannot_override_instance_count():
    s, spec = fixture()
    spec["components"]["Block"]["quantity"] = 999
    assert json.loads(prepare_assembly(s, json.dumps(spec)))["bom"][0]["quantity"] == 2
