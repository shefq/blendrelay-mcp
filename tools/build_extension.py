"""Build the standalone Blender ZIP from the single extension source tree."""
from pathlib import Path
import zipfile
import hashlib
import json
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from blendrelay_blender.version import __version__

source = root / "src" / "blendrelay_blender"
dest = root / "dist"
dest.mkdir(exist_ok=True)
zip_path = dest / f"blendrelay_mcp-{__version__}.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".toml", ".txt"):
            z.write(path, path.relative_to(source))
print(json.dumps({"path": str(zip_path), "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest()}))
