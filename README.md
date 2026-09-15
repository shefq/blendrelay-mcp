# ArchForge MCP

ArchForge MCP connects local AI agents to Blender 4.5. It can inspect and change arbitrary Blender files for modeling, sculpting, shading, rigging, animation, Geometry Nodes, simulations, lighting, rendering, compositing, video editing, scene organization and automation.

The Blender sidebar provides prompts, reference images, viewport sketches, target regions, selection context, quality profiles, permissions and full-file recovery checkpoints. MCP tools perform the actual work.

## Setup

Install the package from this repository and link or install `src/archforge_blender` as the Blender extension. Both the add-on and runtime use `%LOCALAPPDATA%\ArchForgeMCP` by default. This is the shared runtime root. Scene-specific data is stored in `workspaces/<workspace-id>/`; the ID is saved inside the `.blend`, so reopening the file reuses its workspace while a new Blender scene receives a new one. The shared asset cache and authenticated runtime transport stay at the root.

```powershell
archforge-runtime serve
```

Configure the MCP client to run `archforge-mcp`. In Blender, open **3D View → ArchForge** and choose **Connect / Refresh**.

## Blender tools

- `get_scene_info`, `get_object_info`, `inspect_scene`, and `inspect_blender_data` provide bounded structured context.
- `mesh_edit` offers validated common mesh operations.
- `build_scene_batch` handles common objects, materials, lights, cameras, transforms, parenting, modifiers, duplication, deletion and keyframes.
- `execute_blender_python` exposes the full Blender Python API for capabilities that do not have a typed tool.
- `capture_viewport` and `capture_focused_view` provide visual verification.
- Scene checkpoints, restoration, asset discovery/import and asynchronous job tools remain separate.

Prompts can start from Object, Edit, Pose, Sculpt, Paint and other Blender modes. ArchForge supplies the current mode and available selection context to the agent and asks it to restore the working mode after an operation.

## Workflows and verification

Auto mode classifies requests as creation, focused editing, shading, rigging, animation, nodes, simulation, rendering, compositing, sculpting or data management. Each category receives appropriate instructions and acceptance checks. Visual scene-completion rules are used only when the requested result needs them.

Four workload profiles control the starting and emergency MCP budgets: Focused Task, Full Creation, Creation + Assets and Complex Production. Draft, Standard, High and Maximum control finish quality separately. Allowances expand while accepted work succeeds.

ArchForge saves a recovery checkpoint before an AI run and a final checkpoint after successful completion. Arbitrary Python and unattended provider permissions remain visible scene-level controls.

## Asset Library

The optional Asset Library searches the local cache, Poly Haven and Poly Pizza under the active license policy. Imported assets retain source and attribution metadata. If no suitable asset is available, the agent can construct content procedurally or reuse existing Blender data.

## Runtime and MCP

The gateway supports MCP `2026-07-28`, bounded JSON schemas, task lifecycle operations, authenticated loopback communication, operation receipts, audit logging, job expiry and polling limits. Runtime token files receive current-user-only Windows ACLs.

Run tests with:

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
```
