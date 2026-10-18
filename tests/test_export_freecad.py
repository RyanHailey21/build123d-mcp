"""Tests for export_freecad_assembly tool and kinematic joint translation."""

import json
import os
import subprocess

import pytest
from build123d import Location

from build123d_mcp.session import Session
from build123d_mcp.tools.export_freecad import (
    _location_to_pos_and_quat,
    export_freecad_assembly,
    find_freecad_cmd,
)


@pytest.fixture
def session():
    s = Session()
    s.execute("from build123d import *")
    return s


def test_location_to_pos_and_quat():
    loc = Location((10, 20, 30), (0, 0, 90))
    pos, quat = _location_to_pos_and_quat(loc)
    assert pytest.approx(pos[0]) == 10.0
    assert pytest.approx(pos[1]) == 20.0
    assert pytest.approx(pos[2]) == 30.0
    assert pytest.approx(quat[2]) == pytest.approx(0.70710678, abs=1e-4)
    assert pytest.approx(quat[3]) == pytest.approx(0.70710678, abs=1e-4)


def test_export_freecad_revolute_joint_end_to_end(session, tmp_path, monkeypatch):
    """Test full pipeline: build123d geometry -> revolute joint -> FreeCAD .FCStd assembly."""
    fc_bin = find_freecad_cmd()
    if not fc_bin:
        pytest.skip("FreeCAD executable not found in environment.")

    monkeypatch.chdir(tmp_path)
    code = """
base = Box(20, 20, 10)
j1 = RevoluteJoint('hinge', base, Axis((10, 10, 10), (0, 0, 1)), angular_range=(-90, 90))
show(base, 'base')

arm = Box(40, 8, 8)
j2 = RigidJoint('pin', arm, Location((4, 4, 0)))
show(arm, 'arm')

j1.connect_to(j2, angle=0)
"""
    session.execute(code)

    out_file = tmp_path / "robot_arm.FCStd"
    raw_res = export_freecad_assembly(session, str(out_file), object_names="base,arm")
    res = json.loads(raw_res)

    assert res["status"] == "success"
    assert res["file_path"] == str(out_file)
    assert out_file.exists() and out_file.stat().st_size > 0
    assert res["parts"] == ["base", "arm"]
    assert res["grounded_part"] == "base"
    assert len(res["joints"]) == 1

    joint = res["joints"][0]
    assert joint["name"] == "hinge"
    assert joint["type"] == "Revolute"
    assert joint["part1"] == "base"
    assert joint["part2"] == "arm"
    assert joint["angle_min"] == -90.0
    assert joint["angle_max"] == 90.0

    # Verify the generated .FCStd opens cleanly in FreeCAD and re-solves
    verify_script = f"""
import FreeCAD as App
doc = App.openDocument(r"{out_file}")
asm = doc.getObject("Assembly")
assert asm is not None
joints = asm.Joints
assert len(joints) >= 1
assert doc.getObject("base") is not None
assert doc.getObject("arm") is not None
asm.solve()
arm_pos = doc.getObject("arm").Placement.Base
assert abs(arm_pos.x - 6.0) < 1e-3
assert abs(arm_pos.y - 6.0) < 1e-3
assert abs(arm_pos.z - 10.0) < 1e-3
print("VERIFIED_OK")
"""
    check = subprocess.run(
        [fc_bin, "-c", verify_script], capture_output=True, text=True, check=False
    )
    assert "VERIFIED_OK" in check.stdout, (
        f"Verification failed:\nStdout: {check.stdout}\nStderr: {check.stderr}"
    )


def test_export_freecad_slider_joint(session, tmp_path, monkeypatch):
    """Test slider/linear joint translation."""
    fc_bin = find_freecad_cmd()
    if not fc_bin:
        pytest.skip("FreeCAD executable not found in environment.")

    monkeypatch.chdir(tmp_path)
    code = """
rail = Box(100, 20, 10)
j1 = LinearJoint('slide', rail, Axis((0, 10, 10), (1, 0, 0)), linear_range=(0, 80))
show(rail, 'rail')

carriage = Box(20, 20, 10)
j2 = RigidJoint('mount', carriage, Location((0, 10, 0)))
show(carriage, 'carriage')

j1.connect_to(j2, position=0)
"""
    session.execute(code)

    out_file = tmp_path / "slider.FCStd"
    raw_res = export_freecad_assembly(session, str(out_file), object_names="rail,carriage")
    res = json.loads(raw_res)

    assert res["status"] == "success"
    assert len(res["joints"]) == 1
    assert res["joints"][0]["type"] == "Slider"
    assert res["joints"][0]["length_min"] == 0.0
    assert res["joints"][0]["length_max"] == 80.0


def test_export_freecad_fallback_bundle_when_no_freecad(session, tmp_path, monkeypatch):
    """Test fallback generation of python assembly automation script when freecadcmd is missing."""
    import build123d_mcp.tools.export_freecad as mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "find_freecad_cmd", lambda custom="": None)

    code = """
b1 = Box(10, 10, 10)
show(b1, 'b1')
b2 = Box(5, 5, 5)
show(b2, 'b2')
"""
    session.execute(code)

    out_file = tmp_path / "standalone.FCStd"
    raw_res = export_freecad_assembly(session, str(out_file), object_names="b1,b2")
    res = json.loads(raw_res)

    assert res["status"] == "script_generated"
    bundle_dir = res["bundle_directory"]
    assert os.path.isdir(bundle_dir)
    assert os.path.isfile(res["script_path"])
    assert "b1" in res["step_files"] and os.path.isfile(res["step_files"]["b1"])
    assert "b2" in res["step_files"] and os.path.isfile(res["step_files"]["b2"])


def test_export_freecad_explicit_joints_json(session, tmp_path, monkeypatch):
    """Test explicit joints specification overriding/supplementing auto-discovery."""
    monkeypatch.chdir(tmp_path)
    session.execute("show(Box(10, 10, 10), 'partA'); show(Box(20, 10, 10), 'partB')")

    explicit_joints = [
        {
            "name": "CustomHinge",
            "type": "Revolute",
            "part1": "partA",
            "part2": "partB",
            "placement1": {"pos": [5.0, 5.0, 10.0], "quat": [0.0, 0.0, 0.0, 1.0]},
            "placement2": {"pos": [0.0, 5.0, 0.0], "quat": [0.0, 0.0, 0.0, 1.0]},
            "angle_min": -45.0,
            "angle_max": 45.0,
        }
    ]

    out_file = tmp_path / "custom.FCStd"
    raw_res = export_freecad_assembly(
        session,
        str(out_file),
        object_names="partA,partB",
        joints_json=json.dumps(explicit_joints),
    )
    res = json.loads(raw_res)

    assert res["status"] == "success" or res["status"] == "script_generated"
    assert len(res["joints"]) == 1
    assert res["joints"][0]["name"] == "CustomHinge"
    assert res["joints"][0]["type"] == "Revolute"
    assert res["joints"][0]["angle_min"] == -45.0


def test_export_freecad_error_cases(session, tmp_path):
    # Empty session
    empty_s = Session()
    with pytest.raises(ValueError, match="No shapes in session"):
        export_freecad_assembly(empty_s, str(tmp_path / "test.FCStd"))

    # Invalid ground part
    session.execute("show(Box(10, 10, 10), 'box')")
    with pytest.raises(ValueError, match="Ground part 'non_existent' not in target parts"):
        export_freecad_assembly(session, str(tmp_path / "test.FCStd"), ground_part="non_existent")

    # Invalid joint type in explicit json
    invalid_joints = [{"name": "j", "type": "InvalidJointType", "part1": "box", "part2": "box"}]
    with pytest.raises(ValueError, match="Unsupported FreeCAD joint type"):
        export_freecad_assembly(
            session,
            str(tmp_path / "test.FCStd"),
            joints_json=json.dumps(invalid_joints),
        )
