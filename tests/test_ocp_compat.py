"""OCP 7.x / 8.x compatibility (#488).

OCP 8 dropped the ``TopoDS.*_s`` downcasts and moved the ``TopTools_*`` NCollection
instantiations to ``OCP.collections``. Server code must go through
``build123d_mcp._ocp_compat`` so it runs on both.
"""

import re
from pathlib import Path

from build123d import Box

from build123d_mcp import _ocp_compat

SRC = Path(__file__).resolve().parent.parent / "src" / "build123d_mcp"

REMOVED_IN_OCP8 = re.compile(
    r"\bTopoDS\.\w+_s\b"
    r"|\bTopTools_IndexedMapOfShape\b"
    r"|\bTopTools_IndexedDataMapOfShapeListOfShape\b"
    r"|\bTopTools_HSequenceOfShape\b"
)


def test_no_ocp7_only_names_outside_the_compat_module():
    offenders = [
        f"{path.relative_to(SRC)}:{lineno}: {line.strip()}"
        for path in SRC.rglob("*.py")
        if path.name != "_ocp_compat.py"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if REMOVED_IN_OCP8.search(line)
    ]
    assert not offenders, "use build123d_mcp._ocp_compat instead:\n" + "\n".join(offenders)


def test_compat_names_work_on_the_installed_ocp():
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp

    box = Box(1, 2, 3)
    faces = _ocp_compat.IndexedMapOfShape()
    TopExp.MapShapes_s(box.wrapped, TopAbs_FACE, faces)
    assert faces.Extent() == 6
    assert _ocp_compat.to_face(faces.FindKey(1)).ShapeType() == TopAbs_FACE

    ancestors = _ocp_compat.IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(box.wrapped, TopAbs_EDGE, TopAbs_FACE, ancestors)
    assert ancestors.Extent() == 12
    assert ancestors.FindFromIndex(1).Extent() == 2
    assert _ocp_compat.to_edge(ancestors.FindKey(1)).ShapeType() == TopAbs_EDGE

    seq = _ocp_compat.HSequenceOfShape()
    seq.Append(box.edges()[0].wrapped)
    assert seq.Length() == 1

    assert _ocp_compat.to_vertex(box.vertices()[0].wrapped).ShapeType() == TopAbs_VERTEX
    assert _ocp_compat.to_solid(box.solids()[0].wrapped) is not None
    assert _ocp_compat.to_wire(box.faces()[0].outer_wire().wrapped) is not None
    assert _ocp_compat.to_shell(box.shells()[0].wrapped) is not None
