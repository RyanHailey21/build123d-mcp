"""drafting_api — auto-generated API reference for build123d-drafting-helpers.

Exposed as the ``build123d://drafting-api`` MCP resource (#260). The reference
is built with ``inspect`` against the installed library at request time, so it
always matches the version actually importable inside execute() — there is no
hand-maintained signature list to drift when drafting-helpers releases.
"""

import inspect
import os
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

_MAX_CONSTANT_REPR = 400


def _first_doc_line(obj: Any) -> str:
    doc = inspect.getdoc(obj)
    return doc.split("\n", 1)[0].strip() if doc else ""


def _signature(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))
    except (ValueError, TypeError):
        return "(...)"


def _class_entry(name: str, cls: type) -> list[str]:
    lines = [f"{name}{_signature(cls)}"]
    doc = _first_doc_line(cls)
    if doc:
        lines.append(f"    {doc}")
    for mname in sorted(vars(cls)):
        if mname.startswith("_"):
            continue
        # getattr resolves classmethod/staticmethod descriptors to callables
        # with real signatures; raw vars() values would inspect as "(...)".
        method = getattr(cls, mname, None)
        if not callable(method):
            continue
        lines.append(f"    .{mname}{_signature(method)}")
        mdoc = _first_doc_line(method)
        if mdoc:
            lines.append(f"        {mdoc}")
    return lines


def _constant_value(obj: Any) -> str:
    """Render a public constant: whole when short, never the server's install path."""
    if isinstance(obj, (str, os.PathLike)):
        # e.g. DEFAULT_FONT_PATH: the absolute path is host-specific (and private to
        # the server), so show only the bundled file's name. Check both path styles:
        # os.path.isabs("/srv/x") is False on Windows.
        text = os.fspath(obj)
        for flavour in (PurePosixPath, PureWindowsPath):
            path = flavour(text)
            if path.is_absolute():
                return f"<path to bundled file {path.name!r}>"
    if isinstance(obj, Mapping):
        obj = dict(obj)  # mappingproxy etc. read better as a plain dict
    value = repr(obj)
    if len(value) <= _MAX_CONSTANT_REPR:
        return value
    return f"<{type(obj).__name__}: {value[: _MAX_CONSTANT_REPR - 20]}...>"


def drafting_api(session: Any) -> str:
    """Build the API reference text from the installed build123d_drafting.

    The session argument is unused — the reference depends only on the
    installed library — but kept for the uniform worker-op calling convention.
    """
    try:
        import build123d_drafting as bd
    except ImportError as exc:
        return f"Error: build123d_drafting is not installed: {exc}"

    version = getattr(bd, "__version__", "unknown")
    classes: list[str] = []
    functions: list[str] = []
    constants: list[str] = []
    for name in getattr(bd, "__all__", sorted(dir(bd))):
        obj = getattr(bd, name, None)
        if obj is None or inspect.ismodule(obj):
            continue
        if inspect.isclass(obj):
            classes.extend(_class_entry(name, obj))
            classes.append("")
        elif callable(obj):
            functions.append(f"{name}{_signature(obj)}")
            doc = _first_doc_line(obj)
            if doc:
                functions.append(f"    {doc}")
            functions.append("")
        else:
            constants.append(f"{name} = {_constant_value(obj)}")

    header = [
        f"build123d-drafting-helpers API reference (installed version: {version})",
        "Generated from the live library — signatures match what execute() imports",
        "via `from build123d_drafting import ...`.",
        "",
        "=== Classes ===",
        "",
    ]
    sections = header + classes + ["=== Functions ===", ""] + functions
    if constants:
        sections += ["=== Constants ===", "", *constants]
    return "\n".join(sections).rstrip() + "\n"
