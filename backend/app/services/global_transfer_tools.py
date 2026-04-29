import base64
import io
import json
import os
import shutil
import stat
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from app.config import settings

STREAM_TOOL_MAX_ITEMS = 1
BULK_CLEANUP_MAX_ITEMS = 100


GLOBAL_TOOL_DEFINITIONS = [
    {
        "name": "global_upload_audio_files",
        "description": "Stream-upload exactly one audio file into server-side artifact storage. Call this tool once per file instead of uploading a full batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": STREAM_TOOL_MAX_ITEMS,
                    "description": "Exactly one file per call. For many local files, call this tool repeatedly.",
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
        "description": "Stream-upload exactly one text file into server-side artifact storage. Call this tool once per file instead of uploading a full batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "batch_id": {"type": "string"},
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": STREAM_TOOL_MAX_ITEMS,
                    "description": "Exactly one file per call. For many local files, call this tool repeatedly.",
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
        "description": "Stream-output metadata and download handles for exactly one processed or uploaded artifact. Call repeatedly for multiple outputs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": STREAM_TOOL_MAX_ITEMS,
                    "description": "Exactly one artifact id per call.",
                },
                "include_inline_text": {"type": "boolean"},
            },
            "required": ["artifact_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "global_delete_uploaded_artifacts",
        "description": "Stream-delete exactly one uploaded or processed artifact. Call repeatedly after each file is downloaded or no longer needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": STREAM_TOOL_MAX_ITEMS,
                    "description": "Exactly one artifact id per call.",
                },
                "mode": {"type": "string", "enum": ["soft", "hard"]},
            },
            "required": ["artifact_ids"],
            "additionalProperties": False,
        },
    },
    {
        "name": "global_download_processed_artifacts_and_cleanup",
        "description": "Download one or more processed artifacts as a zip/base64 payload, then delete those artifacts from server storage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": BULK_CLEANUP_MAX_ITEMS,
                    "description": "One or more processed artifact ids to zip and delete together.",
                },
                "cleanup_mode": {"type": "string", "enum": ["soft", "hard"], "default": "hard"},
                "archive_name": {"type": "string"},
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
    root = artifacts_root().resolve()
    raw = str(artifact_id).strip()
    if not raw:
        raise ValueError("artifact_id is required")
    candidate = (root / raw).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("artifact_id is outside artifact storage")
    return candidate


def _safe_artifact_id(value: str) -> str:
    raw = str(value).strip()
    if raw.endswith("_dnsmos"):
        UUID(raw[:-7])
        return raw
    UUID(raw)
    return raw


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


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def cleanup_audio_artifacts(*, older_than_hours: float = 0, mode: str = "hard") -> dict:
    if mode not in {"soft", "hard"}:
        raise ValueError("mode must be soft or hard")
    cutoff = datetime.now(timezone.utc).timestamp() - max(0, older_than_hours) * 3600
    scanned = 0
    candidates: list[str] = []
    for target in artifacts_root().iterdir():
        if not target.is_dir():
            continue
        try:
            artifact_id = _safe_artifact_id(target.name)
        except ValueError:
            continue
        manifest = _read_manifest(target)
        if not manifest or manifest.get("deleted"):
            continue
        scanned += 1
        created_at = _parse_datetime(str(manifest.get("created_at") or ""))
        if created_at is not None and created_at.timestamp() > cutoff:
            continue
        metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
        kind = str(manifest.get("kind") or "")
        filename = str(manifest.get("filename") or "").lower()
        should_cleanup = (
            kind in {"audio", "archive"}
            or bool(metadata.get("contains_processed_outputs"))
            or filename.endswith((".wav", ".mp3", ".flac", ".m4a", ".ogg", ".zip"))
        )
        if should_cleanup:
            candidates.append(artifact_id)

    deleted: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for artifact_id in candidates:
        result = json.loads(_delete_artifacts_unbounded([artifact_id], mode=mode)["content"][0]["text"])
        deleted.extend(result.get("deleted_artifact_ids", []))
        missing.extend(result.get("missing_artifact_ids", []))
        failed.extend(result.get("failed_artifact_ids", []))
    return {
        "mode": mode,
        "older_than_hours": older_than_hours,
        "scanned_artifacts": scanned,
        "candidate_artifact_ids": candidates,
        "deleted_artifact_ids": deleted,
        "missing_artifact_ids": missing,
        "failed_artifact_ids": failed,
    }


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

    resolved_artifact_id = _safe_artifact_id(str(artifact_id)) if artifact_id else uuid4().hex
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
        "download_url": f"/artifacts/{resolved_artifact_id}/download",
    }


def delete_artifact(artifact_id: str, *, mode: str = "soft") -> dict:
    result = _delete_artifacts({"artifact_ids": [artifact_id], "mode": mode})
    return json.loads(result["content"][0]["text"])


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
    if len(files) > STREAM_TOOL_MAX_ITEMS:
        return _result(
            {
                "error": "streaming mode accepts exactly one file per call",
                "received_count": len(files),
                "max_items": STREAM_TOOL_MAX_ITEMS,
                "retry": "Call this tool once per file, then process/download/delete that file before moving to the next one.",
            },
            is_error=True,
        )
    batch_id = str(arguments.get("batch_id") or uuid4().hex)
    saved: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        try:
            artifact_id = _safe_artifact_id(str(item.get("artifact_id"))) if item.get("artifact_id") else uuid4().hex
        except ValueError:
            continue
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
    if len(files) > STREAM_TOOL_MAX_ITEMS:
        return _result(
            {
                "error": "streaming mode accepts exactly one file per call",
                "received_count": len(files),
                "max_items": STREAM_TOOL_MAX_ITEMS,
                "retry": "Call this tool once per file, then process/download/delete that file before moving to the next one.",
            },
            is_error=True,
        )
    batch_id = str(arguments.get("batch_id") or uuid4().hex)
    saved: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        try:
            artifact_id = _safe_artifact_id(str(item.get("artifact_id"))) if item.get("artifact_id") else uuid4().hex
        except ValueError:
            continue
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
    if len(artifact_ids) > STREAM_TOOL_MAX_ITEMS:
        return _result(
            {
                "error": "streaming mode accepts exactly one artifact id per call",
                "received_count": len(artifact_ids),
                "max_items": STREAM_TOOL_MAX_ITEMS,
                "retry": "Call this tool repeatedly, once for each processed artifact.",
            },
            is_error=True,
        )
    items: list[dict] = []
    for artifact_id in artifact_ids:
        target = artifact_dir(str(artifact_id))
        manifest = _read_manifest(target)
        if not manifest or manifest.get("deleted"):
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
            "download_url": f"/artifacts/{manifest['artifact_id']}/download",
        }
        if include_inline_text and manifest.get("kind") == "text" and content_path.exists():
            item["inline_text"] = content_path.read_text(encoding=manifest.get("encoding", "utf-8"))
        items.append(item)
    return _result({"artifacts": items})


def _zip_member_name(manifest: dict, fallback_artifact_id: str) -> str:
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else {}
    relative_path = metadata.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        cleaned = relative_path.replace("\\", "/").lstrip("/")
    else:
        cleaned = str(manifest.get("filename") or f"{fallback_artifact_id}.bin")
    cleaned = "/".join(part for part in cleaned.split("/") if part not in {"", ".", ".."})
    return cleaned or str(manifest.get("filename") or f"{fallback_artifact_id}.bin")


def _download_and_cleanup_artifacts(arguments: dict) -> dict:
    artifact_ids = arguments.get("artifact_ids")
    cleanup_mode = str(arguments.get("cleanup_mode") or "hard")
    archive_name = str(arguments.get("archive_name") or f"processed-artifacts-{uuid4().hex}.zip")
    if not archive_name.lower().endswith(".zip"):
        archive_name = f"{archive_name}.zip"
    if cleanup_mode not in {"soft", "hard"}:
        return _result({"error": "cleanup_mode must be soft or hard"}, is_error=True)
    if not isinstance(artifact_ids, list) or not artifact_ids:
        return _result({"error": "artifact_ids is required"}, is_error=True)
    if len(artifact_ids) > BULK_CLEANUP_MAX_ITEMS:
        return _result(
            {
                "error": "cleanup accepts too many artifact ids in one call",
                "received_count": len(artifact_ids),
                "max_items": BULK_CLEANUP_MAX_ITEMS,
                "retry": "Call this tool with a smaller group of processed artifacts.",
            },
            is_error=True,
        )

    resolved: list[tuple[str, dict, Path]] = []
    missing: list[str] = []
    for artifact_id in [str(item) for item in artifact_ids]:
        try:
            target = artifact_dir(artifact_id)
        except ValueError:
            missing.append(artifact_id)
            continue
        manifest = _read_manifest(target)
        if not manifest or manifest.get("deleted"):
            missing.append(artifact_id)
            continue
        content_path = Path(str(manifest.get("content_path") or ""))
        if not content_path.exists() or not content_path.is_file():
            missing.append(artifact_id)
            continue
        resolved.append((artifact_id, manifest, content_path))

    if missing:
        return _result({"error": "Some artifacts are missing or deleted", "missing_artifact_ids": missing}, is_error=True)

    buffer = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact_id, manifest, content_path in resolved:
            member_name = _zip_member_name(manifest, artifact_id)
            if member_name in used_names:
                stem = Path(member_name).stem
                suffix = Path(member_name).suffix
                member_name = f"{stem}-{artifact_id}{suffix}"
            used_names.add(member_name)
            archive.write(content_path, member_name)

    delete_result = _delete_artifacts_unbounded([artifact_id for artifact_id, _manifest, _path in resolved], mode=cleanup_mode)
    delete_payload = json.loads(delete_result["content"][0]["text"])
    return _result(
        {
            "filename": archive_name,
            "mime_type": "application/zip",
            "content_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "artifact_count": len(resolved),
            "artifacts": [
                {
                    "artifact_id": manifest["artifact_id"],
                    "filename": manifest["filename"],
                    "kind": manifest["kind"],
                    "archive_path": _zip_member_name(manifest, artifact_id),
                }
                for artifact_id, manifest, _path in resolved
            ],
            "cleanup_mode": cleanup_mode,
            "deleted_artifact_ids": delete_payload.get("deleted_artifact_ids", []),
            "missing_artifact_ids": delete_payload.get("missing_artifact_ids", []),
            "failed_artifact_ids": delete_payload.get("failed_artifact_ids", []),
        },
        is_error=bool(delete_payload.get("failed_artifact_ids")),
    )


def _delete_artifacts(arguments: dict) -> dict:
    artifact_ids = arguments.get("artifact_ids")
    mode = str(arguments.get("mode") or "soft")
    if not isinstance(artifact_ids, list) or not artifact_ids:
        return _result({"error": "artifact_ids is required"}, is_error=True)
    if len(artifact_ids) > STREAM_TOOL_MAX_ITEMS:
        return _result(
            {
                "error": "streaming mode accepts exactly one artifact id per call",
                "received_count": len(artifact_ids),
                "max_items": STREAM_TOOL_MAX_ITEMS,
                "retry": "Call this tool repeatedly, once for each artifact to delete.",
            },
            is_error=True,
        )
    return _delete_artifacts_unbounded([str(item) for item in artifact_ids], mode=mode)


def _delete_artifacts_unbounded(artifact_ids: list[str], *, mode: str = "soft") -> dict:
    deleted: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for artifact_id in artifact_ids:
        try:
            safe_id = _safe_artifact_id(str(artifact_id))
            target = artifact_dir(safe_id)
        except ValueError:
            failed.append(str(artifact_id))
            continue
        if not target.exists():
            missing.append(safe_id)
            continue
        if mode == "hard":
            _remove_tree_hard(target)
            if target.exists():
                if _mark_artifact_deleted(target):
                    deleted.append(safe_id)
                else:
                    failed.append(safe_id)
            else:
                deleted.append(safe_id)
            continue
        if _mark_artifact_deleted(target):
            deleted.append(safe_id)
        else:
            failed.append(safe_id)
    return _result({"mode": mode, "deleted_artifact_ids": deleted, "missing_artifact_ids": missing, "failed_artifact_ids": failed}, is_error=bool(failed))


def _mark_artifact_deleted(target: Path) -> bool:
    try:
        manifest = _read_manifest(target)
        manifest["deleted"] = True
        manifest["deleted_at"] = _now()
        _write_manifest(target, manifest)
        return True
    except OSError:
        return False


def _grant_windows_delete_access(target: Path) -> None:
    if os.name != "nt":
        return
    username = os.environ.get("USERNAME")
    if not username:
        return
    accounts = []
    domain = os.environ.get("USERDOMAIN")
    if domain:
        accounts.append(f"{domain}\\{username}:F")
    accounts.append(f"{username}:F")
    for account in accounts:
        try:
            subprocess.run(
                ["icacls", str(target), "/grant", account, "/T", "/C"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except (OSError, TypeError):
            continue


def _make_tree_writable(target: Path) -> None:
    if not target.exists():
        return
    _grant_windows_delete_access(target)
    paths = [target]
    if target.is_dir():
        for root, dirs, files in os.walk(target):
            paths.extend(Path(root) / name for name in dirs)
            paths.extend(Path(root) / name for name in files)
    for path in paths:
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        except OSError:
            pass


def _remove_tree_hard(target: Path) -> None:
    for _attempt in range(2):
        if not target.exists():
            return
        _make_tree_writable(target)
        try:
            shutil.rmtree(target, onexc=_handle_remove_readonly)
        except TypeError:
            try:
                shutil.rmtree(target, onerror=_handle_remove_readonly_legacy)
            except OSError:
                pass
        except OSError:
            pass


def _handle_remove_readonly(function, path, excinfo) -> None:
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
        function(path)
    except OSError:
        pass


def _handle_remove_readonly_legacy(function, path, excinfo) -> None:
    _handle_remove_readonly(function, path, excinfo)


def execute_global_tool(name: str, arguments: dict) -> dict:
    if name == "global_upload_audio_files":
        return _store_audio_batch(arguments)
    if name == "global_upload_text_files":
        return _store_text_batch(arguments)
    if name == "global_download_processed_artifacts":
        return _download_artifacts(arguments)
    if name == "global_delete_uploaded_artifacts":
        return _delete_artifacts(arguments)
    if name == "global_download_processed_artifacts_and_cleanup":
        return _download_and_cleanup_artifacts(arguments)
    return _result({"error": f"Unknown global tool: {name}"}, is_error=True)
