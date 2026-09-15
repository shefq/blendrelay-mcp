"""Local authenticated runtime for BlendRelay's Blender bridge."""
import argparse
import json
import os
import secrets
import socketserver
import subprocess
import sys
from pathlib import Path

from .errors import BlendRelayError
from .protocol import Client, data_dir, frame, receive
from .service import Service


def secure_private(path):
    path = Path(path); os.chmod(path, 0o600)
    if os.name != "nt": return
    user, domain = os.environ.get("USERNAME"), os.environ.get("USERDOMAIN")
    identity = (domain + "\\" + user) if domain and user else user
    if not identity: return
    result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", identity + ":(F)"],
                            capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode: raise OSError("Could not protect runtime credentials with Windows ACLs")


def serve(root, source_roots=()):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    lockfile = open(root / "runtime.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            lockfile.seek(0); lockfile.write(b"0"); lockfile.flush(); lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lockfile.close(); raise SystemExit("A BlendRelay runtime already owns this data directory")
    service = Service(root, source_roots); token = secrets.token_urlsafe(32)
    if not service.audit_path.exists(): service.audit_path.touch()
    secure_private(service.audit_path)
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(35)
            while True:
                request = {}
                try:
                    request = receive(self.request)
                    if not isinstance(request, dict): raise BlendRelayError("INVALID_MESSAGE", "Expected an envelope")
                    if not secrets.compare_digest(str(request.get("token", "")), token):
                        raise BlendRelayError("UNAUTHORIZED", "Invalid session token")
                    if request.get("protocol_version") != 1:
                        raise BlendRelayError("PROTOCOL_VERSION", "Expected bridge protocol 1")
                    result = {"request_id": request.get("request_id"),
                              "result": service.dispatch(request.get("method", ""), request.get("params", {}))}
                    encoded = frame(result)
                except (EOFError, ConnectionError, TimeoutError): return
                except BlendRelayError as error:
                    encoded = frame({"request_id": request.get("request_id"), "error": error.as_dict()})
                except Exception as error:
                    print(f"BlendRelay request error: {type(error).__name__}: {error}", file=sys.stderr)
                    encoded = frame({"request_id": request.get("request_id"), "error": {
                        "code": "INTERNAL_ERROR", "message": "Operation failed; see runtime log",
                        "details": {}, "entity_ids": []}})
                try: self.request.sendall(encoded)
                except OSError: return
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = False
    server = Server(("127.0.0.1", 0), Handler)
    connection = {"product": "BlendRelay MCP", "protocol_version": 1,
                  "port": server.server_address[1], "token": token, "pid": os.getpid()}
    temporary = root / "connection.tmp"
    temporary.write_text(json.dumps(connection)); secure_private(temporary)
    temporary.replace(root / "connection.json")
    print(f"BlendRelay MCP runtime ready on loopback port {server.server_address[1]}", file=sys.stderr, flush=True)
    try: server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt: pass
    finally:
        server.server_close(); service.storage.close()
        (root / "connection.json").unlink(missing_ok=True); lockfile.close()


def main(args_list=None):
    parser = argparse.ArgumentParser(description="BlendRelay MCP local Blender runtime")
    parser.add_argument("--data-dir", default=str(data_dir()))
    commands = parser.add_subparsers(dest="command", required=True)
    serve_command = commands.add_parser("serve")
    serve_command.add_argument("--allow-source-root", action="append", default=[])
    commands.add_parser("status")
    clear = commands.add_parser("clear-data")
    clear.add_argument("--include-assets", action="store_true")
    args = parser.parse_args(args_list)
    if args.command == "serve": serve(args.data_dir, args.allow_source_root); return
    client = Client(args.data_dir)
    result = client.call("capabilities") if args.command == "status" else client.call("clear_data", {"include_assets": args.include_assets})
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
