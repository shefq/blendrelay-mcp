# BlendRelay MCP

**General-purpose AI agent control for Blender through MCP**

BlendRelay MCP connects local AI agents to Blender 4.5+. It can inspect and change arbitrary Blender files for modeling, sculpting, shading, rigging, animation, Geometry Nodes, simulations, lighting, rendering, compositing, video editing, scene organization and automation.

Install it from [PyPI](https://pypi.org/project/blendrelay-mcp/).

The Blender sidebar (**BlendRelay**) provides prompts, reference images, viewport sketches, target regions, selection context, quality profiles, permissions, and full-file recovery checkpoints. MCP tools perform the actual work.

## Requirements

- Blender 4.5 or newer.
- Blender must have been opened once so its user configuration directory exists.
- `uvx` (recommended) or Python 3.11+ with `pip`.
- Prompts sent from Blender currently require a signed-in OpenAI Codex CLI or Google Antigravity CLI. Install only the backend you intend to use.
- Claude Desktop, Claude Code, Cursor, and other MCP clients can connect directly to BlendRelay without Codex or Antigravity.

---

## Quick Start & Setup

### 1. Install & Setup

Install the published package and its bundled Blender extension:

```powershell
uvx blendrelay-mcp setup
```

Or install it with `pip`:

```powershell
python -m pip install blendrelay-mcp
blendrelay-mcp setup
```

`setup` creates BlendRelay's local runtime directory and installs the bundled extension into the newest supported Blender installation it finds. Use `blendrelay-mcp install-addon --blender-version 4.5` to target a specific Blender version.

For development from source:

```powershell
git clone https://github.com/shefq/blendrelay-mcp.git
cd blendrelay-mcp
python -m pip install -e .
blendrelay-mcp setup
```

To run diagnostics and verify your environment:

```powershell
blendrelay-mcp doctor
```

### 2. Configure MCP Client

Add BlendRelay MCP to your MCP client configuration (Claude Desktop, Claude Code, Cursor, Antigravity, etc.):

```json
{
  "mcpServers": {
    "blendrelay": {
      "command": "python",
      "args": ["-m", "blendrelay_mcp.cli", "mcp"]
    }
  }
}
```

For the published package, clients can launch it through `uvx`:

```json
{
  "mcpServers": {
    "blendrelay": {
      "command": "uvx",
      "args": ["blendrelay-mcp", "mcp"]
    }
  }
}
```

For Claude Code with the published package:

```powershell
claude mcp add --scope user blendrelay -- uvx blendrelay-mcp mcp
claude mcp list
```

For Claude Code during local development:

```powershell
claude mcp add --scope user blendrelay -- python -m blendrelay_mcp.cli mcp
claude mcp list
```

### 3. Enable Extension in Blender

1. Open Blender.
2. In **Edit → Preferences → Get Extensions / Add-ons**, enable **BlendRelay MCP**.
3. In the 3D Viewport sidebar (press `N`), switch to the **BlendRelay** tab.
4. Click **Connect / Refresh**.

---

## Data & Migration

- **Shared Runtime Root**: `%LOCALAPPDATA%\BlendRelayMCP` (customizable via `BLENDRELAY_DATA_DIR`).
- **Workspaces**: Scene-specific data is saved in `workspaces/<workspace-id>/`. The workspace ID is stored inside the `.blend` file (`blendrelay_workspace_id`).

---

## CLI Commands

The unified `blendrelay-mcp` CLI provides:

- `blendrelay-mcp` or `blendrelay-mcp mcp`: Start the MCP stdio gateway server.
- `blendrelay-mcp setup [--target <path> | --blender-version <version>]`: Migrate data and install into the newest compatible Blender by default.
- `blendrelay-mcp doctor [--strict]`: Validate Python, extension, and runtime health with a meaningful exit status.
- `blendrelay-mcp install-addon [--target <path> | --blender-version <version>]`: Install the bundled extension.
- `blendrelay-mcp runtime serve`: Run the background loopback runtime service manually.

---

## Blender Tools

- `get_scene_info`, `get_object_info`, `inspect_scene`, and `inspect_blender_data` provide bounded structured context.
- `mesh_edit` offers validated common mesh operations (extrude, inset, bevel, bridge, etc.).
- `build_scene_batch` handles objects, materials, lights, cameras, transforms, parenting, modifiers, duplication, deletion, and keyframes.
- `execute_blender_python` exposes the full Blender Python API with `br` helpers.
- `capture_viewport` and `capture_focused_view` provide visual verification and multi-angle framing.
- `blendrelay_blender_sessions`, `blendrelay_blender_job`, `blendrelay_get_capabilities`, `blendrelay_register_source`, and `blendrelay_read_artifact` manage session lifecycles, background jobs, and artifacts.
- Scene checkpoints and restoration allow non-destructive undo and safety recovery.

---

## Workflows and Verification

- **Workflows**: Auto mode classifies requests into creation, focused editing, shading, rigging, animation, nodes, simulation, rendering, compositing, sculpting, or data management.
- **Profiles**: Four workload profiles control budgets: Focused Task, Full Creation, Creation + Assets, and Complex Production. Finish quality can be set from Draft to Maximum.
- **Checkpoints**: BlendRelay automatically creates recovery checkpoints before an AI execution and a final checkpoint upon successful completion.
- **Asset Library**: Searches local caches, Poly Haven, and Poly Pizza under the active license policy.

---

## Testing

Run the test suite with:

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
```

Build the extension package:

```powershell
python tools/build_extension.py
```
