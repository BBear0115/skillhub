import json
import logging
import os
import re
import stat
import shutil
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from fastapi import HTTPException, UploadFile, status

from app.config import settings


MANIFEST_NAMES = ("skill.json", "skillhub.json")
logger = logging.getLogger(__name__)


def _handle_remove_readonly(func, path, exc_info):
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except OSError:
        logger.warning("Failed to remove path during cleanup: %s", path, exc_info=True)


def remove_tree(target: Path) -> None:
    if target.exists():
        logger.info("Removing tree: %s", target)
        try:
            shutil.rmtree(target, onerror=_handle_remove_readonly)
        except OSError:
            logger.warning("Failed to fully remove tree: %s", target, exc_info=True)


def ensure_storage_root() -> Path:
    root = Path(settings.storage_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def skills_root_dir() -> Path:
    root = ensure_storage_root() / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def skill_versions_root_dir() -> Path:
    root = ensure_storage_root() / "skill-versions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def review_workbenches_root_dir() -> Path:
    root = ensure_storage_root() / "review-workbenches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def deployed_skills_root_dir() -> Path:
    root = ensure_storage_root() / "deployed-skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def stable_skill_storage_dir(skill_id: int) -> Path:
    return skills_root_dir() / f"skill-{skill_id}"


def stable_skill_version_storage_dir(version_id: int) -> Path:
    return skill_versions_root_dir() / f"version-{version_id}"


def stable_review_workbench_dir(version_id: int) -> Path:
    return review_workbenches_root_dir() / f"version-{version_id}"


def stable_deployed_skill_dir(skill_id: int, version_id: int) -> Path:
    return deployed_skills_root_dir() / f"skill-{skill_id}" / f"version-{version_id}"


def _find_legacy_skill_storage_dir(skill_id: int) -> Path | None:
    matches = [path for path in skills_root_dir().glob(f"*-{skill_id}") if path.is_dir()]
    if not matches:
        return None
    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0]


def skill_storage_dir(skill_id: int) -> Path:
    stable_dir = stable_skill_storage_dir(skill_id)
    if stable_dir.exists():
        return stable_dir
    legacy_dir = _find_legacy_skill_storage_dir(skill_id)
    if legacy_dir is not None:
        return legacy_dir
    return stable_dir


def skill_version_storage_dir(version_id: int) -> Path:
    return stable_skill_version_storage_dir(version_id)


def review_workbench_dir(version_id: int) -> Path:
    return stable_review_workbench_dir(version_id)


def deployed_skill_dir(skill_id: int, version_id: int) -> Path:
    return stable_deployed_skill_dir(skill_id, version_id)


async def save_upload_to_disk(upload: UploadFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.file.seek(0)
    logger.info("Saving upload %s to %s", upload.filename, target)
    with target.open("wb") as file_obj:
        shutil.copyfileobj(upload.file, file_obj)


def _safe_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _find_marketplace_manifest(root_dir: Path) -> Path | None:
    direct_path = root_dir / "marketplace.json"
    if direct_path.exists():
        return direct_path

    hidden_metadata_paths = [
        root_dir / ".claude-plugin" / "marketplace.json",
        root_dir / ".codex-plugin" / "marketplace.json",
        root_dir / ".cursor-plugin" / "marketplace.json",
    ]
    for candidate in hidden_metadata_paths:
        if candidate.exists():
            return candidate

    recursive_matches = [
        path for path in root_dir.rglob("marketplace.json") if "__MACOSX" not in path.parts
    ]
    if not recursive_matches:
        return None

    recursive_matches.sort(key=lambda path: (len(path.relative_to(root_dir).parts), str(path)))
    return recursive_matches[0]


def _find_skills_index(root_dir: Path) -> Path | None:
    candidates = [
        root_dir / ".codex" / "skills-index.json",
        root_dir / "skills-index.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    recursive_matches = [
        path for path in root_dir.rglob("skills-index.json") if "__MACOSX" not in path.parts
    ]
    if not recursive_matches:
        return None
    recursive_matches.sort(key=lambda path: (len(path.relative_to(root_dir).parts), str(path)))
    return recursive_matches[0]


def _has_direct_package_metadata(root_dir: Path) -> bool:
    return any((root_dir / name).exists() for name in MANIFEST_NAMES) or (root_dir / "marketplace.json").exists()


def _has_implicit_skill_layout(root_dir: Path) -> bool:
    return (root_dir / "SKILL.md").exists()


def _resolve_archive_root(extracted_dir: Path) -> Path:
    children = [child for child in extracted_dir.iterdir() if child.name != "__MACOSX"]
    if len(children) == 1 and children[0].is_dir():
        child = children[0]
        if _has_direct_package_metadata(child) or _find_marketplace_manifest(child) or _has_implicit_skill_layout(child):
            return child

    if _has_direct_package_metadata(extracted_dir) or _find_marketplace_manifest(extracted_dir) or _has_implicit_skill_layout(extracted_dir):
        return extracted_dir
    return extracted_dir


def _read_frontmatter_value(skill_doc: Path, key: str) -> str | None:
    try:
        text = skill_doc.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    for line in lines[1:40]:
        if line.strip() == "---":
            break
        if line.lower().startswith(f"{key.lower()}:"):
            return line.split(":", 1)[1].strip().strip('"')
    return None


def _read_skill_doc_title(skill_doc: Path) -> str | None:
    try:
        text = skill_doc.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def _read_frontmatter_description(skill_doc: Path) -> str | None:
    return _read_frontmatter_value(skill_doc, "description")


def _read_frontmatter_name(skill_doc: Path) -> str | None:
    return _read_frontmatter_value(skill_doc, "name")


def _safe_relative(path: Path, root_dir: Path) -> str:
    return str(path.resolve().relative_to(root_dir.resolve())).replace("\\", "/")


def _is_relative_to(path: Path, root_dir: Path) -> bool:
    try:
        path.resolve().relative_to(root_dir.resolve())
    except ValueError:
        return False
    return True


def _inspect_script(script_path: Path) -> dict[str, Any]:
    if script_path.suffix == ".sh":
        text = script_path.read_text(encoding="utf-8", errors="ignore")
        input_mode = "no_args"
        if "${1:" in text or "$1" in text:
            input_mode = "target_path"
        return {
            "name": script_path.name,
            "relative_path": script_path.name if script_path.parent.name == "scripts" else str(script_path.name),
            "positionals": ["target_path"] if input_mode == "target_path" else [],
            "optionals": [],
            "supports_sample_data": False,
            "supports_json_output": False,
            "default_mode": input_mode,
        }

    try:
        text = script_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = script_path.read_text(encoding="utf-8-sig")

    add_argument_re = re.compile(r"""add_argument\(\s*(['"])([^'"]+)\1""")
    positionals: list[str] = []
    optionals: set[str] = set()
    for match in add_argument_re.finditer(text):
        arg_name = match.group(2)
        if arg_name.startswith("--"):
            optionals.add(arg_name)
        elif arg_name.startswith("-"):
            continue
        else:
            positionals.append(arg_name)

    supports_sample_data = any(
        token in text
        for token in (
            "SAMPLE_DATA",
            "SAMPLE_INPUT",
            "No input file specified",
            "No file provided",
            "running with sample data",
            "Running sample data",
            "--sample",
        )
    )

    input_mode = "none"
    if positionals:
        first = positionals[0].lower()
        if any(token in first for token in ("path", "project", "dir", "directory", "root")):
            input_mode = "target_path"
        elif any(token in first for token in ("file", "input", "data")):
            input_mode = "json_input"
    elif any(flag in optionals for flag in ("--path", "--project", "--project-dir", "--dir", "--directory", "--root")):
        input_mode = "target_path"
    elif any(flag in optionals for flag in ("--input-file", "--input", "--file", "--data-file", "--data", "--json-file")):
        input_mode = "json_input"

    if supports_sample_data:
        default_mode = "sample"
    elif input_mode == "target_path":
        default_mode = "target_path"
    elif input_mode == "json_input":
        default_mode = "json_input"
    else:
        default_mode = "no_args"

    supports_json_output = "--json" in optionals or "--format" in optionals

    return {
        "name": script_path.name,
        "relative_path": script_path.name if script_path.parent.name == "scripts" else str(script_path.name),
        "positionals": positionals,
        "optionals": sorted(optionals),
        "supports_sample_data": supports_sample_data,
        "supports_json_output": supports_json_output,
        "default_mode": default_mode,
    }


def _choose_default_script(scripts: list[dict[str, Any]]) -> dict[str, Any]:
    preferred_scripts = [script for script in scripts if str(script["name"]).endswith(".py")]
    if not preferred_scripts:
        preferred_scripts = scripts
    for preferred_mode in ("sample", "target_path", "json_input", "no_args"):
        for script in preferred_scripts:
            if script["default_mode"] == preferred_mode:
                return script
    return preferred_scripts[0]


def _resolve_index_source(root_dir: Path, skills_index_path: Path, source: str) -> Path:
    index_root = skills_index_path.parent.resolve()
    root_resolved = root_dir.resolve()

    direct = (index_root / source).resolve()
    if _is_relative_to(direct, root_resolved):
        return direct

    normalized_parts = [part for part in Path(source).parts if part not in ("..", ".")]
    fallback = root_resolved.joinpath(*normalized_parts).resolve()
    return fallback


def _build_exec_input_schema(plugin: dict[str, Any], default_script: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "script": {
            "type": "string",
            "description": "Optional script selector when a skill ships multiple Python scripts.",
            "enum": [script["name"] for script in plugin["scripts"]],
        },
        "output_format": {
            "type": "string",
            "enum": ["auto", "json", "text"],
            "description": "Preferred output format. Auto will choose JSON when the script supports it.",
        },
        "options": {
            "type": "object",
            "description": "Optional CLI flag values mapped to the selected script's long options.",
            "additionalProperties": True,
        },
    }

    if default_script["default_mode"] == "target_path":
        properties["target_path"] = {
            "type": "string",
            "description": "Workspace-local file or directory path to analyze.",
        }
    if default_script["default_mode"] == "json_input":
        properties["input"] = {
            "type": "object",
            "description": "Structured JSON payload written to a temporary input file for the script.",
            "additionalProperties": True,
        }
    if default_script["supports_sample_data"]:
        properties["use_sample_data"] = {
            "type": "boolean",
            "description": "Run the script's built-in sample-data path when no explicit input is provided.",
        }

    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _build_exec_repo_import(root_dir: Path, skills_index_path: Path) -> dict[str, Any]:
    skills_index = _safe_json(skills_index_path)
    entries = skills_index.get("skills") or []
    if not entries:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="skills-index.json contains no skills")

    tool_definitions = []
    plugin_map: dict[str, dict[str, Any]] = {}
    root_resolved = root_dir.resolve()

    for entry in entries:
        name = entry.get("name")
        source = entry.get("source")
        if not name or not source:
            continue

        plugin_dir = _resolve_index_source(root_dir, skills_index_path, source)
        if not _is_relative_to(plugin_dir, root_resolved):
            continue
        skill_doc = plugin_dir / "SKILL.md"
        resolved_plugin_dir = plugin_dir
        if not skill_doc.exists():
            nested_skill_doc = _find_nested_skill_doc(plugin_dir)
            if nested_skill_doc is None:
                continue
            skill_doc = nested_skill_doc
            resolved_plugin_dir = skill_doc.parent

        script_paths = (
            sorted(
                (path for path in (resolved_plugin_dir / "scripts").iterdir() if path.is_file() and path.suffix in {".py", ".sh"}),
                key=lambda path: (0 if path.suffix == ".py" else 1, path.name),
            )
            if (resolved_plugin_dir / "scripts").exists()
            else []
        )
        if not script_paths:
            continue

        scripts = [_inspect_script(script_path) for script_path in script_paths]
        default_script = _choose_default_script(scripts)
        plugin_manifest_path = resolved_plugin_dir / ".claude-plugin" / "plugin.json"
        plugin_manifest = _safe_json(plugin_manifest_path) if plugin_manifest_path.exists() else {}
        description = entry.get("description") or plugin_manifest.get("description") or _read_frontmatter_description(skill_doc) or f"Execute {name}"

        references = sorted(
            _safe_relative(path, root_dir) for path in (resolved_plugin_dir / "references").rglob("*")
            if path.is_file()
        ) if (resolved_plugin_dir / "references").exists() else []
        assets = sorted(
            _safe_relative(path, root_dir) for path in (resolved_plugin_dir / "assets").rglob("*")
            if path.is_file()
        ) if (resolved_plugin_dir / "assets").exists() else []
        expected_outputs = sorted(
            _safe_relative(path, root_dir) for path in (resolved_plugin_dir / "expected_outputs").rglob("*")
            if path.is_file()
        ) if (resolved_plugin_dir / "expected_outputs").exists() else []

        plugin_map[name] = {
            "name": name,
            "description": description,
            "source": str(resolved_plugin_dir),
            "source_relative": _safe_relative(resolved_plugin_dir, root_dir),
            "skill_doc": str(skill_doc),
            "skill_doc_relative": _safe_relative(skill_doc, root_dir),
            "homepage": plugin_manifest.get("homepage") or entry.get("homepage"),
            "version": plugin_manifest.get("version") or entry.get("version"),
            "category": entry.get("category") or plugin_manifest.get("category"),
            "plugin_manifest_path": str(plugin_manifest_path) if plugin_manifest_path.exists() else None,
            "plugin_manifest_relative": _safe_relative(plugin_manifest_path, root_dir) if plugin_manifest_path.exists() else None,
            "readme_relative": _safe_relative(resolved_plugin_dir / "README.md", root_dir) if (resolved_plugin_dir / "README.md").exists() else None,
            "settings_relative": _safe_relative(resolved_plugin_dir / "settings.json", root_dir) if (resolved_plugin_dir / "settings.json").exists() else None,
            "evals_relative": _safe_relative(resolved_plugin_dir / "evals.json", root_dir) if (resolved_plugin_dir / "evals.json").exists() else None,
            "scripts": scripts,
            "default_script": default_script["name"],
            "default_mode": default_script["default_mode"],
            "references": references,
            "assets": assets,
            "expected_outputs": expected_outputs,
        }
        tool_definitions.append(
            {
                "name": name,
                "description": description,
                "inputSchema": _build_exec_input_schema(plugin_map[name], default_script),
            }
        )

    if not tool_definitions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No executable Python-backed skills were found in the repository")

    return {
        "kind": "skill_repo_exec",
        "manifest": {
            "name": skills_index.get("name") or root_dir.name,
            "version": skills_index.get("version"),
            "description": skills_index.get("description") or f"Imported executable skill repository from {root_dir.name}",
            "visibility": "private",
            "handler": {
                "type": "skill_repo_exec",
                "root_dir": str(root_dir),
                "plugins": plugin_map,
            },
            "tools": tool_definitions,
        },
    }


def _build_marketplace_plugin_tool(plugin: dict[str, Any], default_script: dict[str, Any] | None) -> dict[str, Any]:
    if default_script is None:
        return {
            "name": plugin["name"],
            "description": plugin["description"],
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    return {
        "name": plugin["name"],
        "description": plugin["description"],
        "inputSchema": _build_exec_input_schema(plugin, default_script),
    }


def _build_marketplace_repo_import(root_dir: Path, marketplace_path: Path) -> dict[str, Any]:
    marketplace = _safe_json(marketplace_path)
    entries = marketplace.get("plugins") or []
    if not entries:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="marketplace.json contains no plugins")

    tool_definitions = []
    plugin_map: dict[str, dict[str, Any]] = {}
    root_resolved = root_dir.resolve()
    marketplace_root = marketplace_path.parent.resolve()
    if marketplace_root.name.startswith(".") and marketplace_root.parent == root_dir.resolve():
        marketplace_root = root_dir.resolve()

    for entry in entries:
        name = entry.get("name")
        source = entry.get("source")
        if not name or not source:
            continue

        plugin_dir = (marketplace_root / source).resolve()
        if not _is_relative_to(plugin_dir, root_resolved) or not plugin_dir.exists():
            continue

        skill_doc = plugin_dir / "SKILL.md"
        resolved_plugin_dir = plugin_dir
        if not skill_doc.exists():
            nested_skill_doc = _find_nested_skill_doc(plugin_dir)
            if nested_skill_doc is None:
                continue
            skill_doc = nested_skill_doc
            resolved_plugin_dir = skill_doc.parent

        script_paths = (
            sorted(
                (path for path in (resolved_plugin_dir / "scripts").iterdir() if path.is_file() and path.suffix in {".py", ".sh"}),
                key=lambda path: (0 if path.suffix == ".py" else 1, path.name),
            )
            if (resolved_plugin_dir / "scripts").exists()
            else []
        )
        scripts = [_inspect_script(script_path) for script_path in script_paths]
        default_script = _choose_default_script(scripts) if scripts else None
        description = entry.get("description") or _read_frontmatter_description(skill_doc) or f"Instructions for {name}"

        plugin_map[name] = {
            "name": name,
            "description": description,
            "source": str(resolved_plugin_dir),
            "source_relative": _safe_relative(resolved_plugin_dir, root_dir),
            "skill_doc": str(skill_doc),
            "skill_doc_relative": _safe_relative(skill_doc, root_dir),
            "homepage": entry.get("homepage"),
            "version": entry.get("version"),
            "category": entry.get("category"),
            "scripts": scripts,
            "default_script": default_script["name"] if default_script else None,
            "default_mode": default_script["default_mode"] if default_script else "docs_only",
            "doc_only": not scripts,
        }
        tool_definitions.append(_build_marketplace_plugin_tool(plugin_map[name], default_script))

    if not tool_definitions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No importable marketplace plugins were found in the repository")

    return {
        "kind": "marketplace_repo",
        "manifest": {
            "name": marketplace.get("name") or root_dir.name,
            "version": marketplace.get("version"),
            "description": marketplace.get("description") or f"Imported marketplace skill repository from {root_dir.name}",
            "visibility": "private",
            "handler": {
                "type": "marketplace_repo",
                "root_dir": str(root_dir),
                "plugins": plugin_map,
            },
            "tools": tool_definitions,
        },
    }


def _collect_skill_doc_paths(root_dir: Path) -> list[Path]:
    candidates = [
        path
        for path in root_dir.rglob("SKILL.md")
        if "__MACOSX" not in path.parts
        and ".git" not in path.parts
        and "node_modules" not in path.parts
    ]
    candidates.sort(key=lambda path: (len(path.relative_to(root_dir).parts), str(path)))
    return candidates


def _find_nested_skill_doc(root_dir: Path) -> Path | None:
    matches = _collect_skill_doc_paths(root_dir)
    if not matches:
        return None
    return matches[0]


def _build_docs_first_plugin(root_dir: Path, plugin_dir: Path, skill_doc: Path) -> dict[str, Any] | None:
    script_paths = (
        sorted(
            (path for path in (plugin_dir / "scripts").iterdir() if path.is_file() and path.suffix in {".py", ".sh"}),
            key=lambda path: (0 if path.suffix == ".py" else 1, path.name),
        )
        if (plugin_dir / "scripts").exists()
        else []
    )
    scripts = [_inspect_script(script_path) for script_path in script_paths]
    default_script = _choose_default_script(scripts) if scripts else None
    plugin_manifest_path = plugin_dir / ".claude-plugin" / "plugin.json"
    plugin_manifest = _safe_json(plugin_manifest_path) if plugin_manifest_path.exists() else {}
    description = (
        plugin_manifest.get("description")
        or _read_frontmatter_description(skill_doc)
        or f"Instructions for {plugin_dir.name}"
    )
    plugin_name = (
        plugin_manifest.get("name")
        or _read_frontmatter_name(skill_doc)
        or _read_skill_doc_title(skill_doc)
        or plugin_dir.name
    )

    references = sorted(
        _safe_relative(path, root_dir) for path in (plugin_dir / "references").rglob("*") if path.is_file()
    ) if (plugin_dir / "references").exists() else []
    assets = sorted(
        _safe_relative(path, root_dir) for path in (plugin_dir / "assets").rglob("*") if path.is_file()
    ) if (plugin_dir / "assets").exists() else []
    expected_outputs = sorted(
        _safe_relative(path, root_dir) for path in (plugin_dir / "expected_outputs").rglob("*") if path.is_file()
    ) if (plugin_dir / "expected_outputs").exists() else []

    return {
        "name": plugin_name,
        "description": description,
        "source": str(plugin_dir),
        "source_relative": _safe_relative(plugin_dir, root_dir),
        "skill_doc": str(skill_doc),
        "skill_doc_relative": _safe_relative(skill_doc, root_dir),
        "homepage": plugin_manifest.get("homepage"),
        "version": plugin_manifest.get("version"),
        "category": plugin_manifest.get("category"),
        "plugin_manifest_path": str(plugin_manifest_path) if plugin_manifest_path.exists() else None,
        "plugin_manifest_relative": _safe_relative(plugin_manifest_path, root_dir) if plugin_manifest_path.exists() else None,
        "readme_relative": _safe_relative(plugin_dir / "README.md", root_dir) if (plugin_dir / "README.md").exists() else None,
        "settings_relative": _safe_relative(plugin_dir / "settings.json", root_dir) if (plugin_dir / "settings.json").exists() else None,
        "evals_relative": _safe_relative(plugin_dir / "evals.json", root_dir) if (plugin_dir / "evals.json").exists() else None,
        "scripts": scripts,
        "default_script": default_script["name"] if default_script else None,
        "default_mode": default_script["default_mode"] if default_script else "docs_only",
        "doc_only": not scripts,
        "references": references,
        "assets": assets,
        "expected_outputs": expected_outputs,
    }


def _build_docs_first_repo_import(root_dir: Path, skill_docs: list[Path]) -> dict[str, Any]:
    tool_definitions = []
    plugin_map: dict[str, dict[str, Any]] = {}
    used_names: set[str] = set()

    for skill_doc in skill_docs:
        plugin_dir = skill_doc.parent
        plugin = _build_docs_first_plugin(root_dir, plugin_dir, skill_doc)
        if plugin is None:
            continue

        base_name = str(plugin["name"]).strip() or plugin_dir.name
        name = base_name
        suffix = 2
        while name in used_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(name)
        plugin["name"] = name
        plugin_map[name] = plugin
        tool_definitions.append(_build_marketplace_plugin_tool(plugin, _choose_default_script(plugin["scripts"]) if plugin["scripts"] else None))

    if not tool_definitions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No importable skills were found in the archive")

    return {
        "kind": "docs_first_repo",
        "manifest": {
            "name": root_dir.name,
            "description": f"Imported docs-first skill repository from {root_dir.name}",
            "visibility": "private",
            "handler": {
                "type": "docs_first_repo",
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
        detail="Uploaded ZIP must contain skill.json/skillhub.json, a docs-first SKILL.md skill folder, skills-index.json, or marketplace.json for a skill repository",
    )


def extract_package_archive(archive_path: Path, target_dir: Path) -> dict[str, Any]:
    if target_dir.exists():
        logger.info("Cleaning extracted package directory before reuse: %s", target_dir)
        remove_tree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting archive %s into %s", archive_path, target_dir)
    with ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            if not _is_relative_to(member_path, target_dir):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Archive contains unsafe paths",
                )
        archive.extractall(target_dir)

    root_dir = _resolve_archive_root(target_dir)
    skills_index_path = _find_skills_index(root_dir)
    if skills_index_path is not None:
        return _build_exec_repo_import(root_dir, skills_index_path)
    marketplace_path = _find_marketplace_manifest(root_dir)
    if marketplace_path is not None:
        return _build_marketplace_repo_import(root_dir, marketplace_path)
    for manifest_name in MANIFEST_NAMES:
        if (root_dir / manifest_name).exists():
            return _build_single_skill_import(root_dir)
    skill_docs = _collect_skill_doc_paths(root_dir)
    if skill_docs:
        return _build_docs_first_repo_import(root_dir, skill_docs)
    return _build_single_skill_import(root_dir)


def cleanup_skill_storage(skill_id: int) -> None:
    skill_dir = skill_storage_dir(skill_id)
    remove_tree(skill_dir)


def cleanup_skill_version_storage(version_id: int) -> None:
    version_dir = skill_version_storage_dir(version_id)
    remove_tree(version_dir)


def cleanup_review_workbench(version_id: int) -> None:
    workbench_dir = review_workbench_dir(version_id)
    remove_tree(workbench_dir)


def cleanup_deployed_skill(skill_id: int, version_id: int) -> None:
    deployment_dir = deployed_skill_dir(skill_id, version_id)
    remove_tree(deployment_dir)


def prepare_review_workbench(
    version_id: int,
    source_package_path: Path | None,
    source_extracted_path: Path | None,
) -> dict[str, str | None]:
    workbench_dir = review_workbench_dir(version_id)
    remove_tree(workbench_dir)
    workbench_dir.mkdir(parents=True, exist_ok=True)

    package_copy_path: Path | None = None
    if source_package_path and source_package_path.exists():
        package_copy_path = workbench_dir / "package.zip"
        shutil.copy2(source_package_path, package_copy_path)

    extracted_copy_path: Path | None = None
    if source_extracted_path and source_extracted_path.exists():
        extracted_copy_path = workbench_dir / "package"
        shutil.copytree(source_extracted_path, extracted_copy_path)

    return {
        "workbench_path": str(workbench_dir),
        "package_path": str(package_copy_path) if package_copy_path else None,
        "extracted_path": str(extracted_copy_path) if extracted_copy_path else None,
    }


def deploy_skill_snapshot(
    skill_id: int,
    version_id: int,
    source_extracted_path: Path,
) -> dict[str, str]:
    target_root = deployed_skill_dir(skill_id, version_id)
    remove_tree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)
    target_package_dir = target_root / "package"
    shutil.copytree(source_extracted_path, target_package_dir)
    return {
        "deployment_root": str(target_root),
        "deployment_path": str(target_package_dir),
    }


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
