import json
import shutil
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

from app.services.skill_packages import extract_package_archive


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_extract_package_archive_builds_exec_repo_manifest() -> None:
    tmp_root = _make_temp_dir()
    source_root = tmp_root / "source"
    plugin_dir = source_root / "skills" / "demo-skill"
    try:
        _write_json(
            source_root / ".codex" / "skills-index.json",
            {
                "name": "Demo skill repo",
                "description": "Repository with executable skills",
                "skills": [
                    {
                        "name": "demo-skill",
                        "source": "../skills/demo-skill",
                        "description": "Run the demo script",
                    }
                ],
            },
        )
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "SKILL.md").write_text(
            "---\n"
            'description: "Demo skill description"\n'
            "---\n"
            "# Demo\n",
            encoding="utf-8",
        )
        (plugin_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "scripts" / "main.py").write_text(
            "from argparse import ArgumentParser\n"
            "parser = ArgumentParser()\n"
            "parser.add_argument('--json', action='store_true')\n"
            "parser.add_argument('--path')\n",
            encoding="utf-8",
        )

        archive_path = tmp_root / "demo.zip"
        with ZipFile(archive_path, "w") as archive:
            for path in source_root.rglob("*"):
                archive.write(path, path.relative_to(source_root))

        extracted = extract_package_archive(archive_path, tmp_root / "unzipped")

        assert extracted["kind"] == "skill_repo_exec"
        manifest = extracted["manifest"]
        assert manifest["name"] == "Demo skill repo"
        assert manifest["handler"]["type"] == "skill_repo_exec"
        tool = manifest["tools"][0]
        assert tool["name"] == "demo-skill"
        assert tool["inputSchema"]["properties"]["target_path"]["type"] == "string"
        plugin = manifest["handler"]["plugins"]["demo-skill"]
        assert plugin["default_script"] == "main.py"
        assert plugin["scripts"][0]["supports_json_output"] is True
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_extract_package_archive_builds_marketplace_repo_manifest() -> None:
    tmp_root = _make_temp_dir()
    source_root = tmp_root / "source"
    plugin_dir = source_root / "skills" / "docs-only"
    try:
        _write_json(
            source_root / "marketplace.json",
            {
                "name": "Docs Repo",
                "description": "Marketplace skill collection",
                "plugins": [
                    {
                        "name": "docs-only",
                        "source": "./skills/docs-only",
                        "description": "Read docs only",
                    }
                ],
            },
        )
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "SKILL.md").write_text("# Docs Only\n", encoding="utf-8")

        archive_path = tmp_root / "docs.zip"
        with ZipFile(archive_path, "w") as archive:
            for path in source_root.rglob("*"):
                archive.write(path, path.relative_to(source_root))

        extracted = extract_package_archive(archive_path, tmp_root / "unzipped")

        assert extracted["kind"] == "marketplace_repo"
        manifest = extracted["manifest"]
        assert manifest["name"] == "Docs Repo"
        assert manifest["handler"]["type"] == "marketplace_repo"
        tool = manifest["tools"][0]
        assert tool["name"] == "docs-only"
        assert tool["inputSchema"]["properties"] == {}
        plugin = manifest["handler"]["plugins"]["docs-only"]
        assert plugin["doc_only"] is True
        assert plugin["default_mode"] == "docs_only"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_extract_package_archive_detects_shell_scripts_in_marketplace_repo() -> None:
    tmp_root = _make_temp_dir()
    source_root = tmp_root / "source"
    plugin_dir = source_root / "skills" / "health"
    try:
        _write_json(
            source_root / "marketplace.json",
            {
                "name": "Ops Marketplace",
                "plugins": [
                    {
                        "name": "health",
                        "source": "./skills/health",
                        "description": "Run health diagnostics",
                    }
                ],
            },
        )
        (plugin_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "SKILL.md").write_text("# Health\n", encoding="utf-8")
        (plugin_dir / "scripts" / "collect-data.sh").write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")

        archive_path = tmp_root / "health.zip"
        with ZipFile(archive_path, "w") as archive:
            for path in source_root.rglob("*"):
                archive.write(path, path.relative_to(source_root))

        extracted = extract_package_archive(archive_path, tmp_root / "unzipped")
        plugin = extracted["manifest"]["handler"]["plugins"]["health"]
        assert plugin["doc_only"] is False
        assert plugin["default_script"] == "collect-data.sh"
        assert plugin["default_mode"] == "no_args"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_extract_package_archive_rejects_prefix_traversal_paths() -> None:
    tmp_root = _make_temp_dir()
    archive_path = tmp_root / "unsafe.zip"
    target_dir = tmp_root / "extract"
    try:
        with ZipFile(archive_path, "w") as archive:
            archive.writestr("../extract-evil/skill.json", "{}")

        with pytest.raises(HTTPException) as exc_info:
            extract_package_archive(archive_path, target_dir)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Archive contains unsafe paths"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
