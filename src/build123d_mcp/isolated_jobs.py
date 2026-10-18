"""Saved-geometry jobs. Never dispatch to or restart the live WorkerSession."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from build123d_mcp.tools._paths import check_input_size, safe_input_path, safe_output_path


def sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)


def prepare(instances):
    if not instances or len(instances) > 100:
        raise ValueError("Supply 1..100 saved STEP instances")
    result, seen = [], set()
    for item in instances:
        name = item["id"]
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name)
            or name.lower() in seen
            or re.fullmatch(r"(?i:con|prn|aux|nul|com[0-9]|lpt[0-9])", name)
        ):
            raise ValueError("Instance IDs must be unique safe filenames (case insensitive)")
        seen.add(name.lower())
        path = safe_input_path(item["path"])
        if Path(path).suffix.lower() not in (".step", ".stp"):
            raise ValueError("Saved STEP inputs required")
        check_input_size(path, "cad")
        placement = item.get("placement", [0, 0, 0, 0, 0, 0])
        if len(placement) != 6 or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in placement
        ):
            raise ValueError("placement is [x,y,z,rx,ry,rz], mm/degrees, finite numbers")
        result.append({"id": name, "path": path, "sha256": sha(path), "placement": placement})
    return result


def run_job(instances, output_dir, mode="verify", pairs=None, timeout=60):
    if mode not in ("verify", "bundle") or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError("Valid mode and timeout in (0,60] required")
    items = prepare(instances)
    pairs = pairs or []
    ids = {x["id"] for x in items}
    for pair in pairs:
        if pair["a"] not in ids or pair["b"] not in ids or pair["a"] == pair["b"]:
            raise ValueError("Pair must identify two distinct supplied instances")
        for key in ("minimum_clearance_mm", "allowed_overlap_mm3"):
            value = pair.get(key, 0)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("Clearance and allowed overlap must be nonnegative finite numbers")
    if len(pairs) > 100:
        raise ValueError("At most 100 explicit pairs per job")
    root = Path(safe_output_path(output_dir))
    root.mkdir(parents=True, exist_ok=True)
    job = root / ("job-" + uuid.uuid4().hex)
    job.mkdir()
    # Immutable input copies prevent concurrent edits from creating mixed revisions.
    import shutil

    for item in items:
        target = job / (item["id"] + ".input.step")
        shutil.copyfile(item["path"], target)
        if sha(target) != item["sha256"]:
            raise ValueError("Input changed while snapshotting")
        item["snapshot"] = str(target)
    request = {"mode": mode, "instances": items, "pairs": pairs}
    write(job / "request.json", request)
    with (job / "stdout.log").open("w") as out, (job / "stderr.log").open("w") as err:
        proc = subprocess.Popen(
            [sys.executable, "-m", "build123d_mcp.isolated_jobs", str(job)],
            stdout=out,
            stderr=err,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            code = proc.wait(timeout=timeout)
            status = "error" if code else "pass"
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            status, code = "timeout", None
    report_path = job / "report.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    if status != "pass" or not report:
        report.update(status=status if status != "pass" else "error", returncode=code)
    report.update(job_dir=str(job), inputs=items, verifier_sha256=sha(__file__))
    if any(not Path(x["path"]).is_file() or sha(x["path"]) != x["sha256"] for x in items):
        report.update(status="error", error="Source changed during job; publication withheld")
    from build123d_mcp.evidence import envelope
    report["evidence"] = envelope(report, "build123d-mcp", mode, job, items,
                                  report.get("artifacts", []), __file__)
    write(report_path, report)
    if mode == "bundle" and report.get("status") == "pass":
        # The pointer is the sole commit point. Never replace a previous release's files.
        write(
            root / "latest.json",
            {"job_dir": str(job), "manifest": str(report_path), "sha256": sha(report_path)},
        )
    return report


def worker(job):
    from importlib.metadata import version

    from build123d import Location, import_step

    from build123d_mcp.tools.export import _stl_write, _write_step

    request = json.loads((job / "request.json").read_text())
    report = {
        "status": "pass",
        "scope": "Supplied saved STEP instances and explicit pairs only; no continuous-motion proof",
        "versions": {x: version(x) for x in ("build123d", "build123d-mcp")},
        "parts": [],
        "pairs": [],
        "artifacts": [],
    }
    shapes = {}
    try:
        for item in request["instances"]:
            shape = import_step(item["snapshot"])
            p = item["placement"]
            shape = shape.moved(Location(tuple(p[:3]), tuple(p[3:])))
            valid = bool(shape.is_valid) and len(shape.solids()) > 0
            bb = shape.bounding_box()
            report["parts"].append(
                {
                    "id": item["id"],
                    "valid": valid,
                    "solids": len(shape.solids()),
                    "volume_mm3": shape.volume,
                    "bounds": [*bb.min, *bb.max],
                }
            )
            shapes[item["id"]] = shape
            if not valid:
                report["status"] = "fail"
            write(job / "report.json", report | {"status": "running"})
        for pair in request["pairs"]:
            a, b = shapes[pair["a"]], shapes[pair["b"]]
            distance = a.distance_to(b)
            overlap = a.intersect(b)
            volume = (
                0
                if overlap is None
                else (
                    abs(overlap.volume)
                    if hasattr(overlap, "volume")
                    else sum(abs(piece.volume) for piece in overlap)
                )
            )
            passed = (
                distance + 1e-7 >= pair.get("minimum_clearance_mm", 0)
                and volume <= pair.get("allowed_overlap_mm3", 0) + 1e-7
            )
            points = a.closest_points(b)
            report["pairs"].append(
                {
                    "a": pair["a"],
                    "b": pair["b"],
                    "clearance_mm": distance,
                    "overlap_mm3": volume,
                    "closest_points": [list(p) for p in points],
                    "status": "pass" if passed else "fail",
                }
            )
            if not passed:
                report["status"] = "fail"
            write(job / "report.json", report | {"status": "running"})
        if request["mode"] == "bundle" and report["status"] == "pass":
            for name, shape in shapes.items():
                step, stl = job / (name + ".step"), job / (name + ".stl")
                _write_step(shape, str(step))
                check = import_step(str(step))
                if (
                    not check.is_valid
                    or len(check.solids()) != len(shape.solids())
                    or not math.isclose(check.volume, shape.volume, rel_tol=1e-7, abs_tol=1e-6)
                ):
                    raise ValueError("STEP round trip failed: " + name)
                _stl_write(shape, str(stl))
                import trimesh

                mesh = trimesh.load(str(stl), force="mesh")
                if (
                    not mesh.is_watertight
                    or not mesh.is_winding_consistent
                    or mesh.volume <= 0
                    or not math.isclose(mesh.volume, shape.volume, rel_tol=0.01, abs_tol=1e-5)
                ):
                    raise ValueError("Mesh gate failed: " + name)
                for path in (step, stl):
                    report["artifacts"].append({"id": name, "path": str(path), "sha256": sha(path)})
                write(job / "report.json", report | {"status": "running"})
    except Exception as exc:
        report.update(status="error", error=f"{type(exc).__name__}: {exc}")
    write(job / "report.json", report)


if __name__ == "__main__":
    worker(Path(sys.argv[1]))
