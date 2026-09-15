# SPDX-License-Identifier: GPL-3.0-or-later
"""BlendRelay MCP Blender extension. Importing this module does not register UI."""
from .version import __version__, VERSION, VERSION_TUPLE

bl_info = {
    "name": "BlendRelay MCP",
    "author": "BlendRelay MCP contributors",
    "version": VERSION_TUPLE,
    "blender": (4, 5, 0),
    "location": "3D View > Sidebar > BlendRelay",
    "description": "General-purpose AI agent control for Blender through MCP",
    "category": "3D View",
}

def register():
    from . import bridge_ui as ui
    ui.register()

def unregister():
    from . import bridge_ui as ui
    ui.unregister()
