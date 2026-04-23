import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.config import settings


GLOBAL_TOOL_DEFINITIONS = [
    {
        "name": "global_upload_audio_files",
        "description": "Batch upload one or many audio files into server-side artifact storage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "artifact_id": {"type": "string"},
                            "filename": {"type": "string"},
                            "mime_type": {"type": "string"},
                            "content_base64": {"type": "string"},
                            "metadata": {"type": "object", "additionalProperties": True},
                        },
                        "required": ["filename", "content_base64"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    },
    {
        "name": "global_upload_text_files",
        "description": "Batch upload one or many text files into server-side artifact storage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "artifact_id": {"type": "string"},
                            "filename": {"type": "string"},
                            "content_text": {"type": "string"},
                            "encoding": {"type": "string"},
                            "metadata": {"type": "object", "additionalProperties": True},
                        },
                        "required": ["filename", "content_text"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    },
    {
        "name": "global_download_processed_artifacts",
        "description": "Download metadata and content handles for processed or uploaded artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_ids": {"type": "array", "items": {"type": "string"}},
                "include_inline_text": {"type": "boolean"},
            },
            "required": ["artifact_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "global_delete_uploaded_artifacts",
        "description": "Soft-delete or hard-delete uploaded artifacts in batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_ids": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["soft", "hard"]},
            },
            "required": ["artifact_ids"],
            "additionalProperties": False,
        },
    },
]


def global_tools_root() -> Path:
    root = Path(settings.storage_root).resolve() / "global-transfer-tools"
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifacts_root() -> Path:
    root = global_tools_root() / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_dir(artifact_id: str) -> Path:
    return artifacts_root() / artifact_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(target: Path) -> Path:
    return target / "manifest.json"


def _write_manifest(target: Path, payload: dict) -> None:
    target.mkdir(parents=True, exist_ok=True)
    _manifest_path(target).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_manifest(target: Path) -> dict:
    path = _manifest_path(target)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_artifact_manifest(artifact_id: str) -> dict:
    return _read_manifest(artifact_dir(str(artifact_id)))


def store_existing_file_as_artifact(
    source_path: Path,
    *,
    artifact_id: str | None = None,
    batch_id: str | None = None,
    kind: str | None = None,
    metadata: dict | None = None,
) -> dict:
    resolved_source = Path(source_path).resolve()
    if not resolved_source.exists() or not resolved_source.is_file():
        raise FileNotFoundError(f"Artifact source file not found: {resolved_source}")

    resolved_artifact_id = str(artifact_id or uuid4().hex)
    target = artifact_dir(resolved_artifact_id)
    target.mkdir(parents=True, exist_ok=True)
    target_path = target / resolved_source.name
    shutil.copy2(resolved_source, target_path)

    suffix = resolved_source.suffix.lower()
    inferred_kind = kind or (
        "audio" if suffix in {".wav", ".mp3", ".flac", ".m4a", ".ogg"} else "text" if suffix in {".csv", ".txt", ".json"} else "binary"
    )
    manifest = {
        "artifact_id": resolved_artifact_id,
        "batch_id": str(batch_id or uuid4().hex),
        "filename": resolved_source.name,
        "metadata": metadata or {},
        "kind": inferred_kind,
        "created_at": _now(),
        "deleted": False,
        "content_path": str(target_path),
    }
    _write_manifest(target, manifest)
    return {
        "artifact_id": resolved_artifact_id,
        "filename": resolved_source.name,
        "kind": inferred_kind,
        "content_path": str(target_path),
    }


def list_global_tool_definitions() -> list[dict]:
    return GLOBAL_TOOL_DEFINITIONS


def _result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _store_audio_batch(arguments: dict) -> dict:
    files = arguments.get("files")
    if not isinstance(files, list) or not files:
        return _result({"error": "files is required"}, is_error=True)
    batch_id = str(arguments.get("batch_id") or uuid4().hex)
    saved: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or uuid4().hex)
        filename = str(item.get("filename") or f"{artifact_id}.bin")
        payload = item.get("content_base64")
        if not isinstance(payload, str) or not payload:
            continue
        target = artifact_dir(artifact_id)
        target.mkdir(parents=True, exist_ok=True)
        content_path = target / filename
        content_path.write_bytes(base64.b64decode(payload.encode("utf-8")))
        manifest = {
            "artifact_id": artifact_id,
            "batch_id": batch_id,
            "filename": filename,
            "mime_type": item.get("mime_type") or "application/octet-stream",
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "kind": "audio",
            "created_at": _now(),
            "deleted": False,
            "content_path": str(content_path),
        }
        _write_manifest(target, manifest)
        saved.append({"artifact_id": artifact_id, "filename": filename, "content_path": str(content_path)})
    return _result({"batch_id": batch_id, "saved": saved})


def _store_text_batch(arguments: dict) -> dict:
    files = arguments.get("files")
    if not isinstance(files, list) or not files:
        return _result({"error": "files is required"}, is_error=True)
    batch_id = str(arguments.get("batch_id") or uuid4().hex)
    saved: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        artifact_id = str(item.get("artifact_id") or uuid4().hex)
        filename = str(item.get("filename") or f"{artifact_id}.txt")
        payload = item.get("content_text")
        if not isinstance(payload, str):
            continue
        target = artifact_dir(artifact_id)
        target.mkdir(parents=True, exist_ok=True)
        content_path = target / filename
        content_path.write_text(payload, encoding=str(item.get("encoding") or "utf-8"))
        manifest = {
            "artifact_id": artifact_id,
            "batch_id": batch_id,
            "filename": filename,
            "encoding": str(item.get("encoding") or "utf-8"),
            "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
            "kind": "text",
            "created_at": _now(),
            "deleted": False,
            "content_path": str(content_path),
        }
        _write_manifest(target, manifest)
        saved.append({"artifact_id": artifact_id, "filename": filename, "content_path": str(content_path)})
    return _result({"batch_id": batch_id, "saved": saved})


def _download_artifacts(arguments: dict) -> dict:
    artifact_ids = arguments.get("artifact_ids")
    include_inline_text = bool(arguments.get("include_inline_text"))
    if not isinstance(artifact_ids, list) or not artifact_ids:
        return _result({"error": "artifact_ids is required"}, is_error=True)
    items: list[dict] = []
    for artifact_id in artifact_ids:
        target = artifact_dir(str(artifact_id))
        manifest = _read_manifest(target)
        if not manifest:
            items.append({"artifact_id": artifact_id, "found": False})
            continue
        content_path = Path(manifest["content_path"])
        item = {
            "artifact_id": manifest["artifact_id"],
            "found": True,
            "filename": manifest["filename"],
            "kind": manifest["kind"],
            "deleted": manifest.get("deleted", False),
            "content_path": str(content_path),
        }
        if include_inline_text and manifest.get("kind") == "text" and content_path.exists():
            item["inline_text"] = content_path.read_text(encoding=manifest.get("encoding", "utf-8"))
        items.append(item)
    return _result({"artifacts": items})


def _delete_artifacts(arguments: dict) -> dict:
    artifact_ids = arguments.get("artifact_ids")
    mode = str(arguments.get("mode") or "soft")
    if not isinstance(artifact_ids, list) or not artifact_ids:
        return _result({"error": "artifact_ids is required"}, is_error=True)
    deleted: list[str] = []
    missing: list[str] = []
    for artifact_id in artifact_ids:
        target = artifact_dir(str(artifact_id))
        if not target.exists():
            missing.append(str(artifact_id))
            continue
        if mode == "hard":
            shutil.rmtree(target, ignore_errors=True)
            deleted.append(str(artifact_id))
            continue
        manifest = _read_manifest(target)
        manifest["deleted"] = True
        manifest["deleted_at"] = _now()
        _write_manifest(target, manifest)
        deleted.append(str(artifact_id))
    return _result({"mode": mode, "deleted_artifact_ids": deleted, "missing_artifact_ids": missing})


def execute_global_tool(name: str, arguments: dict) -> dict:
    if name == "global_upload_audio_files":
        return _store_audio_batch(arguments)
    if name == "global_upload_text_files":
        return _store_text_batch(arguments)
    if name == "global_download_processed_artifacts":
        return _download_artifacts(arguments)
    if name == "global_delete_uploaded_artifacts":
        return _delete_artifacts(arguments)
    return _result({"error": f"Unknown global tool: {name}"}, is_error=True)
