# Portable CAD workflow integration

Project facts, acceptance thresholds and check scheduling belong in project files,
not this server. Use https://github.com/RyanHailey21/mechanical-workflow-template
for a small project-local starter. No companion MCP server is needed.

Install uv and the Python version required by pyproject.toml. Clone this fork,
check out the exact tested commit, then run `uv sync --frozen`. Configure your
agent to launch `uv --project /absolute/checkout run --frozen build123d-mcp`.
Set the server working directory to the project workspace; --project selects the
installed environment without changing that working directory. This matters for
build123d allowed file roots. Call `cad_capabilities` first. It reports installed features and discovery only;
it is not a successful geometry check. Existing tools and result fields remain
compatible. Restart an already-running host after updating its checkout.

Saved-file operations now add an `evidence` object with schema `cad-evidence/1`:
producer, operation, status, reason, scope, versions, implementation SHA-256,
inputs and artifacts. Artifact paths are relative to the saved report directory,
so copy the whole job directory to move it. Original legacy absolute paths are
retained as audit context. Each input/output has an explicit ID and SHA-256.
Statuses are pass, fail, unknown or unsupported. Timeouts, crashes and incomplete
reports are unknown, never pass. A failed requirement is fail. Retain the raw
reason and partial results. Do not retry a whole session automatically.

The saved-geometry evidence starts at the STEP input. It does not prove that a
part came from a particular source script. Save the execute_file response and
source hash, then export in that same fresh session without intervening edits.
Project orchestration records that chain. Import names/labels must be mapped
explicitly to project instance IDs, never guessed from list order. Compare shape
validity, solid count, volume and bounds; declare transform conventions before
comparing placements. None of these checks proves continuous safe motion.
