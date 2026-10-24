# FreeCAD 1.1+ Assembly & Kinematic Integration

This document outlines how `build123d-mcp` interacts with FreeCAD 1.1's native Assembly workbench, kinematic joints, and the FreeCAD GUI RPC server.

## Overview
The `export_freecad_assembly` tool bridges `build123d` programmatic solid modeling with FreeCAD 1.1's native Assembly solver and joint constraints.

## Architecture
1. **Geometric Export**: Individual solids are isolated to local coordinates and written as STEP files into a temporary workspace.
2. **Document Construction**: An isolated `freecadcmd` worker uses Qt's offscreen platform to build an `Assembly::AssemblyObject` and its joint group, including GUI metadata. The FreeCAD installation must include its GUI libraries and Qt offscreen plugin. No window or running FreeCAD RPC server is required.
3. **Kinematic Joints**: Constraints are created as `App::FeaturePython` objects using FreeCAD's native `JointObject.Joint` and `JointObject.GroundedJoint`.
4. **Detached Placements**: Because STEP imports do not have stable topological face indices, joints use detached mode (`Detach1 = True`, `Detach2 = True`) with explicit `Placement1` and `Placement2` local offsets derived from build123d `Joint` / `Location` coordinates.

## FreeCAD GUI & RPC Interactions
- **Visuals**: Joints in FreeCAD display Joint Coordinate System (JCS) markers (RGB planar axes) at the mate point. Tree view eye icons toggle these JCS markers, not the solids.
- **GUI Dispatch**: When sending Python code via the `freecad` MCP `execute_code` tool, the call queues on the Qt GUI thread. If a user is interacting with the 3D viewport or has a modal task panel open, dispatch waits until the event loop is idle.

## Ready-to-open exports

Parts and the assembly are saved visible, with native joint view providers and a fitted isometric view. Joint markers start hidden; toggle their tree visibility to inspect them. No follow-up Python repairs are required. Export preserves original placements and operates on copies, without relocating the live build123d objects. Explicit joint placements are local to those parts, not world coordinates.

`solve=true` succeeds only when FreeCAD returns solver code 0. Failed exports leave an existing destination unchanged. `solve=false` saves mates without claiming they were solved. `joints_json="[]"` explicitly exports without mates; an omitted string enables automatic discovery. An unconnected shape does not contain enough information to infer mechanical intent: callers must supply its mates or define build123d joints first. Rigid subassemblies can be supplied as compounds.

## Belt and gear mates

Explicit Belt and Gears mates require `radius1` and `radius2`: positive finite pitch radii in millimetres. GT2 pitch radius is tooth_count / pi. Belt couples parallel shafts in the same direction; Gears reverses direction. Shaft support revolutes are separate mates. A paired set of physical belts uses a single coupling constraint, not duplicate constraints.

```json
{"name":"Reduction", "type":"Belt", "part1":"motor", "part2":"rotor",
 "placement1":{"pos":[0,0,0],"quat":[0,0,0,1]},
 "placement2":{"pos":[0,0,0],"quat":[0,0,0,1]},
 "radius1":6.366197724, "radius2":50.929581789}
```

Optional `distance` and `angle` pass through to the native joint properties for applicable joint types. Each supplied angle/length limit is enabled independently. Coupled workspace restrictions still need application logic; independent angular limits do not enforce them.

## Implementation and regression evidence

Construction and solving run with GuiUp=0. A second, fresh offscreen worker initializes native GUI proxies and saves visibility and view state. Keeping these phases separate avoids both solver error dialogs and proxies imported before GUI initialization. Both workers share the operation's time budget. Publication is atomic and requires explicit completion markers, since FreeCADCmd may return exit code zero after a Python exception.

On Windows with FreeCAD 1.1, the focused export suite passed 10 tests (2026-10-23): existing revolute/slider exports, explicit mates, fallback bundle, belt radii validation, unchanged source placements, persisted display metadata, independent reopen/solve, stale-output rejection and an injected solver failure code preserving the old destination. A fresh belt export also opened in the normal running FreeCAD GUI with visible solids, working native joint markers and solver result 0, without a repair script. Other operating systems were not exercised locally.

When FreeCAD is absent, the fallback bundle is explicitly reported as `script_generated`, not a finished export. On a FreeCAD machine, run its script once with `BUILD123D_FREECAD_GUI_ONLY=0`, then again with `BUILD123D_FREECAD_GUI_ONLY=1` and `QT_QPA_PLATFORM=offscreen` to create its GUI metadata. The normal MCP export performs both phases automatically.
