# SPDX-License-Identifier: GPL-3.0-or-later
"""Single source of truth for BlendRelay version.

Reads the authoritative version from blender_manifest.toml.
"""
from pathlib import Path
import tomllib

def _load_version() -> str:
    manifest = Path(__file__).parent / "blender_manifest.toml"
    if manifest.is_file():
        try:
            with open(manifest, "rb") as f:
                data = tomllib.load(f)
                if "version" in data:
                    return str(data["version"])
        except Exception:
            pass
    try:
        from importlib.metadata import version
        return version("blendrelay-mcp")
    except Exception:
        pass
    return "0.0.0"

__version__ = _load_version()
VERSION = __version__
VERSION_TUPLE = tuple(int(x) for x in __version__.split(".") if x.isdigit())
