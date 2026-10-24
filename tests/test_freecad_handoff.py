import json
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from build123d_mcp import freecad_handoff as handoff


def fixture_rpc(monkeypatch, response=None, error=None):
    rpc = Mock()
    rpc.ping.return_value = True
    rpc.execute_code.return_value = response or {
        "success": True,
        "message": 'Output: BUILD123D_HANDOFF:{"document":"Transfer","assembly":"Assembly"}',
    }
    rpc.execute_code.side_effect = error

    @contextmanager
    def proxy(timeout):
        yield rpc

    monkeypatch.setattr(handoff, "_proxy", proxy)
    return rpc


def exporter(path):
    session = Mock()
    session.export_freecad_assembly.return_value = json.dumps(
        {
            "status": "success",
            "file_path": str(path),
            "kinematics_solved": True,
        }
    )
    return session


def test_send_opens_and_reports_document(tmp_path, monkeypatch):
    path = tmp_path / "new.FCStd"
    session = exporter(path)
    rpc = fixture_rpc(monkeypatch)
    result = json.loads(handoff.send_to_freecad(session, str(path)))
    assert result["handoff"]["status"] == "opened"
    assert result["handoff"]["document"] == "Transfer"
    assert session.export_freecad_assembly.call_args.args[-1] is True
    assert rpc.execute_code.call_count == 1


def test_send_refuses_overwrite_before_connecting(tmp_path, monkeypatch):
    path = tmp_path / "finished.FCStd"
    path.write_bytes(b"finished design")
    rpc = fixture_rpc(monkeypatch)
    session = exporter(path)
    with pytest.raises(ValueError, match="never overwrites"):
        handoff.send_to_freecad(session, str(path))
    assert not session.export_freecad_assembly.called
    assert not rpc.ping.called
    assert path.read_bytes() == b"finished design"


def test_unavailable_receiver_does_not_export(tmp_path, monkeypatch):
    rpc = fixture_rpc(monkeypatch)
    rpc.ping.side_effect = OSError("connection refused")
    session = exporter(tmp_path / "new.FCStd")
    with pytest.raises(RuntimeError, match="handoff unavailable"):
        handoff.send_to_freecad(session, str(tmp_path / "new.FCStd"))
    assert not session.export_freecad_assembly.called


@pytest.mark.parametrize(
    "response,error",
    [
        ({"success": False, "error": "GUI busy"}, None),
        ({"success": True, "message": "no acknowledgement"}, None),
        (None, TimeoutError("GUI queue timed out")),
    ],
)
def test_unconfirmed_handoff_preserves_export_and_never_retries(
    tmp_path, monkeypatch, response, error
):
    path = tmp_path / "new.FCStd"
    session = exporter(path)
    rpc = fixture_rpc(monkeypatch, response, error)
    result = json.loads(handoff.send_to_freecad(session, str(path)))
    assert result["status"] == "exported_handoff_failed"
    assert result["file_path"] == str(path)
    assert rpc.execute_code.call_count == 1


def test_fallback_bundle_is_not_sent(tmp_path, monkeypatch):
    session = exporter(tmp_path / "new.FCStd")
    session.export_freecad_assembly.return_value = '{"status":"script_generated"}'
    rpc = fixture_rpc(monkeypatch)
    result = json.loads(handoff.send_to_freecad(session, str(tmp_path / "new.FCStd")))
    assert result["handoff"]["status"] == "not_attempted"
    assert not rpc.execute_code.called


def test_filename_is_data_and_existing_documents_untouched(tmp_path):
    path = str(tmp_path / "quote';raise Exception('bad').FCStd")
    code = handoff._open_code(path)
    compile(code, "<handoff>", "exec")
    assert "reloadDocument" not in code
    assert "save(" not in code
    assert "App.closeDocument(received.Name)" in code  # only malformed new import


def test_source_registry_supplies_mates_and_bom(tmp_path, monkeypatch):
    path = tmp_path / "new.FCStd"
    session = exporter(path)
    fixture_rpc(monkeypatch)
    registry = {
        "object_names": "asm_Demo_Base,asm_Demo_Arm",
        "ground_part": "asm_Demo_Base",
        "joints": [],
        "bom": [{"id": "bolt", "quantity": 2}],
    }
    session.prepare_assembly.return_value = json.dumps(registry)
    result = json.loads(handoff.send_to_freecad(session, str(path), assembly_json="@assembly_spec"))
    assert result["assembly_registry"]["bom"][0]["quantity"] == 2
    assert session.export_freecad_assembly.call_args.args[1:4] == (
        "asm_Demo_Base",
        "asm_Demo_Base,asm_Demo_Arm",
        "[]",
    )
