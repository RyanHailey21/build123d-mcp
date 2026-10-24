import json
from pathlib import Path
from unittest.mock import Mock

from build123d_mcp import runtime_info
from build123d_mcp.project_build import build_project


def setup(tmp_path):
    (tmp_path / "a.py").write_text("result = 1")
    (tmp_path / "b.py").write_text("result = 2")
    jobs = [
        {
            "id": n,
            "source": n + ".py",
            "result_name": n,
            "outputs": [{"path": "build/" + n + ".step", "format": "step"}],
            "receipt": "build/" + n + ".json",
            "model_fingerprint": "demo",
        }
        for n in ("a", "b")
    ]
    manifest = tmp_path / "request.json"
    manifest.write_text(json.dumps({"schema": "cad-build/1", "jobs": jobs}))
    s = Mock()
    s.execute_file.return_value = '{"ok":true}'

    def export(path, fmt, name):
        Path(path).write_text(name)
        return "Exported to " + path

    s.export_file.side_effect = export
    return s, manifest


def test_incremental_build_reuses_unaffected_outputs(tmp_path):
    s, path = setup(tmp_path)
    assert [r["action"] for r in json.loads(build_project(s, str(path)))["results"]] == [
        "built",
        "built",
    ]
    s.reset_mock()
    assert [r["action"] for r in json.loads(build_project(s, str(path)))["results"]] == [
        "reused",
        "reused",
    ]
    assert not s.execute_file.called
    (tmp_path / "a.py").write_text("result = 3")
    report = json.loads(build_project(s, str(path)))
    assert [r["action"] for r in report["results"]] == ["built", "reused"]
    assert s.execute_file.call_count == 1


def test_tampering_and_failure_cannot_leave_pass_receipt(tmp_path):
    s, path = setup(tmp_path)
    build_project(s, str(path))
    (tmp_path / "build/a.step").write_text("tampered")
    s.export_file.side_effect = lambda *a: "Error: failed export"
    report = json.loads(build_project(s, str(path)))
    assert report["status"] == "failed"
    assert report["not_run"] == ["b"]
    assert not (tmp_path / "build/a.json").exists()


def test_declared_dependency_invalidates_only_its_model(tmp_path):
    s, path = setup(tmp_path)
    (tmp_path / "bought.step").write_text("supplier revision 1")
    spec = json.loads(path.read_text())
    spec["jobs"][0]["dependencies"] = ["bought.step"]
    path.write_text(json.dumps(spec))
    build_project(s, str(path))
    (tmp_path / "bought.step").write_text("supplier revision 2")
    report = json.loads(build_project(s, str(path)))
    assert [r["action"] for r in report["results"]] == ["built", "reused"]


def test_runtime_distinguishes_loaded_and_installed(monkeypatch):
    monkeypatch.setattr(runtime_info, "revision", lambda: "new")
    monkeypatch.setattr(runtime_info, "LOADED_REVISION", "old")
    monkeypatch.setattr(runtime_info, "source_hash", lambda: "changed")
    report = runtime_info.toolchain_status("new")
    assert report["restart_required"]
    assert not report["expected_revision_matches"]
    assert report["freecad_rpc"] == "not_probed"


def test_generated_dependency_builds_first_and_invalidates_downstream(tmp_path):
    s, path = setup(tmp_path)
    spec = json.loads(path.read_text())
    spec["jobs"][1]["dependencies"] = ["build/a.step"]
    spec["jobs"].reverse()
    path.write_text(json.dumps(spec))

    # Upstream export content follows its source revision.
    def export(dest, fmt, name):
        Path(dest).write_text((tmp_path / (name + ".py")).read_text())
        return "Exported to " + dest

    s.export_file.side_effect = export
    first = json.loads(build_project(s, str(path)))
    assert [r["id"] for r in first["results"]] == ["a", "b"]
    (tmp_path / "a.py").write_text("changed source")
    changed = json.loads(build_project(s, str(path)))
    assert [r["action"] for r in changed["results"]] == ["built", "built"]
