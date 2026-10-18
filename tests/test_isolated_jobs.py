"""Small independent fixtures; no persistent modeling worker or robot rebuild."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from build123d_mcp.isolated_jobs import prepare, run_job


@pytest.fixture
def part(tmp_path):
    from build123d_mcp.session import Session
    from build123d_mcp.tools.execute import execute_code
    from build123d_mcp.tools.export import export_file

    session = Session()
    execute_code(session, 'from build123d import Box; show(Box(2, 3, 4), "block")')
    path = tmp_path / "block.step"
    export_file(session, str(path), "step", "block")
    return {"id": "block", "path": str(path)}


def test_clearance_collision_and_allowed_contact(part, tmp_path):
    items = [part, dict(part, id="other", placement=[3, 0, 0, 0, 0, 0])]
    result = run_job(
        items,
        str(tmp_path / "clear"),
        pairs=[{"a": "block", "b": "other", "minimum_clearance_mm": 1}],
    )
    assert result["status"] == "pass"
    assert result["pairs"][0]["clearance_mm"] == pytest.approx(1)
    items[1]["placement"] = [1, 0, 0, 0, 0, 0]
    result = run_job(items, str(tmp_path / "collision"), pairs=[{"a": "block", "b": "other"}])
    assert result["status"] == "fail"
    assert result["pairs"][0]["overlap_mm3"] == pytest.approx(12)


def test_bundle_and_timeout_preserve_commit(part, tmp_path):
    out = tmp_path / "bundle"
    result = run_job([part], str(out), mode="bundle")
    assert result["status"] == "pass"
    assert len(result["artifacts"]) == 2
    committed = (out / "latest.json").read_bytes()
    timed = run_job([part], str(out), mode="bundle", timeout=0.001)
    assert timed["status"] == "timeout"
    assert (out / "latest.json").read_bytes() == committed
    assert json.loads((Path(timed["job_dir"]) / "report.json").read_text())["status"] == "timeout"


def test_invalid_inputs_fail_early(part, tmp_path):
    for items in (
        [],
        [part, part],
        [dict(part, id="../escape")],
        [dict(part, id="CON")],
        [dict(part, placement=[float("nan")] * 6)],
    ):
        with pytest.raises(ValueError):
            prepare(items)
    with pytest.raises(ValueError):
        run_job([part], str(tmp_path), pairs=[{"a": "block", "b": "missing"}])


def test_crash_does_not_publish(part, tmp_path):
    with patch("build123d_mcp.isolated_jobs.subprocess.Popen") as popen:
        popen.return_value.wait.return_value = 42
        result = run_job([part], str(tmp_path / "crash"), mode="bundle")
    assert result["status"] == "error"
    assert not (tmp_path / "crash/latest.json").exists()


def test_tools_registered():
    import inspect

    from build123d_mcp import server

    # These entrypoints intentionally bypass _resolve_session / WorkerSession.
    assert "_resolve_session" not in inspect.getsource(server.verify_saved_geometry)
    assert "_resolve_session" not in inspect.getsource(server.export_bundle)
