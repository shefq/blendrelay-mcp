"""General-purpose Blender bridge, asset service, references and audit logging."""
from datetime import datetime, timezone
import json
import re
import shutil
import threading
import time
from pathlib import Path

from .asset_service import AssetService
from .bridge import Bridge
from .errors import ArchForgeError, uid
from .storage import RuntimeStorage
from archforge_blender.version import VERSION


class Service(Bridge, AssetService):
    def __init__(self, root, source_roots=()):
        self.storage = RuntimeStorage(root)
        self.store = self.storage  # Compatibility for integrations using service.store.root.
        self.lock = threading.RLock()
        self.source_roots = [Path(p).resolve() for p in source_roots]
        self.audit_path = self.storage.root / "audit.jsonl"
        self.bridge_init()
        self.assets_init()

    def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise ArchForgeError("INVALID_INPUT", "Parameters must be an object")
        started = time.monotonic()
        ok = False
        error_code = None
        with self.lock:
            function = getattr(self, "rpc_" + method.replace(".", "_"), None)
            if function is None:
                raise ArchForgeError("UNKNOWN_METHOD", method)
            try:
                result = function(**params)
                ok = True
                return result
            except ArchForgeError as error:
                error_code = error.code
                raise
            except TypeError as error:
                error_code = "INVALID_ARGUMENTS"
                raise ArchForgeError(error_code, str(error))
            except ValueError as error:
                error_code = "ASSET_POLICY" if method.startswith("assets.") else "INVALID_ARGUMENTS"
                raise ArchForgeError(error_code, str(error))
            finally:
                self._audit(method, params, ok, error_code, time.monotonic() - started)

    def _audit(self, method, params, ok, error_code, duration):
        try:
            if self.audit_path.exists() and self.audit_path.stat().st_size > 5 * 1024 * 1024:
                previous = self.audit_path.with_suffix(".previous.jsonl")
                previous.unlink(missing_ok=True)
                self.audit_path.replace(previous)
            safe = {key: str(params[key])[:120] for key in
                    ("instance_id", "operation_id", "job_id", "action") if key in params}
            entry = {"time": datetime.now(timezone.utc).isoformat(), "method": method, "ok": ok,
                     "error_code": error_code, "duration_ms": round(duration * 1000, 2),
                     "fields": sorted(params), "identifiers": safe}
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=True) + "\n")
        except OSError:
            pass

    def rpc_capabilities(self):
        return {
            "product": "ArchForge MCP", "version": VERSION, "protocol_version": 1,
            "mcp_protocol": "2026-07-28", "schema_version": "2.0.0",
            "general_blender": {
                "enabled": True, "python_execution": True, "full_scene_versions": True,
                "mode_aware_prompts": True, "typed_scene_inspection": True,
                "typed_batch_operations": True, "task_support": True,
            },
            "workflows": ["modeling", "sculpting", "shading", "rigging", "animation",
                          "geometry_nodes", "simulation", "lighting", "rendering",
                          "compositing", "video_editing", "scene_management", "automation"],
            "inspection_sections": ["objects", "collections", "materials", "node_groups",
                                    "actions", "armatures", "images", "worlds", "cameras",
                                    "lights", "scenes"],
            "image_workflow": "Use attached references, viewport sketches, selection views and focused views as visual context.",
        }

    def rpc_clear_data(self, include_assets=False, workspace_id=None):
        if workspace_id is not None:
            if not isinstance(workspace_id, str) or not re.fullmatch(r'[0-9a-f]{32}', workspace_id):
                raise ArchForgeError('INVALID_WORKSPACE', 'Invalid scene workspace ID')
            target = self.storage.root / 'workspaces' / workspace_id
            if target.is_dir(): shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            if include_assets:
                assets = self.storage.root / 'assets'
                if assets.is_dir(): shutil.rmtree(assets, ignore_errors=True)
                assets.mkdir(parents=True, exist_ok=True)
            return {'cleared': True, 'root': str(target), 'workspace_id': workspace_id}

        # CLI maintenance without a workspace ID clears every operational workspace.
        self.storage.clear()
        for path in self.bridge_dir.glob('*.json'): path.unlink(missing_ok=True)
        for name in ('agent_runs', 'scene_versions', 'conversations', 'restored_scenes',
                     'focused_views', 'selection_views', 'sources', 'workspaces'):
            target = self.storage.root / name
            if target.is_dir(): shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
        if include_assets:
            assets = self.storage.root / 'assets'
            if assets.is_dir(): shutil.rmtree(assets, ignore_errors=True)
            assets.mkdir(parents=True, exist_ok=True)
        for sketch_file in self.storage.root.glob('sketch_viewport.*'): sketch_file.unlink(missing_ok=True)
        return {'cleared': True, 'root': str(self.storage.root)}

    def rpc_source_register(self, path):
        source = Path(path).resolve()
        if not any(source.is_relative_to(root) for root in self.source_roots):
            raise ArchForgeError("SOURCE_NOT_ALLOWED", "Start the runtime with --allow-source-root for this file location")
        supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
        if source.suffix.lower() not in supported or not source.is_file():
            raise ArchForgeError("INVALID_SOURCE", "Use a supported raster reference image")
        if source.stat().st_size > 50 * 1024 * 1024:
            raise ArchForgeError("RESOURCE_LIMIT", "Reference image exceeds 50 MiB")
        source_id = uid("source")
        destination = self.storage.root / "sources" / (source_id + source.suffix.lower())
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        return {"source_id": source_id, "path": str(destination),
                "next_step": "Use this image as visual context for the requested Blender operation."}

    def rpc_artifact_read(self, path, offset=0, limit=524288):
        import base64
        artifact = Path(path).resolve()
        allowed = ("sources", "focused_views", "selection_views")
        if not any(artifact.is_relative_to(self.storage.root / name) for name in allowed) or not artifact.is_file():
            raise ArchForgeError("ARTIFACT_NOT_ALLOWED", "Only registered and generated visual artifacts can be read")
        if artifact.stat().st_size > 20 * 1024 * 1024:
            raise ArchForgeError("RESOURCE_LIMIT", "Artifact is too large")
        if not isinstance(offset, int) or not isinstance(limit, int) or offset < 0 or not 1 <= limit <= 524288:
            raise ArchForgeError("INVALID_RANGE", "Invalid artifact byte range")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff",
                ".tiff": "image/tiff"}.get(artifact.suffix.lower())
        if not mime:
            raise ArchForgeError("UNSUPPORTED_ARTIFACT", "Unsupported visual artifact")
        with artifact.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read(limit)
        return {"mime_type": mime, "data": base64.b64encode(chunk).decode(), "offset": offset,
                "eof": offset + len(chunk) >= artifact.stat().st_size,
                "total_bytes": artifact.stat().st_size}
