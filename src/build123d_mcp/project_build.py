"""Incremental CAD batches through the existing worker; never run a kernel in this process."""

import hashlib
import json
from pathlib import Path

from build123d_mcp.evidence import versions
from build123d_mcp.runtime_info import LOADED_HASH
from build123d_mcp.tools._paths import safe_input_path, safe_output_path
from build123d_mcp.tools.execute_file import read_source


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_project(session, manifest_path: str) -> str:
    path = Path(safe_input_path(manifest_path))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "cad-build/1":
        raise ValueError("Expected cad-build/1")
    jobs = manifest["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 32:
        raise ValueError("Submit 1â€“32 affected models per batch")
    prepared = []
    destinations = set()
    names = set()
    for job in jobs:
        if job["id"] in names:
            raise ValueError("Duplicate job ID")
        names.add(job["id"])
        source = read_source(str(path.parent / job["source"]))
        dependencies = {
            str(s): safe_input_path(str(path.parent / s)) for s in job.get("dependencies", [])
        }
        outputs = []
        for item in job["outputs"]:
            fmt = item["format"]
            if fmt not in ("step", "stl", "3mf", "dxf", "svg"):
                raise ValueError("Unsupported output format")
            dest = Path(safe_output_path(str(path.parent / item["path"])))
            if dest.suffix.lower() != "." + fmt:
                raise ValueError("Output suffix must match format")
            outputs.append((fmt, dest))
        if not outputs:
            raise ValueError("A build job needs outputs")
        receipt = Path(safe_output_path(str(path.parent / job["receipt"])))
        for dest in [receipt] + [d for _, d in outputs]:
            if str(dest) in destinations or str(dest) == source[0]:
                raise ValueError("Output paths must be unique and cannot overwrite sources")
            destinations.add(str(dest))
        prepared.append((job, source, outputs, receipt, dependencies))
    if destinations.intersection(item[1][0] for item in prepared):
        raise ValueError("Outputs cannot overwrite another job's source")
    owners = {str(dest): item[0]["id"] for item in prepared for _, dest in item[2]}
    by_id = {item[0]["id"]: item for item in prepared}
    ordered, visiting, visited = [], set(), set()

    def visit(name):
        if name in visiting:
            raise ValueError("Cyclic build dependencies")
        if name in visited:
            return
        if name not in by_id:
            raise ValueError("Unknown prerequisite job: " + name)
        visiting.add(name)
        item = by_id[name]
        deps = list(item[0].get("depends_on", []))
        deps += [owners[p] for p in item[4].values() if p in owners]
        for dep in deps:
            visit(dep)
        visiting.remove(name)
        visited.add(name)
        ordered.append(item)

    for name in by_id:
        visit(name)
    prepared = ordered
    results = []
    for job, source, outputs, receipt, dependency_paths in prepared:
        # Hash produced dependencies AFTER prerequisites ran, including the first build.
        dependencies = {name: sha(p) for name, p in dependency_paths.items()}
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "job": job,
                    "source_sha256": source[2],
                    "dependencies": dependencies,
                    "versions": versions("build123d-mcp", "build123d"),
                    "runner_sha256": sha(__file__),
                    "toolchain_sha256": LOADED_HASH,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        old = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
        if (
            old.get("fingerprint") == fingerprint
            and len(old.get("artifacts", [])) == len(outputs)
            and all(
                dest.exists() and old["artifacts"][i].get("sha256") == sha(dest)
                for i, (_, dest) in enumerate(outputs)
            )
        ):
            results.append({"id": job["id"], "action": "reused", "receipt": str(receipt)})
            continue
        try:
            executed = json.loads(
                session.execute_file(
                    code=source[1],
                    source_path=source[0],
                    source_sha256=source[2],
                    result_name=job["result_name"],
                    snapshot="",
                )
            )
            if not executed.get("ok"):
                raise RuntimeError("Source execution failed")
            registry = (
                json.loads(session.prepare_assembly()) if job.get("assembly", False) else None
            )
            artifacts = []
            for fmt, dest in outputs:
                dest.parent.mkdir(parents=True, exist_ok=True)
                # Remove prior receipt before changing its artifacts; no stale pass can survive failure.
                if receipt.exists():
                    receipt.unlink()
                exported = session.export_file(str(dest), fmt, job["result_name"])
                if not isinstance(exported, str) or not exported.startswith("Exported to"):
                    raise RuntimeError("Export did not acknowledge success: " + str(exported))
                if not dest.exists():
                    raise RuntimeError("Export did not produce " + str(dest))
                artifacts.append({"path": str(dest), "format": fmt, "sha256": sha(dest)})
            report = {
                "schema": "cad-build-receipt/1",
                "id": job["id"],
                "fingerprint": fingerprint,
                "model_fingerprint": job.get("model_fingerprint"),
                "source_sha256": source[2],
                "artifacts": artifacts,
                "state": "exported",
                "assembly_registry": registry,
                "scope": "Exported CAD; not manufacturing approval",
            }
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            results.append({"id": job["id"], "action": "built", "receipt": str(receipt)})
        except Exception as exc:
            if receipt.exists():
                receipt.unlink()
            results.append({"id": job["id"], "action": "failed", "error": str(exc)})
            return json.dumps(
                {
                    "status": "failed",
                    "results": results,
                    "not_run": [j[0]["id"] for j in prepared[len(results) :]],
                }
            )
    return json.dumps({"status": "complete", "results": results})
