from __future__ import annotations

import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _storage_root(context: dict) -> Path:
    base_root = Path(context.get("server_storage_root") or Path(__file__).resolve().parent / ".server-data").resolve()
    skill_id = context.get("skill_id", "unknown")
    version_id = context.get("skill_version_id", "unknown")
    root = base_root / "stream-transfer-skill" / f"skill-{skill_id}" / f"version-{version_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _stream_dir(context: dict, kind: str, stream_id: str) -> Path:
    return _storage_root(context) / kind / stream_id


def _manifest_path(stream_dir: Path) -> Path:
    return stream_dir / "manifest.json"


def _chunks_dir(stream_dir: Path) -> Path:
    return stream_dir / "chunks"


def _read_manifest(stream_dir: Path) -> dict:
    path = _manifest_path(stream_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(stream_dir: Path, payload: dict) -> None:
    stream_dir.mkdir(parents=True, exist_ok=True)
    _manifest_path(stream_dir).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _chunk_file(stream_dir: Path, chunk_index: int, suffix: str) -> Path:
    return _chunks_dir(stream_dir) / f"{chunk_index:08d}{suffix}"


def _sorted_chunk_files(stream_dir: Path) -> list[Path]:
    chunks = _chunks_dir(stream_dir)
    if not chunks.exists():
        return []
    return sorted(path for path in chunks.iterdir() if path.is_file())


def _binary_suffix(mime_type: str | None) -> str:
    mapping = {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/flac": ".flac",
        "audio/ogg": ".ogg",
        "audio/webm": ".webm",
    }
    if isinstance(mime_type, str) and mime_type in mapping:
        return mapping[mime_type]
    return ".bin"


def _write_binary_chunk(stream_dir: Path, chunk_index: int, chunk_base64: str) -> int:
    _chunks_dir(stream_dir).mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(chunk_base64.encode("utf-8"))
    _chunk_file(stream_dir, chunk_index, ".bin").write_bytes(data)
    return len(data)


def _assemble_binary(stream_dir: Path, suffix: str) -> Path:
    assembled_path = stream_dir / f"assembled{suffix}"
    with assembled_path.open("wb") as handle:
        for chunk in _sorted_chunk_files(stream_dir):
            handle.write(chunk.read_bytes())
    return assembled_path


def _write_text_chunk(stream_dir: Path, chunk_index: int, chunk_text: str) -> int:
    _chunks_dir(stream_dir).mkdir(parents=True, exist_ok=True)
    payload = chunk_text.encode("utf-8")
    _chunk_file(stream_dir, chunk_index, ".txt").write_text(chunk_text, encoding="utf-8")
    return len(payload)


def _assemble_text(stream_dir: Path, encoding: str) -> Path:
    assembled_path = stream_dir / "assembled.txt"
    text = "".join(chunk.read_text(encoding="utf-8") for chunk in _sorted_chunk_files(stream_dir))
    assembled_path.write_text(text, encoding=encoding or "utf-8")
    return assembled_path


def _json_content(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, indent=2),
            }
        ],
        "isError": is_error,
    }


def _handle_audio(context: dict) -> dict:
    arguments = context["arguments"]
    stream_id = str(arguments.get("stream_id") or "").strip()
    if not stream_id:
        raise ValueError("stream_id is required")
    chunk_index = arguments.get("chunk_index")
    if not isinstance(chunk_index, int) or chunk_index < 0:
        raise ValueError("chunk_index must be a non-negative integer")
    chunk_base64 = arguments.get("chunk_base64")
    if not isinstance(chunk_base64, str) or not chunk_base64:
        raise ValueError("chunk_base64 is required")

    mime_type = arguments.get("mime_type")
    finalize = bool(arguments.get("finalize"))
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {}
    stream_dir = _stream_dir(context, "audio", stream_id)
    bytes_written = _write_binary_chunk(stream_dir, chunk_index, chunk_base64)
    manifest = _read_manifest(stream_dir)
    manifest.update(
        {
            "kind": "audio",
            "stream_id": stream_id,
            "mime_type": mime_type or manifest.get("mime_type") or "application/octet-stream",
            "finalized": finalize,
            "updated_at": _now(),
            "metadata": metadata,
        }
    )
    chunk_files = _sorted_chunk_files(stream_dir)
    manifest["chunk_count"] = len(chunk_files)
    manifest["total_bytes"] = sum(path.stat().st_size for path in chunk_files)

    assembled_path = None
    if finalize:
        assembled_path = _assemble_binary(stream_dir, _binary_suffix(mime_type if isinstance(mime_type, str) else None))
        manifest["assembled_path"] = str(assembled_path)

    _write_manifest(stream_dir, manifest)
    return _json_content(
        {
            "tool": context["tool"],
            "stream_id": stream_id,
            "kind": "audio",
            "chunk_index": chunk_index,
            "bytes_written": bytes_written,
            "chunk_count": manifest["chunk_count"],
            "total_bytes": manifest["total_bytes"],
            "finalized": finalize,
            "stream_path": str(stream_dir),
            "assembled_path": str(assembled_path) if assembled_path else None,
        }
    )


def _handle_text(context: dict) -> dict:
    arguments = context["arguments"]
    stream_id = str(arguments.get("stream_id") or "").strip()
    if not stream_id:
        raise ValueError("stream_id is required")
    chunk_index = arguments.get("chunk_index")
    if not isinstance(chunk_index, int) or chunk_index < 0:
        raise ValueError("chunk_index must be a non-negative integer")
    chunk_text = arguments.get("chunk_text")
    if not isinstance(chunk_text, str):
        raise ValueError("chunk_text is required")

    encoding = str(arguments.get("encoding") or "utf-8")
    finalize = bool(arguments.get("finalize"))
    metadata = arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {}
    stream_dir = _stream_dir(context, "text", stream_id)
    bytes_written = _write_text_chunk(stream_dir, chunk_index, chunk_text)
    manifest = _read_manifest(stream_dir)
    manifest.update(
        {
            "kind": "text",
            "stream_id": stream_id,
            "encoding": encoding,
            "finalized": finalize,
            "updated_at": _now(),
            "metadata": metadata,
        }
    )
    chunk_files = _sorted_chunk_files(stream_dir)
    manifest["chunk_count"] = len(chunk_files)
    manifest["total_bytes"] = sum(len(path.read_text(encoding="utf-8").encode("utf-8")) for path in chunk_files)

    assembled_path = _assemble_text(stream_dir, encoding)
    manifest["assembled_path"] = str(assembled_path)
    _write_manifest(stream_dir, manifest)
    return _json_content(
        {
            "tool": context["tool"],
            "stream_id": stream_id,
            "kind": "text",
            "chunk_index": chunk_index,
            "bytes_written": bytes_written,
            "chunk_count": manifest["chunk_count"],
            "total_bytes": manifest["total_bytes"],
            "finalized": finalize,
            "stream_path": str(stream_dir),
            "assembled_path": str(assembled_path),
        }
    )
def _candidate_delete_paths(context: dict, kind: str, stream_id: str) -> list[Path]:
    root = _storage_root(context)
    if kind == "all":
        return [root / "audio" / stream_id, root / "text" / stream_id]
    return [root / kind / stream_id]


def _delete_stream_path(path: Path) -> bool:
    if not path.exists():
        return False
    manifest = _read_manifest(path)
    manifest["deleted"] = True
    manifest["deleted_at"] = _now()
    manifest["deleted_marker"] = str(path / f".deleted-{uuid4().hex}.json")
    _write_manifest(path, manifest)
    return True


def _handle_delete(context: dict) -> dict:
    arguments = context["arguments"]
    mode = str(arguments.get("mode") or "").strip()
    kind = str(arguments.get("kind") or "all").strip()
    if mode not in {"stream", "batch"}:
        raise ValueError("mode must be 'stream' or 'batch'")
    if kind not in {"audio", "text", "all"}:
        raise ValueError("kind must be 'audio', 'text', or 'all'")

    if mode == "stream":
        stream_id = str(arguments.get("stream_id") or "").strip()
        if not stream_id:
            raise ValueError("stream_id is required when mode=stream")
        stream_ids = [stream_id]
    else:
        raw_ids = arguments.get("stream_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            raise ValueError("stream_ids is required when mode=batch")
        stream_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        if not stream_ids:
            raise ValueError("stream_ids must contain at least one non-empty stream id")

    deleted: list[str] = []
    missing: list[str] = []
    for stream_id in stream_ids:
        removed_any = False
        for candidate in _candidate_delete_paths(context, kind, stream_id):
            removed_any = _delete_stream_path(candidate) or removed_any
        if removed_any:
            deleted.append(stream_id)
        else:
            missing.append(stream_id)

    return _json_content(
        {
            "tool": context["tool"],
            "mode": mode,
            "kind": kind,
            "deleted_stream_ids": deleted,
            "missing_stream_ids": missing,
            "deleted_count": len(deleted),
        }
    )


def handle_tool(context: dict) -> dict:
    try:
        if context["tool"] == "stream_audio_to_server":
            return _handle_audio(context)
        if context["tool"] == "stream_text_to_server":
            return _handle_text(context)
        if context["tool"] == "delete_server_streams":
            return _handle_delete(context)
        return _json_content({"error": f"Unsupported tool: {context['tool']}"}, is_error=True)
    except Exception as exc:  # pragma: no cover - defensive path for runtime use
        return _json_content({"error": str(exc), "tool": context.get("tool")}, is_error=True)
