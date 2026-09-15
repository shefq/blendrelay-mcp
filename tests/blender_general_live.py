"""Bootstrap a disposable GUI Blender for the general bridge integration test."""
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import bpy
from blendrelay_blender import register
register()
bpy.context.scene.blendrelay_runtime_dir = os.environ['BLENDRELAY_TEST_ROOT']
bpy.ops.blendrelay.refresh()
