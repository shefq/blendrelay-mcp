"""Run either entry point directly from a checkout without installing packages."""
import sys
from pathlib import Path
import os

src = str(Path(__file__).resolve().parents[1] / "src")
sys.path.insert(0, src)
# Propagate source discovery when the gateway launches its companion runtime.
os.environ["PYTHONPATH"] = src + os.pathsep + os.environ.get("PYTHONPATH", "")

mode = sys.argv.pop(1) if len(sys.argv) > 1 else "runtime"
if mode == "mcp":
    from blendrelay_mcp.server import main
elif mode == "cli":
    from blendrelay_mcp.cli import main
else:
    from blendrelay_runtime.cli import main
main()
