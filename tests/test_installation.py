import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from blendrelay_mcp.addon_installer import blender_config_root, default_target, install_addon
from blendrelay_mcp.cli import _run_doctor, _run_setup


def _platform_blender_config(tmp: Path) -> tuple[Path, dict[str, str]]:
    if os.name == "nt":
        root = tmp / "Blender Foundation" / "Blender"
        env = {"APPDATA": str(tmp)}
    elif sys.platform == "darwin":
        root = tmp / "Library" / "Application Support" / "Blender"
        env = {"HOME": str(tmp)}
    else:
        root = tmp / "blender"
        env = {"XDG_CONFIG_HOME": str(tmp)}
    return root, env


class AddonInstallerTests(unittest.TestCase):
    def test_newest_compatible_blender_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            blender_root, env = _platform_blender_config(Path(tmp))
            for version in ("4.2", "4.5", "5.0", "preview"):
                (blender_root / version).mkdir(parents=True)
            with patch.dict(os.environ, env, clear=False):
                target = default_target()
            self.assertEqual(
                target,
                blender_root / "5.0" / "extensions" / "user_default" / "blendrelay_mcp",
            )

    def test_explicit_compatible_version_and_target_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            blender_root, env = _platform_blender_config(Path(tmp))
            (blender_root / "4.5").mkdir(parents=True)
            with patch.dict(os.environ, env, clear=False):
                self.assertIn("4.5", str(default_target("4.5")))
                with self.assertRaises(RuntimeError):
                    default_target("4.2")
            destination = Path(tmp) / "manual" / "blendrelay_mcp"
            message = install_addon(str(destination))
            self.assertTrue((destination / "blender_manifest.toml").is_file())
            self.assertIn(str(destination), message)

    def test_config_root_override_environment_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            override_root = Path(tmp) / "custom_blender"
            with patch.dict(os.environ, {"BLENDRELAY_BLENDER_CONFIG_ROOT": str(override_root)}, clear=False):
                self.assertEqual(blender_config_root(), override_root)


class CliHealthTests(unittest.TestCase):
    def test_setup_returns_failure_when_extension_install_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("blendrelay_runtime.protocol.data_dir", return_value=Path(tmp)), patch(
                "blendrelay_mcp.addon_installer.install_addon", side_effect=OSError("denied")
            ):
                self.assertEqual(_run_setup(), 1)

    def test_doctor_exit_status_reflects_extension_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            local = Path(tmp) / "local"
            runtime = local / "BlendRelayMCP"
            runtime.mkdir(parents=True)
            blender_root, env = _platform_blender_config(config_dir)
            version_root = blender_root / "5.0"
            version_root.mkdir(parents=True)
            environment = {**env, "LOCALAPPDATA": str(local)}
            with patch.dict(os.environ, environment, clear=False):
                self.assertEqual(_run_doctor(str(runtime)), 1)
                extension = version_root / "extensions" / "user_default" / "blendrelay_mcp"
                extension.mkdir(parents=True)
                (extension / "blender_manifest.toml").write_text(
                    'schema_version="1.0.0"\nid="blendrelay_mcp"\nversion="0.7.0"\n',
                    encoding="utf-8",
                )
                self.assertEqual(_run_doctor(str(runtime)), 0)


if __name__ == "__main__":
    unittest.main()
