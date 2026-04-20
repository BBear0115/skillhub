import json
import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4
from zipfile import ZipFile

from fastapi import HTTPException, UploadFile, status

from app.config import settings


MANIFEST_NAMES = ("skill.json", "skillhub.json")
SESSION_STORAGE_NAMESPACE = uuid4().hex


def _handle_remove_readonly(func, path, exc_info):
    os.chmod(path, 0o700)
    func(path)


def remove_tree(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target, onerror=_handle_remove_readonly)


def ensure_storage_root() -> Path:
    root = Path(settings.storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def skill_storage_dir(skill_id: int) -> Path:
    return ensure_storage_root() / "skills" / f"{SESSION_STORAGE_NAMESPACE}-{skill_id}"


async def save_upload_to_disk(upload: UploadFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.file.seek(0)
    with target.open("wb") as file_obj:
        shutil.copyfileobj(upload.file, file_obj)


def _safe_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_archive_root(extracted_dir: Path) -> Path:
    direct_manifests = any((extracted_dir / name).exists() for name in MANIFEST_NAMES)
    if direct_manifests or (extracted_dir / "marketplace.json").exists():
        return extracted_dir

    children = [child for child in extracted_dir.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        child = children[0]
        if any((child / name).exists() for name in MANIFEST_NAMES) or (child / "marketplace.json").exists():
            return child
    return extracted_dir


def _build_repo_import(root_dir: Path, marketplace_path: Path) -> dict[str, Any]:
    marketplace = _safe_json(marketplace_path)
    plugins = marketplace.get("plugins") or []
    if not plugins:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="marketplace.json contains no plugins")

    tool_definitions = []
    plugin_map: dict[str, dict[str, Any]] = {}
    for plugin in plugins:
        name = plugin.get("name")
        source = plugin.get("source")
        if not name or not source:
            continue
        plugin_dir = (root_dir / source).resolve()
        if not str(plugin_dir).startswith(str(root_dir.resolve())):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Plugin source points outside archive")
        skill_doc = plugin_dir / "SKILL.md"
        if not skill_doc.exists():
            continue
        plugin_map[name] = {
            "name": name,
            "description": plugin.get("description") or f"Browse and inspect the {name} skill",
            "source": str(plugin_dir),
            "skill_doc": str(skill_doc),
            "homepage": plugin.get("homepage"),
            "version": plugin.get("version"),
            "category": plugin.get("category"),
        }
        tool_definitions.append(
            {
                "name": name,
                "description": plugin_map[name]["description"],
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["summary", "full"],
                            "description": "Return either a short summary or the full SKILL.md content",
                        }
                    },
                },
            }
        )

    if not tool_definitions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No importable skills were found in the repository")

    return {
        "kind": "skill_repo",
        "manifest": {
            "name": marketplace.get("name") or root_dir.name,
            "description": marketplace.get("description") or f"Imported skill repository from {root_dir.name}",
            "visibility": "private",
            "handler": {
                "type": "skill_repo",
                "root_dir": str(root_dir),
                "plugins": plugin_map,
            },
            "tools": tool_definitions,
        },
    }


def _build_single_skill_import(root_dir: Path) -> dict[str, Any]:
    for manifest_name in MANIFEST_NAMES:
        manifest_path = root_dir / manifest_name
        if manifest_path.exists():
            return {
                "kind": "single_skill",
                "manifest": _safe_json(manifest_path),
                "root_dir": root_dir,
            }
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Uploaded ZIP must contain skill.json/skillhub.json, or marketplace.json for a skill repository",
    )


def extract_package_archive(archive_path: Path, target_dir: Path) -> dict[str, Any]:
    if target_dir.exists():
        remove_tree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            if not str(member_path).startswith(str(target_dir.resolve())):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Archive contains unsafe paths",
                )
        archive.extractall(target_dir)

    root_dir = _resolve_archive_root(target_dir)
    marketplace_path = root_dir / "marketplace.json"
    if marketplace_path.exists():
        return _build_repo_import(root_dir, marketplace_path)
    return _build_single_skill_import(root_dir)


def cleanup_skill_storage(skill_id: int) -> None:
    skill_dir = skill_storage_dir(skill_id)
    remove_tree(skill_dir)


def clone_skill_storage(source_skill_id: int, target_skill_id: int) -> dict[str, str]:
    source_dir = skill_storage_dir(source_skill_id)
    target_dir = skill_storage_dir(target_skill_id)
    if not source_dir.exists():
        return {}

    if target_dir.exists():
        remove_tree(target_dir)
    shutil.copytree(source_dir, target_dir)

    source_package = source_dir / "package"
    target_package = target_dir / "package"
    rewrites: dict[str, str] = {}
    if source_package.exists() and target_package.exists():
        rewrites[str(source_package)] = str(target_package)
    return rewrites
