# Efficient scene editing

The Generate panel now offers **Auto / Focused Edit / Build**. Auto chooses a
focused workflow when something is selected, unless the prompt clearly requests
whole-scene work. This is a heuristic; use the override for ambiguous requests.
Both workflows allow corrections and arbitrary Python when required. There is no
hard four-call limit or automatic termination after the first edit.

## Context and verification

Edit Mode coordinates are rounded to six decimal places, expressed locally with
the object transform, and shared selected/adjacent vertices are deduplicated.
Truncated selections are explicitly marked. Fine-scale work can inspect the live
BMesh for full precision. Focused Object Mode tasks get one selection-framed view;
Build tasks retain three. Edit Mode captures the current viewport. Screenshots
default to 512 pixels on the longest edge; callers can request 128–1920 pixels.
Only explicitly attached references are included.

Verification defaults to Auto: mesh topology checks and visual checks when relevant
and the existing Auto-Verify toggle is enabled. Geometry suppresses final visual
checks; Geometry + Visual requests both. Verification instructions are agent
guidance. Structured mesh operations return geometry checks automatically. These
checks count degeneracy and boundaries; they do not prove alignment, watertightness
requirements, intersection freedom, or design quality.

## MCP jobs

Commands wait at the gateway for up to eight seconds by default (wait_seconds
accepts 0–30). This does not block Blender acknowledgement handling in the runtime.
Queued/running results can be polled; completed results do not need polling.
Responses omit submitted arguments, source code, and internal fingerprints.
Repeated identical results on the same gateway connection are marked unchanged;
repeat_result=true explicitly retrieves the full result again. Interrupted jobs
must not be blindly resubmitted. Duplicate edits are not automatically suppressed
because repeating an edit can be intentional; callers should reuse operation IDs
for retry safety.

## Structured mesh actions

Use mesh_edit (or blendrelay_blender_job with action=mesh_edit) and arguments containing
operation: bevel, extrude, inset, bridge, move_normal, or assign_material.
distance is in **local mesh units**, not automatically metres, and segments is
1–64. Material assignment takes an existing material name. These actions operate
on live Edit Mode selections, check parameters before editing, and return before/
after geometry reports. Bridge currently requires two equal-sized closed boundary
loops in each target mesh. More general shapes can use execute. validate_selection
accepts names or defaults to selected mesh objects.

## Conversation and usage

Continue conversation is optional and defaults off. When enabled, IDs are saved
per BlendRelay workspace ID and backend under the runtime conversations directory.
Codex uses exec resume with the explicit ID; Antigravity uses --conversation.
New Conversation clears the ID for that backend without deleting the old history.
Scene versions sharing the workspace ID share the conversation, with fresh scene
context supplied on every prompt. Copies made with Save As may also share that ID;
use New Conversation if the design has diverged. CLI resume failures are surfaced;
they do not silently start a replacement conversation.

Each run writes a .usage.json report beside its log. It separates provider input,
output, cache, and thinking fields and counts completed tool calls once. Stage
usage estimates are attributed to the preceding tool and are not exact billing.
Repeated requests generate a warning. Actual provider token savings require a
real task comparison; tests do not launch paid agent runs.

## Access boundaries

The focused prompt tells the agent not to search old logs, source, or unrelated
files. This is **not a filesystem sandbox**. CLI permissions govern separate shell
and file tools; unrestricted Blender Python can also access files. Existing CLI
permissions are unchanged. BlendRelay does not claim to enforce filesystem denial
or strict selected-only mutation for arbitrary scripts.

Only the repository is updated. Reload/restart the addon and MCP gateway/runtime
from the updated source when deploying. Existing installed addon copies are not
changed by this repository update.

Tests: python -m unittest discover -s tests -p "test_*.py" (with src on PYTHONPATH),
and Blender --background --factory-startup --python tests/blender_optimization.py.
