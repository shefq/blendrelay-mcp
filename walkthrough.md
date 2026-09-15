# BlendRelay MCP 0.7.0 Rebrand Walkthrough

BlendRelay MCP is a general-purpose Blender integration. The Python distribution,
MCP server, runtime, Blender extension, UI, permissions and public documentation now use the
BlendRelay identity.

## Install and connect

```powershell
uvx blendrelay-mcp setup
codex mcp add blendrelay -- uvx blendrelay-mcp
blendrelay-mcp doctor
```

Setup selects the newest installed Blender version compatible with BlendRelay (4.5 or newer).
Use `--blender-version 4.5` or `--target <extension-directory>` when an explicit destination is
needed.

## Compatibility

- Existing BlendRelay files win conflicts; the legacy source remains untouched.
- Legacy branded MCP tool calls route to canonical BlendRelay tools without being advertised.
- Blender Python jobs use the `br` helper namespace.

## Release verification

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
python tests/blender_optimization.py
python tests/blender_general.py
python tools/build_extension.py
```

The extension artifact is written to `dist/blendrelay_mcp-0.7.0.zip`.
