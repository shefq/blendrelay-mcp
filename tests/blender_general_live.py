"""Bootstrap a disposable GUI Blender for the general bridge integration test."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import bpy
from archforge_blender import register
register()
bpy.context.scene.archforge_runtime_dir=os.environ['ARCHFORGE_TEST_ROOT']
bpy.ops.archforge.refresh()
