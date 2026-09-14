# SPDX-License-Identifier: GPL-3.0-or-later
"""ArchForge MCP Blender extension. Importing this module does not register UI."""
from .version import __version__, VERSION, VERSION_TUPLE

bl_info = {
    "name": "ArchForge MCP",
    "author": "ArchForge MCP contributors",
    "version": VERSION_TUPLE,
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > ArchForge",
    "description": "General Blender automation with Codex prompts and adaptive quality workflows",
    "category": "3D View",
}

def register():
    from . import bridge_ui as ui
    ui.register()

def unregister():
    from . import bridge_ui as ui
    ui.unregister()
