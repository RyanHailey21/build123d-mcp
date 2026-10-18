import hashlib

from build123d_mcp.evidence import envelope


def test_portable_paths_and_unknown_status(tmp_path):
    p = tmp_path / "input.step"
    p.write_bytes(b"fixture")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    report = envelope(
        {"status": "timeout"},
        "build123d_mcp",
        "verify",
        tmp_path,
        [{"id": "part", "path": str(p), "sha256": digest}],
        [],
        __file__,
    )
    assert report["status"] == "unknown"
    assert report["reason"] == "timeout"
    assert report["inputs"][0]["path"] == "input.step"
    assert report["inputs"][0]["sha256"] == digest


def test_failed_geometry_remains_fail(tmp_path):
    assert (
        envelope({"status": "fail"}, "build123d_mcp", "verify", tmp_path, [], [], __file__)[
            "status"
        ]
        == "fail"
    )
