"""Install BlendRelay's bundled Blender extension without a second checkout."""
from importlib.resources import files
from pathlib import Path
import os
import re
import shutil
import sys


MIN_BLENDER_VERSION = (4, 5)


def blender_config_root() -> Path:
    """Return the platform's standard parent directory for Blender versions."""
    override = os.environ.get("BLENDRELAY_BLENDER_CONFIG_ROOT")
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is unavailable; pass --target explicitly.")
        return Path(appdata) / "Blender Foundation" / "Blender"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blender"


def _version_tuple(name: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)\.(\d+)", name)
    return (int(match.group(1)), int(match.group(2))) if match else None


def compatible_blender_versions(config_root: Path | None = None) -> list[tuple[tuple[int, int], Path]]:
    """List installed compatible Blender configuration directories, oldest first."""
    root = Path(config_root) if config_root else blender_config_root()
    if not root.is_dir():
        return []
    found = []
    for child in root.iterdir():
        version = _version_tuple(child.name) if child.is_dir() else None
        if version and version >= MIN_BLENDER_VERSION:
            found.append((version, child))
    return sorted(found)


def default_target(blender_version: str | None = None, config_root: Path | None = None) -> Path:
    """Choose an explicit compatible version or the newest installed Blender version."""
    root = Path(config_root) if config_root else blender_config_root()
    if blender_version:
        version = _version_tuple(blender_version)
        if not version or version < MIN_BLENDER_VERSION:
            raise RuntimeError("BlendRelay MCP requires Blender 4.5 or newer.")
        version_dir = root / blender_version
        if not version_dir.is_dir():
            raise RuntimeError(f"Blender {blender_version} was not found under {root}")
    else:
        versions = compatible_blender_versions(root)
        if not versions:
            raise RuntimeError(
                f"No compatible Blender installation was found under {root}; "
                "install Blender 4.5+ or pass --target explicitly."
            )
        _, version_dir = versions[-1]
    return version_dir / "extensions" / "user_default" / "blendrelay_mcp"


def install_addon(target: str | None = None, blender_version: str | None = None) -> str:
    """Replace the installed extension with files bundled in this distribution."""
    if target and blender_version:
        raise ValueError("Use either target or blender_version, not both.")
    destination = Path(target) if target else default_target(blender_version)
    source = files("blendrelay_blender")
    temporary = destination.with_name(destination.name + ".new")
    backup = destination.with_name(destination.name + ".bak")

    def _remove(p: Path) -> None:
        if p.is_symlink():
            p.unlink()
        elif p.is_dir():
            try:
                os.rmdir(p)
            except OSError:
                shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    if temporary.exists() or temporary.is_symlink():
        _remove(temporary)
    temporary.mkdir(parents=True)
    for child in source.iterdir():
        if child.name == "__pycache__" or not child.is_file():
            continue
        shutil.copy2(child, temporary / child.name)
    if not (temporary / "blender_manifest.toml").is_file():
        _remove(temporary)
        raise RuntimeError("The installed package does not contain the Blender extension manifest.")
    if backup.exists() or backup.is_symlink():
        _remove(backup)
    if destination.exists() or destination.is_symlink():
        destination.replace(backup)
    try:
        temporary.replace(destination)
    except Exception:
        if backup.exists() or backup.is_symlink():
            backup.replace(destination)
        raise
    if backup.exists() or backup.is_symlink():
        _remove(backup)
    return f"Installed BlendRelay MCP extension at {destination}"
