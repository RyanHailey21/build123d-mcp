"""Opt-in MCP-to-live-FreeCAD smoke test; only creates a new three-part demo.
Usage: python scripts/smoke_freecad_handoff.py --output NEW.FCStd [--server-python PYTHON]
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def run(args):
    output = Path(args.output).resolve()
    assert not output.exists(), "Choose a new output path"
    parameters = StdioServerParameters(
        command=args.server_python,
        args=["-c", "from build123d_mcp.cli import main; main()"],
        cwd=str(output.parent),
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            assert "send_to_freecad" in names
            built = await client.call_tool(
                "execute",
                {
                    "code": """
from build123d import *
show(Pos(10,20,30)*Box(100,20,5), "base")
show(Pos(10,20,40)*Cylinder(5,10), "driver")
show(Pos(60,20,40)*Cylinder(10,10), "driven")
"""
                },
            )
            assert not built.is_error, built
            origin = {"pos": [0, 0, 0], "quat": [0, 0, 0, 1]}
            joints = [
                {
                    "name": "DriverPivot",
                    "type": "Revolute",
                    "part1": "base",
                    "part2": "driver",
                    "placement1": {"pos": [0, 0, 10], "quat": [0, 0, 0, 1]},
                    "placement2": origin,
                },
                {
                    "name": "DrivenPivot",
                    "type": "Revolute",
                    "part1": "base",
                    "part2": "driven",
                    "placement1": {"pos": [50, 0, 10], "quat": [0, 0, 0, 1]},
                    "placement2": origin,
                },
                {
                    "name": "DriveBelt",
                    "type": "Belt",
                    "part1": "driver",
                    "part2": "driven",
                    "placement1": origin,
                    "placement2": origin,
                    "radius1": 5,
                    "radius2": 10,
                },
            ]
            sent = await client.call_tool(
                "send_to_freecad",
                {
                    "filename": str(output),
                    "ground_part": "base",
                    "object_names": "base,driver,driven",
                    "joints_json": json.dumps(joints),
                },
            )
            assert not sent.is_error, sent
            texts = [c.text for c in sent.content if c.type == "text"]
            result = json.loads(texts[0])
            assert result["status"] == "success", result
            assert result["handoff"]["status"] == "opened", result
            assert result["kinematics_solved"] is True
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--server-python", default=sys.executable)
    asyncio.run(run(parser.parse_args()))
