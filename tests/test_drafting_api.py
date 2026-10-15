"""Tests for the build123d://drafting-api resource (#260)."""

from build123d_mcp.tools.drafting_api import drafting_api


def test_reference_covers_the_public_api():
    """Every __all__ symbol of the installed library appears with a signature."""
    import inspect

    import build123d_drafting as bd

    ref = drafting_api(None)
    for name in bd.__all__:
        obj = getattr(bd, name)
        if inspect.ismodule(obj):
            continue
        # Callables are listed with their signature; constants (e.g. DEFAULT_FONT_PATH) by value.
        expected = f"{name}(" if callable(obj) else f"{name} = "
        assert expected in ref, f"public symbol '{name}' missing from the reference"


def test_reference_carries_signatures_not_just_names():
    ref = drafting_api(None)
    # Keyword names are the whole point of the resource (#260).
    assert "label" in ref
    assert "drawing_scale" in ref  # lint_drawing keyword — proves signatures, not just names


def test_resource_uses_worker_routing():
    """The MCP resource must call through the session proxy, not import the
    drafting library (and thereby OCC) in the parent process."""
    import inspect

    from build123d_mcp import server

    src = inspect.getsource(server.build123d_drafting_api)
    assert "_resolve_session().drafting_api()" in src


def test_constants_are_listed_without_host_paths(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("build123d_drafting")
    fake.__version__ = "9.9"
    fake.FONT = "/srv/venv/site-packages/build123d_drafting/fonts/Sans.ttf"
    fake.LIMITS = types.MappingProxyType({"title": 30, "sheet": 4})
    fake.HUGE = list(range(1000))
    fake.__all__ = ["FONT", "LIMITS", "HUGE"]
    monkeypatch.setitem(sys.modules, "build123d_drafting", fake)

    ref = drafting_api(None)
    assert "FONT = <path to bundled file 'Sans.ttf'>" in ref
    assert "/srv/venv" not in ref
    assert "LIMITS = {'title': 30, 'sheet': 4}" in ref
    assert "HUGE = <list: [0, 1, 2" in ref and "999" not in ref
