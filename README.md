# ArchForge MCP

ArchForge MCP connects a local MCP client and Blender 4.5 so an agent can
inspect and edit any Blender scene. It includes a procedural architecture
workflow, full-scene checkpoints, viewport sketches, and a policy-controlled
Asset Library.

The architecture helpers are optional. General modelling, sculpting, materials,
lighting, animation, rendering, compositing, Geometry Nodes, and scene repair
can use the same connection. ArchForge keeps the Blender interface compact: the
prompt, references, selection context, run limits, and safety approvals live in
the sidebar while MCP tools carry the actual work.

## Setup

Install the Python package from this repository, then install or link the
`src/archforge_blender` extension in Blender. Set the add-on Runtime setting to
the same data directory used by the runtime. By default it is:

`%LOCALAPPDATA%\ArchForgeMCP`

Start the runtime with:

```powershell
archforge-runtime serve
```

Configure the MCP gateway to run `archforge-mcp`. In Blender, open **3D View →
ArchForge**, then click **Connect / Refresh**.

## General Blender and safety controls

ArchForge exposes separate typed tools for common work:

- `inspect_scene`, `get_scene_info`, and `get_object_info` read bounded context.
- `mesh_edit` performs validated Edit Mode operations on the current selection.
- `capture_viewport` produces a bounded visual check.
- `execute_blender_python` is the escape hatch for work that the typed tools
  cannot express.
- `build_scene_batch` creates common objects, materials, collections, cameras,
  lights, and world settings in one validated call.
- checkpoint, restore, asset, and job tools remain separate so clients can
  understand their cost and effect before calling them.

Arbitrary Blender Python is disabled for each scene until **Allow arbitrary
Python execution** is enabled in ArchForge Settings. This approval is stored in
the `.blend` file. Headless Antigravity runs also need **Allow ArchForge MCP
tools**. After that explicit switch is enabled, the next prompt adds only
`mcp(archforge/*)` to Antigravity's global permission allow-list. **Allow
unattended CLI permissions** is also disabled by default; ArchForge adds the
provider's unrestricted-permission flag only when that separate switch is
enabled. For substantial generated scenes, enable Python execution and the
narrow ArchForge MCP permission, create a checkpoint, and keep unattended CLI
permissions off unless the selected agent provider specifically needs them.

Codex runs with its `workspace-write` sandbox and automatic approval review.
This lets its reviewer assess approval-required tools such as `import_asset`
instead of rejecting them automatically under the noninteractive `never`
policy. The reviewer can still reject a call, and ArchForge's per-scene Python
approval remains an independent requirement for `execute_blender_python`.

The Generate panel combines workload, output quality, and generation permissions.
Workload allowances expand automatically while accepted operations make progress.
Rejected submissions do not consume an edit, and polling limits apply per job.
The High-quality starting profiles are:

- **Object / Material:** 15 calls, 4 edits, 10 polls per job.
- **Full Build:** 40 calls, 10 edits, 20 polls per job.
- **Build + Assets:** 60 calls, 15 edits, 30 polls per job.
- **Complex Scene:** 100 calls, 25 edits, 40 polls per job.

Complex Scene can expand to 250 calls and 60 accepted edits. Draft, Standard,
High, and Maximum quality independently control finish level and verification.
Maximum quality raises the starting allowance and requires additional visual
refinement. Autonomous generation enables Blender Python for the scene and the
narrow ArchForge Antigravity MCP permission; provider-level approval behavior
still follows the selected CLI's own supported policy.

Full scene builds use `execute_blender_python` as a primary construction tool.
Generated scripts also receive `af`, a compact helper module for common materials,
primitives, collections, cameras, area lights, look-at orientation, and world setup.
Each successful operation returns progress including created object/material counts
and scene bounds.

ArchForge saves one recovery checkpoint before an AI run and one final checkpoint
after generation and its automatic visual audit. Individual Python stages do not
save full `.blend` copies.

The previous three manual limit fields remain hidden for older `.blend` files.

## MCP protocol and long jobs

The gateway supports MCP `2026-07-28` discovery and request metadata while
retaining `initialize` compatibility for existing 2024/2025 clients. Tool
inputs and structured outputs use bounded JSON Schema contracts. Destructive
tools are annotated, and arbitrary Python has its own explicit tool.

Blender and asset operations can be requested as MCP tasks. Tasks have a
bounded lifetime, concurrency limit, cancellation state, and polling ceiling.
Cancelling a task stops further gateway polling; an operation already executing
on Blender's main thread may finish, so use scene checkpoints for rollback.

Runtime RPC metadata is written to `%LOCALAPPDATA%\ArchForgeMCP\audit.jsonl`.
The audit log records methods, timing, result state, and opaque identifiers; it
does not record prompts, Python source, credentials, or file contents. On
Windows, runtime connection-token files receive a current-user-only ACL and
startup fails if that protection cannot be applied.

## Asset Library

The Asset Library is a separate panel in the ArchForge sidebar. Provider policy
is stored in the current Blender scene, so opening an older scene receives safe
defaults:

- Local Asset Cache is always available.
- Poly Haven is enabled by default. Its public catalogue assets are CC0.
- Poly Pizza is enabled by default; CC0 is enabled and CC-BY is disabled.
- Maximum download size is 200 MB and can be changed from 1 to 2048 MB.

Search checks the local cache first. When no suitable cached asset exists, it
searches only enabled providers and returns only assets allowed by the licence
filters. Poly Haven is suitable for HDRIs, PBR materials, rocks, terrain, and
realistic environment assets. Poly Pizza is suitable for lightweight props,
low-poly background assets, and rapid scene population.

Poly Pizza CC-BY assets require explicit opt-in. Each CC-BY import retains the
creator, source URL, and licence metadata on the imported collection and data
blocks, and adds a non-destructive record to `ATTRIBUTION.txt`. If CC-BY is
disabled the library reports: “This asset requires CC-BY attribution, which is
disabled in Asset Provider Policy.”

The public Poly Haven API does not require an API key, so ArchForge has no API
key field or account setup. Asset downloads are made by the local runtime, not
by Blender itself.

Downloads are stored at:

`%LOCALAPPDATA%\ArchForgeMCP\assets\<provider>\<asset-id>`

Each directory contains the original cached files and `metadata.json` with the
provider, asset ID, licence, creator, source, date, tags, original filename,
and integrity hashes. `ATTRIBUTION.txt` is stored directly in the `assets`
directory. Cached files are verified before import and are reused instead of
being downloaded again.

Imports create `ArchForge Assets / <Provider> / <Asset Name>` collections.
They leave unrelated objects, cameras, collections, materials, and active world
lighting alone. HDRIs are imported as separate World assets rather than being
made active automatically.

GLB and GLTF import uses Blender's built-in glTF importer. If its bundled NumPy
installation is incomplete, ArchForge first uses its per-user compatibility
fallback and stops before changing the scene if import still cannot proceed.

## MCP asset tools

The gateway exposes these policy-enforced tools:

- `get_asset_policy`
- `search_assets`
- `import_asset`
- `list_cached_assets`
- `refresh_asset_cache`
- `asset_job` for asynchronous search and import results

Agents must use these tools for external assets. If no asset is permitted or
both providers are disabled, the intended fallback is procedural Blender
geometry.

## Development checks

Run the standard test suite from the repository root:

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
```
