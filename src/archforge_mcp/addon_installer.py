"""Install ArchForge's bundled Blender extension without a second checkout."""
from importlib.resources import files
from pathlib import Path
import os
import shutil


def default_target() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is unavailable; pass --target explicitly.")
    return Path(appdata) / "Blender Foundation" / "Blender" / "4.5" / "extensions" / "user_default" / "archforge_mcp"


def install_addon(target: str | None = None) -> str:
    """Replace the installed extension with files bundled in this distribution."""
    destination = Path(target) if target else default_target()
    source = files("archforge_blender")
    temporary = destination.with_name(destination.name + ".new")
    backup = destination.with_name(destination.name + ".bak")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    for child in source.iterdir():
        if child.name == "__pycache__" or not child.is_file():
            continue
        shutil.copy2(child, temporary / child.name)
    if not (temporary / "blender_manifest.toml").is_file():
        raise RuntimeError("The installed package does not contain the Blender extension manifest.")
    if backup.exists():
        shutil.rmtree(backup)
    if destination.exists():
        destination.replace(backup)
    temporary.replace(destination)
    if backup.exists():
        shutil.rmtree(backup)
    return f"Installed ArchForge extension at {destination}"
