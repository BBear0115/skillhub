import base64
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings
from app.models.tool import Tool
from app.services.skill_runner import execute_tool


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _example_skill_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "server-transfer-skill"


def _tool(name: str) -> Tool:
    return Tool(id=1, skill_id=9, skill_version_id=3, name=name, description=None, input_schema={})


def _payload(result: dict) -> dict:
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_server_transfer_skill_supports_stream_and_batch_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_root = _make_temp_dir()
    monkeypatch.setattr(settings, "storage_root", str(tmp_root / "storage"))
    handler_config = {
        "type": "python_package",
        "package_dir": str(_example_skill_dir()),
        "entrypoint": "main.py:handle_tool",
    }

    try:
        audio_result = await execute_tool(
            handler_config,
            _tool("stream_audio_to_server"),
            {
                "stream_id": "audio-session",
                "chunk_index": 0,
                "chunk_base64": base64.b64encode(b"audio-bytes").decode("utf-8"),
                "mime_type": "audio/wav",
                "finalize": True,
            },
        )
        audio_payload = _payload(audio_result)
        assembled_audio = Path(audio_payload["assembled_path"])
        assert assembled_audio.exists()
        assert assembled_audio.read_bytes() == b"audio-bytes"

        first_text = await execute_tool(
            handler_config,
            _tool("stream_text_to_server"),
            {
                "stream_id": "text-session",
                "chunk_index": 0,
                "chunk_text": "hello ",
                "finalize": False,
            },
        )
        first_text_payload = _payload(first_text)
        assert first_text_payload["chunk_count"] == 1

        second_text = await execute_tool(
            handler_config,
            _tool("stream_text_to_server"),
            {
                "stream_id": "text-session",
                "chunk_index": 1,
                "chunk_text": "world",
                "finalize": True,
            },
        )
        second_text_payload = _payload(second_text)
        assembled_text = Path(second_text_payload["assembled_path"])
        assert assembled_text.exists()
        assert assembled_text.read_text(encoding="utf-8") == "hello world"

        delete_single = await execute_tool(
            handler_config,
            _tool("delete_server_streams"),
            {
                "mode": "stream",
                "kind": "audio",
                "stream_id": "audio-session",
            },
        )
        delete_single_payload = _payload(delete_single)
        assert delete_single_payload["deleted_stream_ids"] == ["audio-session"]
        audio_manifest = json.loads((assembled_audio.parent / "manifest.json").read_text(encoding="utf-8"))
        assert audio_manifest["deleted"] is True

        delete_batch = await execute_tool(
            handler_config,
            _tool("delete_server_streams"),
            {
                "mode": "batch",
                "kind": "all",
                "stream_ids": ["text-session", "missing-stream"],
            },
        )
        delete_batch_payload = _payload(delete_batch)
        assert delete_batch_payload["deleted_stream_ids"] == ["text-session"]
        assert delete_batch_payload["missing_stream_ids"] == ["missing-stream"]
        text_manifest = json.loads((assembled_text.parent / "manifest.json").read_text(encoding="utf-8"))
        assert text_manifest["deleted"] is True
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
