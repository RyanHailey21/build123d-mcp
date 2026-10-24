"""Direct local build123d -> running FreeCAD handoff, without a second MCP dependency."""

from __future__ import annotations

import json
import os
import uuid
import xmlrpc.client
from pathlib import Path

from build123d_mcp.tools._paths import safe_output_path


class _Transport(xmlrpc.client.Transport):
    def __init__(self, timeout: float, token: str):
        super().__init__(headers=[("Authorization", f"Bearer {token}")] if token else [])
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


def _proxy(timeout: float):
    # Deliberately loopback-only: the exported file lives on this machine.
    port = int(os.environ.get("FREECAD_MCP_PORT", "9875"))
    if not 1 <= port <= 65535:
        raise ValueError("FREECAD_MCP_PORT must be between 1 and 65535")
    return xmlrpc.client.ServerProxy(
        f"http://127.0.0.1:{port}",
        allow_none=True,
        transport=_Transport(timeout, os.environ.get("FREECAD_MCP_TOKEN", "")),
    )


def _open_code(path: str) -> str:
    # repr is Python quoting; filenames must never become executable source.
    return f"""
def _build123d_receive():
    import os, json
    import FreeCAD as App, FreeCADGui as Gui
    path = {path!r}
    matching = [d for d in App.listDocuments().values()
                if d.FileName and os.path.normcase(os.path.realpath(d.FileName)) ==
                os.path.normcase(os.path.realpath(path))]
    if matching:
        raise RuntimeError("Transfer file is already open; no document was reloaded")
    received = App.openDocument(path)
    assembly = received.getObject("Assembly")
    if assembly is None:
        App.closeDocument(received.Name)
        raise RuntimeError("Transfer has no native Assembly")
    App.setActiveDocument(received.Name)
    view = Gui.getDocument(received.Name).activeView()
    view.viewAxonometric()
    view.fitAll()
    print("BUILD123D_HANDOFF:" + json.dumps({{
        "document": received.Name, "file_path": received.FileName,
        "assembly": assembly.Name,
    }}))
_build123d_receive()
"""


def send_to_freecad(
    session,
    filename: str = "",
    ground_part: str = "",
    object_names: str = "*",
    joints_json: str = "",
    freecad_cmd: str = "",
    assembly_json: str = "",
) -> str:
    """Solve/export a new snapshot and open it through the FreeCAD addon GUI queue."""
    output = safe_output_path(
        filename or str(Path("freecad-transfers") / f"assembly-{uuid.uuid4().hex[:12]}.FCStd")
    )
    if not output.lower().endswith(".fcstd"):
        output += ".FCStd"
    if os.path.exists(output):
        raise ValueError("send_to_freecad never overwrites files; choose a new filename or omit it")
    # Fail quickly before expensive export if the receiving app is unavailable.
    try:
        with _proxy(3) as rpc:
            if not rpc.ping():
                raise RuntimeError("RPC ping was not acknowledged")
    except Exception as exc:
        raise RuntimeError(
            "FreeCAD handoff unavailable. Start the FreeCAD MCP addon RPC server "
            "(localhost:9875 by default); configure FREECAD_MCP_TOKEN if required. "
            f"Connection error: {type(exc).__name__}"
        ) from None

    registry = None
    if assembly_json:
        registry = json.loads(session.prepare_assembly(assembly_json))
        object_names = registry["object_names"]
        ground_part = registry["ground_part"]
        joints_json = json.dumps(registry["joints"])
    result = json.loads(
        session.export_freecad_assembly(
            output, ground_part, object_names, joints_json, freecad_cmd, True
        )
    )
    if registry is not None:
        result["assembly_registry"] = registry
    if result.get("status") != "success":
        result["handoff"] = {"status": "not_attempted", "reason": "No completed FCStd export"}
        return json.dumps(result)
    try:
        with _proxy(35) as rpc:
            # Addon permits queue budget followed by execution budget.
            response = rpc.execute_code(_open_code(result["file_path"]), 15)
        if not isinstance(response, dict) or not response.get("success"):
            raise RuntimeError(
                str(response.get("error", "RPC rejected handoff"))
                if isinstance(response, dict)
                else "Invalid RPC response"
            )
        output_text = str(response.get("output", "")) + "\n" + str(response.get("message", ""))
        lines = [
            line.split("BUILD123D_HANDOFF:", 1)[1]
            for line in output_text.splitlines()
            if "BUILD123D_HANDOFF:" in line
        ]
        if not lines:
            raise RuntimeError("RPC returned without a document-open acknowledgement")
        result["handoff"] = {"status": "opened", **json.loads(lines[-1])}
    except Exception as exc:
        # Do not retry: a timed-out GUI operation may still finish. The export is retained.
        result["status"] = "exported_handoff_failed"
        result["handoff"] = {
            "status": "unconfirmed",
            "error": str(exc),
            "next_action": "Keep this export. Inspect FreeCAD before retrying; a queued open may finish later.",
        }
    return json.dumps(result, indent=2)
