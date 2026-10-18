"""Portable additive evidence envelope; legacy report fields remain unchanged."""

import hashlib
import platform
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def versions(*names):
    result = {"python": platform.python_version(), "platform": platform.system()}
    for name in names:
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = "unknown"
    return result


def envelope(report, producer, operation, root, inputs, artifacts, implementation):
    def artifact(item):
        path = Path(item["snapshot"] if "snapshot" in item else item["path"])
        return {
            "id": item["id"],
            "path": path.relative_to(root).as_posix(),
            "sha256": item["sha256"],
        }

    raw = report.get("status", "unknown")
    return {
        "schema": "cad-evidence/1",
        "producer": producer,
        "operation": operation,
        "status": raw if raw in ("pass", "fail", "unsupported") else "unknown",
        "reason": raw,
        "scope": report.get("scope", "Operation did not complete"),
        "versions": versions(producer),
        "implementation_sha256": hashlib.sha256(Path(implementation).read_bytes()).hexdigest(),
        "inputs": [artifact(x) for x in inputs],
        "artifacts": [artifact(x) for x in artifacts],
    }
