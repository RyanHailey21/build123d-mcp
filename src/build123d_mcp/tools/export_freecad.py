"""Export build123d parts and kinematic joints to a FreeCAD 1.1 Assembly document (.FCStd)."""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any

from build123d import Location

from build123d_mcp.tools._budget import op_budget
from build123d_mcp.tools._paths import safe_output_path
from build123d_mcp.tools.export import _write_step

JOINT_TYPE_MAP: dict[str, str] = {
    "RigidJoint": "Fixed",
    "RevoluteJoint": "Revolute",
    "LinearJoint": "Slider",
    "CylindricalJoint": "Cylindrical",
    "BallJoint": "Ball",
}

VALID_FREECAD_JOINTS: set[str] = {
    "Fixed",
    "Revolute",
    "Cylindrical",
    "Slider",
    "Ball",
    "Distance",
    "Parallel",
    "Perpendicular",
    "Angle",
    "RackPinion",
    "Screw",
    "Gears",
    "Belt",
}


def find_freecad_cmd(custom_path: str = "") -> str | None:
    """Locate the freecadcmd executable across environment, PATH, and standard install paths."""
    if custom_path:
        path = os.path.expanduser(os.path.expandvars(custom_path))
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.realpath(path)

    env_path = os.environ.get("FREECAD_CMD") or os.environ.get("FREECAD_PATH")
    if env_path:
        resolved = os.path.expanduser(os.path.expandvars(env_path))
        if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
            return os.path.realpath(resolved)

    for name in ("freecadcmd", "FreeCADCmd", "freecad", "FreeCAD"):
        found = shutil.which(name)
        if found:
            return os.path.realpath(found)

    # Windows standard locations
    if os.name == "nt":
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\freecadcmd.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD\bin\freecadcmd.exe"),
            r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe",
            r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe",
            r"C:\Program Files\FreeCAD\bin\freecadcmd.exe",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.realpath(candidate)

    # Linux / macOS standard locations
    posix_candidates = [
        "/usr/bin/freecadcmd",
        "/usr/local/bin/freecadcmd",
        "/usr/bin/freecad",
        "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
        "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
    ]
    for candidate in posix_candidates:
        if os.path.isfile(candidate):
            return os.path.realpath(candidate)

    return None


def _location_to_pos_and_quat(
    loc: Location,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Extract position (x, y, z) and rotation quaternion (x, y, z, w) from build123d Location."""
    pos = loc.position
    trsf = loc.wrapped.Transformation()
    rot = trsf.GetRotation()
    return (
        (float(pos.X), float(pos.Y), float(pos.Z)),
        (float(rot.X()), float(rot.Y()), float(rot.Z()), float(rot.W())),
    )


def _resolve_target_objects(session: Any, object_names: str) -> dict[str, Any]:
    """Resolve target objects from session according to object_names spec."""
    if not session.objects:
        if session.current_shape is not None:
            return {"part": session.current_shape}
        raise ValueError("No shapes in session. Create geometry or use show() first.")

    if not object_names or object_names.strip() == "*":
        return dict(session.objects)

    selected: dict[str, Any] = {}
    for raw in object_names.split(","):
        name = raw.strip()
        if not name:
            continue
        if name not in session.objects:
            raise ValueError(f"Unknown object '{name}'. Registered: {list(session.objects.keys())}")
        selected[name] = session.objects[name]

    if not selected:
        raise ValueError("No matching objects found for export.")
    return selected


def _extract_assembly_joints(
    target_objects: dict[str, Any], explicit_joints: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Extract joint definitions and local coordinate placements from shapes or explicit spec."""
    if explicit_joints is not None:
        validated: list[dict[str, Any]] = []
        for i, j in enumerate(explicit_joints):
            if not isinstance(j, dict):
                raise ValueError("Each joint must be a JSON object")
            name = j.get("name") or f"Joint_{i + 1}"
            jtype = j.get("type", "Fixed")
            if jtype not in VALID_FREECAD_JOINTS:
                raise ValueError(
                    f"Unsupported FreeCAD joint type '{jtype}'. Valid types: {sorted(VALID_FREECAD_JOINTS)}"
                )
            p1 = j.get("part1")
            p2 = j.get("part2")
            if p1 not in target_objects:
                raise ValueError(f"Joint '{name}' references unknown part1 '{p1}'")
            if p2 not in target_objects:
                raise ValueError(f"Joint '{name}' references unknown part2 '{p2}'")

            plac1 = j.get("placement1", {"pos": [0.0, 0.0, 0.0], "quat": [0.0, 0.0, 0.0, 1.0]})
            plac2 = j.get("placement2", {"pos": [0.0, 0.0, 0.0], "quat": [0.0, 0.0, 0.0, 1.0]})
            if jtype in ("Belt", "Gears"):
                for field in ("radius1", "radius2"):
                    value = j.get(field)
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value <= 0
                    ):
                        raise ValueError(f"Joint '{name}' requires positive finite {field} in mm")
            validated.append(
                {
                    "name": name,
                    "type": jtype,
                    "part1": p1,
                    "part2": p2,
                    "placement1": plac1,
                    "placement2": plac2,
                    "angle_min": j.get("angle_min"),
                    "angle_max": j.get("angle_max"),
                    "length_min": j.get("length_min"),
                    "length_max": j.get("length_max"),
                    "radius1": j.get("radius1"),
                    "radius2": j.get("radius2"),
                    "distance": j.get("distance"),
                    "angle": j.get("angle"),
                }
            )
        return validated

    # Automatic joint discovery from build123d shape joints
    shape_to_name: dict[int, str] = {id(shape): name for name, shape in target_objects.items()}
    joints: list[dict[str, Any]] = []
    seen_pairs: set[tuple[int, int, str]] = set()

    for name1, shape1 in target_objects.items():
        shape_joints = getattr(shape1, "joints", {})
        if not shape_joints or not isinstance(shape_joints, dict):
            continue

        for jname, joint in shape_joints.items():
            connected = getattr(joint, "connected_to", None)
            if connected is None:
                continue

            conn_parent = getattr(connected, "parent", None)
            if conn_parent is None:
                continue

            parent_id = id(conn_parent)
            if parent_id not in shape_to_name:
                continue

            name2 = shape_to_name[parent_id]
            if name1 == name2:
                continue

            pair_key = (min(id(joint), id(connected)), max(id(joint), id(connected)), jname)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            raw_type = type(joint).__name__
            fc_type = JOINT_TYPE_MAP.get(raw_type, "Fixed")

            # Local coordinates within shape1 and shape2
            loc1_local = shape1.location.inverse() * joint.location
            loc2_local = conn_parent.location.inverse() * connected.location

            p1_pos, p1_quat = _location_to_pos_and_quat(loc1_local)
            p2_pos, p2_quat = _location_to_pos_and_quat(loc2_local)

            joint_def: dict[str, Any] = {
                "name": jname,
                "type": fc_type,
                "part1": name1,
                "part2": name2,
                "placement1": {"pos": list(p1_pos), "quat": list(p1_quat)},
                "placement2": {"pos": list(p2_pos), "quat": list(p2_quat)},
            }

            angular_range = getattr(joint, "angular_range", None)
            if angular_range and len(angular_range) == 2:
                joint_def["angle_min"] = float(angular_range[0])
                joint_def["angle_max"] = float(angular_range[1])

            linear_range = getattr(joint, "linear_range", None)
            if linear_range and len(linear_range) == 2:
                joint_def["length_min"] = float(linear_range[0])
                joint_def["length_max"] = float(linear_range[1])

            joints.append(joint_def)

    return joints


def _generate_freecad_assembly_script(
    doc_name: str,
    parts_step: dict[str, str],
    joints: list[dict[str, Any]],
    ground_part: str,
    output_fcstd: str,
    solve: bool,
    placements: dict[str, Any] | None = None,
) -> str:
    """Create a self-contained worker with GUI metadata but no visible window.

    Import native joint proxies only AFTER GuiUp is set, otherwise their GUI
    callbacks are partially initialized and fail when the file is reopened.
    JSON is data, never interpolated Python code or filesystem names.
    """
    payload = json.dumps(
        {
            "name": doc_name,
            "parts": parts_step,
            "joints": joints,
            "ground": ground_part,
            "output": output_fcstd,
            "solve": solve,
            "placements": placements or {},
        },
        allow_nan=False,
    )
    return (
        "data = __import__('json').loads("
        + repr(payload)
        + ")\n"
        + r"""
import sys, os, json
import FreeCAD as App
import Part
# GUI metadata runs in a fresh process: do not reuse proxies imported with GuiUp=0.
if os.environ.get("BUILD123D_FREECAD_GUI_ONLY") == "1":
    import FreeCADGui as Gui
    Gui.showMainWindow()
    Gui.getMainWindow().hide()
    sys.path.insert(0, App.getHomePath())
    sys.path.insert(0, os.path.join(App.getHomePath(), "Mod", "Assembly"))
    import JointObject, UtilsAssembly
    doc = App.openDocument(data["output"])
    asm = doc.getObject("Assembly")
    asm.Visibility = True
    for obj in asm.Group:
        if obj.TypeId == "Part::Feature":
            obj.Visibility = True
    for joint in UtilsAssembly.getJointGroup(asm).Group:
        if hasattr(joint, "JointType"):
            JointObject.ViewProviderJoint(joint.ViewObject)
            if not hasattr(joint.ViewObject.Proxy, "switch_JCS1"):
                joint.ViewObject.Proxy.attach(joint.ViewObject)
            joint.Visibility = False
        elif hasattr(joint, "ObjectToGround"):
            JointObject.ViewProviderGroundedJoint(joint.ViewObject)
    doc.recompute()
    Gui.getDocument(doc.Name).activeView().viewAxonometric()
    Gui.getDocument(doc.Name).activeView().fitAll()
    doc.save()
    App.closeDocument(doc.Name)
    print("FREECAD_ASSEMBLY_EXPORT_OK")
    sys.exit(0)
sys.path.insert(0, App.getHomePath())
sys.path.insert(0, os.path.join(App.getHomePath(), "Mod", "Assembly"))
import JointObject, UtilsAssembly, Preferences
prefs = Preferences.preferences()
previous_auto_solve = prefs.GetBool("SolveInJointCreation", True)
prefs.SetBool("SolveInJointCreation", False)
def placement(spec):
    return App.Placement(App.Vector(*spec["pos"]), App.Rotation(*spec["quat"]))
doc = App.newDocument(data["name"])
try:
    asm = doc.addObject("Assembly::AssemblyObject", "Assembly")
    parts = {}
    for name, path in data["parts"].items():
        shape = Part.Shape()
        shape.read(path)
        feat = doc.addObject("Part::Feature", name)
        feat.Label = name
        feat.Shape = shape
        asm.addObject(feat)
        if name in data["placements"]:
            feat.Placement = placement(data["placements"][name])
        parts[name] = feat
    joint_group = UtilsAssembly.getJointGroup(asm)
    ground = joint_group.newObject("App::FeaturePython", "GroundedJoint")
    JointObject.GroundedJoint(ground, parts[data["ground"]])
    ground.Label = "Ground_" + data["ground"]
    for spec in data["joints"]:
        joint = joint_group.newObject("App::FeaturePython", spec["name"])
        JointObject.Joint(joint, JointObject.JointTypes.index(spec["type"]))
        joint.Label = spec["name"]
        joint.Reference1 = (parts[spec["part1"]], ("", ""))
        joint.Reference2 = (parts[spec["part2"]], ("", ""))
        joint.Detach1 = joint.Detach2 = True
        joint.Placement1 = placement(spec["placement1"])
        joint.Placement2 = placement(spec["placement2"])
        for key, prop in (("angle_min","AngleMin"), ("angle_max","AngleMax"),
                          ("length_min","LengthMin"), ("length_max","LengthMax")):
            if spec.get(key) is not None:
                setattr(joint, "Enable" + prop, True)
                setattr(joint, prop, spec[key])
        for key, prop in (("distance","Distance"), ("angle","Angle"),
                          ("radius1","Distance"), ("radius2","Distance2")):
            if spec.get(key) is not None:
                setattr(joint, prop, spec[key])
    doc.recompute()
    result = asm.solve() if data["solve"] else None
    if result not in (None, 0):
        raise RuntimeError("FreeCAD assembly solve failed with code " + str(result))
    doc.saveAs(data["output"])
    print("FREECAD_ASSEMBLY_BUILD_OK")
finally:
    prefs.SetBool("SolveInJointCreation", previous_auto_solve)
    App.closeDocument(doc.Name)
"""
    )


def export_freecad_assembly(
    session: Any,
    filename: str = "",
    ground_part: str = "",
    object_names: str = "*",
    joints_json: str = "",
    freecad_cmd: str = "",
    solve: bool = True,
) -> str:
    """Export build123d geometry as a FreeCAD 1.1 Assembly document (.FCStd) with kinematic joints.

    Args:
        session: Active build123d-mcp session.
        filename: Destination path for .FCStd assembly document (defaults to assembly.FCStd).
        ground_part: Name of the part to ground (defaults to first part or part with RigidJoint 'ground').
        object_names: Comma-separated object names or '*' for all registered session shapes.
        joints_json: Optional explicit JSON string of joints list. If empty, joints are
            automatically discovered from shape.joints and j.connected_to connections.
        freecad_cmd: Optional path to freecadcmd executable. Auto-detected across standard paths if empty.
        solve: Whether to solve kinematic constraints headlessly before saving (default: True).

    Returns:
        JSON string reporting exported parts, joints, solve status, and file paths.
    """
    target_filename = filename.strip() if filename else "assembly.FCStd"
    if not target_filename.lower().endswith(".fcstd"):
        target_filename += ".FCStd"

    abs_output = safe_output_path(target_filename)
    os.makedirs(os.path.dirname(abs_output), exist_ok=True)

    target_objects = _resolve_target_objects(session, object_names)
    if not target_objects:
        raise ValueError("No shapes found to include in assembly.")

    explicit_joints: list[dict[str, Any]] | None = None
    if joints_json.strip():
        try:
            parsed = json.loads(joints_json)
            if not isinstance(parsed, list):
                raise ValueError("joints_json must be a JSON array of joint objects.")
            explicit_joints = parsed
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid joints_json: {exc}") from exc

    joints = _extract_assembly_joints(target_objects, explicit_joints)

    # Determine ground part
    determined_ground = ground_part.strip()
    if not determined_ground:
        for name, shape in target_objects.items():
            shape_joints = getattr(shape, "joints", {})
            if isinstance(shape_joints, dict) and any(
                k.lower() in ("ground", "fixed", "base") for k in shape_joints
            ):
                determined_ground = name
                break
        if not determined_ground:
            determined_ground = next(iter(target_objects.keys()))

    if determined_ground not in target_objects:
        raise ValueError(
            f"Ground part '{determined_ground}' not in target parts: {list(target_objects.keys())}"
        )

    # Temporary directory for intermediate STEP files and script
    temp_dir = tempfile.mkdtemp(prefix="freecad_assembly_")
    try:
        parts_step: dict[str, str] = {}
        placements: dict[str, Any] = {}
        for index, (name, shape) in enumerate(target_objects.items()):
            step_file = os.path.join(temp_dir, f"part_{index}.step")
            pos, quat = _location_to_pos_and_quat(shape.location)
            placements[name] = {"pos": pos, "quat": quat}
            # Clear location to export intrinsic local geometry
            local_shape = copy.deepcopy(shape).locate(Location())
            _write_step(local_shape, step_file)
            parts_step[name] = step_file

        doc_stem = os.path.splitext(os.path.basename(abs_output))[0]
        script_content = _generate_freecad_assembly_script(
            doc_name=doc_stem,
            parts_step=parts_step,
            joints=joints,
            ground_part=determined_ground,
            output_fcstd=os.path.join(temp_dir, "result.FCStd"),
            solve=solve,
            placements=placements,
        )

        script_path = os.path.join(temp_dir, "build_assembly.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        fc_bin = find_freecad_cmd(freecad_cmd)
        if not fc_bin:
            # FreeCAD not installed: persist script and STEP files alongside output for downstream use
            bundle_dir = os.path.splitext(abs_output)[0] + "_freecad_bundle"
            os.makedirs(bundle_dir, exist_ok=True)
            saved_step_files: dict[str, str] = {}
            for name, step_p in parts_step.items():
                dest_step = os.path.join(bundle_dir, os.path.basename(step_p))
                shutil.copyfile(step_p, dest_step)
                saved_step_files[name] = dest_step

            dest_script = os.path.join(bundle_dir, "build_assembly.py")
            bundle_script = _generate_freecad_assembly_script(
                doc_name=doc_stem,
                parts_step=saved_step_files,
                joints=joints,
                ground_part=determined_ground,
                output_fcstd=abs_output,
                solve=solve,
                placements=placements,
            )
            with open(dest_script, "w", encoding="utf-8") as f:
                f.write(bundle_script)

            return json.dumps(
                {
                    "status": "script_generated",
                    "message": "FreeCAD executable not found. Generated assembly automation script and component STEP files.",
                    "bundle_directory": bundle_dir,
                    "script_path": dest_script,
                    "step_files": saved_step_files,
                    "parts": list(target_objects.keys()),
                    "grounded_part": determined_ground,
                    "joints": joints,
                    "target_fcstd": abs_output,
                },
                indent=2,
            )

        # FreeCAD executable found: run headlessly
        budget = max(30, int(op_budget(session)))
        deadline = time.monotonic() + budget
        proc = subprocess.run(
            [fc_bin, script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "BUILD123D_FREECAD_GUI_ONLY": "0"},
            timeout=budget,
            check=False,
        )

        staged_output = os.path.join(temp_dir, "result.FCStd")
        if (
            proc.returncode != 0
            or "FREECAD_ASSEMBLY_BUILD_OK" not in proc.stdout
            or not os.path.isfile(staged_output)
        ):
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"FreeCAD assembly creation failed: {err_msg}")
        gui_proc = subprocess.run(
            [fc_bin, script_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, deadline - time.monotonic()),
            check=False,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "BUILD123D_FREECAD_GUI_ONLY": "1"},
        )
        if gui_proc.returncode != 0 or "FREECAD_ASSEMBLY_EXPORT_OK" not in gui_proc.stdout:
            err_msg = gui_proc.stderr.strip() or gui_proc.stdout.strip()
            raise RuntimeError(f"FreeCAD assembly GUI metadata failed: {err_msg}")

        # Publish only a successfully completed worker output.
        with tempfile.NamedTemporaryFile(dir=os.path.dirname(abs_output), delete=False) as staging:
            publish_path = staging.name
        try:
            shutil.copyfile(staged_output, publish_path)
            os.replace(publish_path, abs_output)
        finally:
            if os.path.exists(publish_path):
                os.unlink(publish_path)
        file_size = os.path.getsize(abs_output)
        return json.dumps(
            {
                "status": "success",
                "file_path": abs_output,
                "file_size_bytes": file_size,
                "parts": list(target_objects.keys()),
                "grounded_part": determined_ground,
                "joints": joints,
                "kinematics_solved": solve,
                "gui_ready": True,
                "freecad_cmd": fc_bin,
            },
            indent=2,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
