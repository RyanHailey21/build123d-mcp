# FreeCAD 1.1+ Assembly & Kinematic Integration

This document outlines how `build123d-mcp` interacts with FreeCAD 1.1's native Assembly workbench, kinematic joints, and the FreeCAD GUI RPC server.

## Overview
The `export_freecad_assembly` tool bridges `build123d` programmatic solid modeling with FreeCAD 1.1's native Assembly solver and joint constraints.

## Architecture
1. **Geometric Export**: Individual solids are isolated to local coordinates and written as STEP files into a temporary workspace.
2. **Document Construction**: A Python script is executed via `freecadcmd` to build an `Assembly::Assembly` container with an `Assembly::JointGroup`.
3. **Kinematic Joints**: Constraints are created as `App::FeaturePython` objects using FreeCAD's native `JointObject.Joint` and `JointObject.GroundedJoint`.
4. **Detached Placements**: Because STEP imports do not have stable topological face indices, joints use detached mode (`Detach1 = True`, `Detach2 = True`) with explicit `Placement1` and `Placement2` local offsets derived from build123d `Joint` / `Location` coordinates.

## FreeCAD GUI & RPC Interactions
- **Visuals**: Joints in FreeCAD display Joint Coordinate System (JCS) markers (RGB planar axes) at the mate point. Tree view eye icons toggle these JCS markers, not the solids.
- **GUI Dispatch**: When sending Python code via the `freecad` MCP `execute_code` tool, the call queues on the Qt GUI thread. If a user is interacting with the 3D viewport or has a modal task panel open, dispatch waits until the event loop is idle.
