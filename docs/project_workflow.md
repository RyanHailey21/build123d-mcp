# Source-declared assemblies and incremental projects

## Assembly registry

Declare `assembly_spec` in the model source and call `prepare_assembly()`, or provide the JSON
explicitly as spec_json. The schema is `cad-assembly/1`:

- id: stable identifier.
- components: keyed definitions with object (registered source shape name), kind, description,
  supplier/part number/source or other BOM metadata.
- groups: unique rigid group IDs; ground names one of them.
- instances: unique id, component reference, group, optional placement.
- joints: exporter-compatible native mates between group IDs.

Placements are `{"pos":[x,y,z],"quat":[qx,qy,qz,qw]}` in mm with unit quaternions.
An instance placement is applied to a COPY of the source shape (including its existing placement).
Rigid groups share the assembly/world frame; joint connector placements use that frame.
No union is performed and no source shape is moved or reparented.

The receipt contains exact instance-derived BOM quantities, group membership, fingerprint and
object_names/ground_part/joints for the existing exporter. This checks declarations, not physical fit.
Use `send_to_freecad(assembly_json="@assembly_spec")` to prepare and transfer in one call.

No motion intent is inferred from names or geometric proximity. A static assembly may have no mates;
a mechanism must declare its constraints. Kinematics and practical collision checks remain distinct.

## Incremental batch build

`build_project(manifest_path)` consumes `cad-build/1`:

```json
{"schema":"cad-build/1","jobs":[
  {"id":"bracket","source":"build/generated/bracket/model.py","result_name":"bracket",
   "dependencies":["purchased/motor.step"],
   "outputs":[{"path":"build/exports/bracket.step","format":"step"}],
   "receipt":"build/receipts/bracket.json"}
]}
```

Paths are relative to the manifest. The template's workflow.py build emits this format.
Declare imported files in dependencies. Up to 32 selected jobs are accepted; the tool executes CAD
through the existing worker. It does not launch stress, collision or historical analysis.
The current model becomes the last rebuilt job; use an isolated session to preserve interactive work.

Cached reuse requires matching source, dependencies, job settings, toolchain fingerprint, and actual
exported hashes. A changed or tampered artifact rebuilds its job. The first failure stops the batch,
invalidates that receipt and lists jobs not run. Never treat a partial batch as a package.

Generated-file dependencies are topologically ordered even if the submitted jobs are out of order;
their hashes are read after prerequisite jobs complete. Optional depends_on lists explicit job IDs.
Set assembly=true on a job to include its source-declared assembly registry/BOM in the build receipt.
An entirely cached batch does not reload geometry; execute its generated source before using the
live assembly registry if the desired assembly is not already active.

## Readiness and deployment

`toolchain_status(expected_revision="", probe_freecad=false)` reports the process's startup revision
and source hash separately from files currently installed. It reports restart_required if they differ.
The optional ping is bounded and local; it does not open/close/rebuild documents. Unknown Git revisions
(e.g. wheel installs without Git metadata) remain null. Capture this report after reconnecting, not
just after editing the client configuration. This feature does not claim to restart the MCP host.

The reusable template and skill live in mechanical-workflow-template. Its coupon and belt-drive
examples are synthetic, independent of robot geometry. Release package requirements belong to the
project, rather than being hard-coded into the CAD server.

