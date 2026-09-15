"""Commands for the packaged BlendRelay MCP distribution."""
import argparse
import sys
import tomllib
from pathlib import Path


def _run_doctor(data_dir_override: str | None = None, strict: bool = False) -> int:
    from blendrelay_blender.version import __version__
    from blendrelay_runtime.protocol import data_dir, Client
    from blendrelay_mcp.addon_installer import compatible_blender_versions

    target_dir = Path(data_dir_override).resolve() if data_dir_override else data_dir()
    warnings: list[str] = []
    errors: list[str] = []
    print("BlendRelay MCP Diagnostic Report")
    print("================================")
    print(f"BlendRelay MCP Version : {__version__}")
    print(f"Python Executable      : {sys.executable}")
    print(f"Python Version         : {sys.version.split()[0]} ({sys.platform})")
    print(f"Runtime Data Directory : {target_dir}")
    print(f"Data Dir Exists        : {target_dir.is_dir()}")
    if sys.version_info < (3, 11):
        errors.append("Python 3.11 or newer is required")
    if not target_dir.is_dir():
        warnings.append("runtime data directory has not been initialized")

    print("\nBlender Extensions:")
    try:
        versions = compatible_blender_versions()
    except RuntimeError as error:
        versions = []
        errors.append(str(error))
    installed = 0
    if not versions:
        print("  - No compatible Blender 4.5+ installation detected")
        errors.append("no compatible Blender 4.5+ installation detected")
    for _, version_dir in versions:
        extension = version_dir / "extensions" / "user_default" / "blendrelay_mcp"
        status = []
        manifest = extension / "blender_manifest.toml"
        if manifest.is_file():
            try:
                metadata = tomllib.loads(manifest.read_text(encoding="utf-8"))
                if metadata.get("id") != "blendrelay_mcp":
                    raise ValueError("manifest id is not blendrelay_mcp")
                installed += 1
                extension_version = str(metadata.get("version", "unknown"))
                status.append(f"BlendRelay MCP {extension_version} installed")
                if extension_version != __version__:
                    warnings.append(
                        f"Blender {version_dir.name} has extension {extension_version}; package is {__version__}"
                    )
            except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
                status.append(f"invalid BlendRelay extension ({error})")
                errors.append(f"Blender {version_dir.name} has an invalid BlendRelay extension")
        elif extension.exists():
            status.append("BlendRelay directory has no manifest")
            errors.append(f"Blender {version_dir.name} BlendRelay extension is incomplete")
        else:
            status.append("BlendRelay MCP not installed")
        print(f"  - Blender {version_dir.name}: {', '.join(status)}")
    if versions and not installed:
        errors.append("BlendRelay MCP is not installed in any compatible Blender version")

    print("\nRuntime Status:")
    connection_file = target_dir / "connection.json"
    if connection_file.is_file():
        try:
            capabilities = Client(str(target_dir)).call("capabilities")
            print(f"  - RUNNING (PID {capabilities.get('pid', 'unknown')}, protocol v{capabilities.get('protocol_version', '1')})")
        except Exception as error:
            print(f"  - FAILED: stale or unreachable connection ({error})")
            errors.append("runtime connection file is stale or unreachable")
    else:
        print("  - STOPPED (normal; the MCP gateway starts it automatically)")

    print("\nDiagnostic Summary:")
    for message in warnings:
        print(f"  WARNING: {message}")
    for message in errors:
        print(f"  ERROR: {message}")
    failed = bool(errors or (strict and warnings))
    print(f"  RESULT: {'FAIL' if failed else 'PASS'} ({len(errors)} error(s), {len(warnings)} warning(s))")
    return 1 if failed else 0


def _run_setup(target: str | None = None, blender_version: str | None = None) -> int:
    from blendrelay_runtime.protocol import data_dir
    from blendrelay_mcp.addon_installer import install_addon

    print("Setting up BlendRelay MCP...")
    try:
        runtime_dir = data_dir()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        print(f"Runtime data directory ready at {runtime_dir}")
        print(install_addon(target, blender_version))
    except Exception as error:
        print(f"BlendRelay MCP setup failed: {error}", file=sys.stderr)
        print("Run blendrelay-mcp doctor for details.", file=sys.stderr)
        return 1

    print("\nBlendRelay MCP setup finished successfully.")
    print("Next steps:")
    print("1. Enable 'BlendRelay MCP' in Blender Preferences -> Add-ons / Extensions.")
    print("2. Add `uvx blendrelay-mcp` to your MCP client.")
    return 0


def _add_install_location_arguments(parser: argparse.ArgumentParser) -> None:
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--target", help="Explicit Blender extension directory to replace.")
    location.add_argument("--blender-version", help="Installed Blender version, for example 4.5 or 5.0.")


def main():
    parser = argparse.ArgumentParser(
        prog="blendrelay-mcp",
        description="BlendRelay MCP: General-purpose AI agent control for Blender through MCP."
    )
    commands = parser.add_subparsers(dest="command")

    # mcp command
    commands.add_parser("mcp", help="Run the MCP stdio gateway (default).")

    # runtime command
    runtime_parser = commands.add_parser("runtime", help="Run or interact with the local bridge runtime.")
    runtime_parser.add_argument("runtime_args", nargs=argparse.REMAINDER, help="Arguments for the runtime daemon.")

    # install-addon command
    install = commands.add_parser("install-addon", help="Install the bundled Blender extension.")
    _add_install_location_arguments(install)

    # setup command
    setup_parser = commands.add_parser(
        "setup", help="Migrate data and install the extension into the newest compatible Blender."
    )
    _add_install_location_arguments(setup_parser)

    # doctor command
    doctor_parser = commands.add_parser("doctor", help="Inspect BlendRelay installation and diagnostic status.")
    doctor_parser.add_argument("--data-dir", help="Explicit data directory to inspect.")
    doctor_parser.add_argument("--strict", action="store_true", help="Treat diagnostic warnings as failures.")

    args, remaining = parser.parse_known_args()

    if args.command == "doctor":
        sys.exit(_run_doctor(args.data_dir, args.strict))
    elif args.command == "setup":
        sys.exit(_run_setup(args.target, args.blender_version))
    elif args.command == "install-addon":
        if remaining:
            parser.error("install-addon does not accept additional arguments")
        from blendrelay_mcp.addon_installer import install_addon
        print(install_addon(args.target, args.blender_version))
        return
    elif args.command == "runtime":
        from blendrelay_runtime.cli import main as run_runtime
        run_runtime(args.runtime_args or remaining)
        return
    elif args.command in (None, "mcp"):
        from blendrelay_mcp.server import main as run_mcp
        sys.argv = [sys.argv[0], *remaining]
        run_mcp()
        return
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
