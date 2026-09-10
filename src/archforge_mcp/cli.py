"""Commands for the packaged ArchForge MCP distribution."""
import argparse


def main():
    parser = argparse.ArgumentParser(prog="archforge-mcp")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("mcp", help="Run the MCP stdio gateway.")
    install = commands.add_parser("install-addon", help="Install the bundled Blender extension.")
    install.add_argument("--target", help="Blender extension directory to replace.")
    args, remaining = parser.parse_known_args()
    if args.command == "install-addon":
        if remaining:
            parser.error("install-addon does not accept additional arguments")
        from archforge_mcp.addon_installer import install_addon
        print(install_addon(args.target))
        return
    if args.command in (None, "mcp"):
        from archforge_mcp.server import main as run_mcp
        import sys
        sys.argv = [sys.argv[0], *remaining]
        run_mcp()
        return
    parser.error("Choose 'mcp' or 'install-addon'.")


if __name__ == "__main__":
    main()
