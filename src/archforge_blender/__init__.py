# SPDX-License-Identifier: GPL-3.0-or-later
"""ArchForge MCP Blender extension. Importing this module does not register UI."""
bl_info = {"name":"ArchForge MCP","author":"ArchForge MCP contributors","version":(0,2,4),"blender":(4,5,0),"location":"3D View > Sidebar > ArchForge","description":"General Blender automation with Codex prompts and full-scene versions","category":"3D View"}

def register():
    from . import bridge_ui as ui
    ui.register()

def unregister():
    from . import bridge_ui as ui
    ui.unregister()
