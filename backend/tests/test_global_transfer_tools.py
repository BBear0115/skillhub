import base64
import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.services.global_transfer_tools import execute_global_tool, store_existing_file_as_artifact


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _payload(result: dict) -> dict:
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def test_download_processed_artifacts_and_cleanup_zips_then_hard_deletes(monkeypatch):
    tmp_root = _make_temp_dir()
    monkeypatch.setattr(settings, "storage_root", str(tmp_root / "storage"))
    source = tmp_root / "dnsmos_scores.csv"
    source.write_text("file,OVRL\nsample.wav,3.1\n", encoding="utf-8")

    try:
        stored = store_existing_file_as_artifact(source, metadata={"relative_path": "reports/dnsmos_scores.csv"})
        result = execute_global_tool(
            "global_download_processed_artifacts_and_cleanup",
            {
                "artifact_ids": [stored["artifact_id"]],
                "archive_name": "results",
                "cleanup_mode": "hard",
            },
        )

        payload = _payload(result)
        assert payload["filename"] == "results.zip"
        assert payload["deleted_artifact_ids"] == [stored["artifact_id"]]
        archive_path = tmp_root / "results.zip"
        archive_path.write_bytes(base64.b64decode(payload["content_base64"]))
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("reports/dnsmos_scores.csv").decode("utf-8").replace("\r\n", "\n") == "file,OVRL\nsample.wav,3.1\n"

        assert execute_global_tool("global_download_processed_artifacts", {"artifact_ids": [stored["artifact_id"]]})["isError"] is False
        download_payload = _payload(execute_global_tool("global_download_processed_artifacts", {"artifact_ids": [stored["artifact_id"]]}))
        assert download_payload["artifacts"][0]["found"] is False
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_download_processed_artifacts_and_cleanup_does_not_delete_when_any_artifact_missing(monkeypatch):
    tmp_root = _make_temp_dir()
    monkeypatch.setattr(settings, "storage_root", str(tmp_root / "storage"))
    source = tmp_root / "keep.txt"
    source.write_text("keep me", encoding="utf-8")

    try:
        stored = store_existing_file_as_artifact(source, kind="text")
        result = execute_global_tool(
            "global_download_processed_artifacts_and_cleanup",
            {
                "artifact_ids": [stored["artifact_id"], "missing-artifact"],
                "cleanup_mode": "hard",
            },
        )

        assert result["isError"] is True
        error_payload = json.loads(result["content"][0]["text"])
        assert error_payload["missing_artifact_ids"] == ["missing-artifact"]
        download_payload = _payload(
            execute_global_tool(
                "global_download_processed_artifacts",
                {"artifact_ids": [stored["artifact_id"]], "include_inline_text": True},
            )
        )
        assert download_payload["artifacts"][0]["found"] is True
        assert download_payload["artifacts"][0]["inline_text"] == "keep me"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
