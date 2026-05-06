import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.models import User, Workspace
from app.services.skill_packages import ensure_storage_root, remove_tree


class SkillDeploymentError(RuntimeError):
    pass


def ensure_admin_workspace(session: Session, user: User) -> Workspace:
    workspace = session.exec(
        select(Workspace).where(Workspace.owner_id == user.id, Workspace.type == "admin")
    ).first()
    if workspace is not None:
        return workspace
    workspace = Workspace(name="Super Admin Workbench", type="admin", owner_id=user.id)
    session.add(workspace)
    session.commit()
    session.refresh(workspace)
    return workspace


def admin_deployments_root(admin_workspace_id: int) -> Path:
    root = ensure_storage_root() / "admin-workspaces" / f"workspace-{admin_workspace_id}" / "deployments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def deployment_root(admin_workspace_id: int, skill_id: int, version_id: int) -> Path:
    return admin_deployments_root(admin_workspace_id) / f"skill-{skill_id}" / f"version-{version_id}"


def venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def venv_bin_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts"
    return venv_path / "bin"


def _command_timeout(default: int) -> int:
    raw = os.getenv("SKILLHUB_DEPLOY_COMMAND_TIMEOUT_SECONDS")
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    timeout = _command_timeout(timeout)
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
        raise SkillDeploymentError(output or f"Command failed: {' '.join(command)}")
    return completed


def _runtime_python_override() -> Path | None:
    raw = os.getenv("SKILLHUB_SKILL_RUNTIME_PYTHON")
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def _manifest_dependencies(package_dir: Path) -> list[str]:
    for name in ("skill.json", "skillhub.json"):
        manifest = _read_json(package_dir / name)
        runtime = manifest.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("dependencies"), list):
            return [str(item).strip() for item in runtime["dependencies"] if str(item).strip()]
    return []


def _dependency_manifest(package_dir: Path) -> dict[str, Any]:
    requirements = package_dir / "requirements.txt"
    if requirements.exists():
        return {"kind": "requirements", "path": str(requirements)}
    pyproject = package_dir / "pyproject.toml"
    if pyproject.exists():
        return {"kind": "pyproject", "path": str(pyproject)}
    dependencies = _manifest_dependencies(package_dir)
    if dependencies:
        return {"kind": "manifest", "dependencies": dependencies}
    return {"kind": "none"}


def _install_dependencies(venv_python: Path, package_dir: Path, dependency_manifest: dict[str, Any]) -> None:
    kind = dependency_manifest.get("kind")
    if kind == "none":
        return
    if kind == "requirements":
        _run([str(venv_python), "-m", "pip", "install", "-r", str(dependency_manifest["path"])], cwd=package_dir)
        return
    if kind == "pyproject":
        _run([str(venv_python), "-m", "pip", "install", "."], cwd=package_dir)
        return
    if kind == "manifest":
        dependencies = dependency_manifest.get("dependencies") or []
        if dependencies:
            _run([str(venv_python), "-m", "pip", "install", *[str(item) for item in dependencies]], cwd=package_dir)


def _rewrite_path_value(value: Any, source_root: Path, target_root: Path) -> Any:
    if isinstance(value, str):
        try:
            source_path = Path(value).resolve()
            relative_path = source_path.relative_to(source_root.resolve())
            return str((target_root / relative_path).resolve())
        except (OSError, ValueError):
            return value
        return value
    if isinstance(value, list):
        return [_rewrite_path_value(item, source_root, target_root) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_path_value(item, source_root, target_root) for key, item in value.items()}
    return value


def build_deployed_handler_config(
    handler_config: dict[str, Any],
    source_root: Path,
    deployed_package_path: Path,
    venv_python: Path,
    venv_path: Path,
) -> dict[str, Any]:
    rewritten = _rewrite_path_value(handler_config or {}, source_root, deployed_package_path)
    handler = rewritten if isinstance(rewritten, dict) else dict(handler_config or {})
    handler["runtime_path"] = str(deployed_package_path.parent.resolve())
    handler["venv_path"] = str(venv_path.resolve())
    handler["venv_python"] = str(venv_python.resolve())
    handler["venv_bin_path"] = str(venv_bin_path(venv_path).resolve())
    if isinstance(handler.get("plugins"), dict):
        for plugin in handler["plugins"].values():
            if isinstance(plugin, dict):
                plugin["venv_path"] = handler["venv_path"]
                plugin["venv_python"] = handler["venv_python"]
                plugin["venv_bin_path"] = handler["venv_bin_path"]
    if handler.get("type") == "python_package":
        handler["package_dir"] = str(deployed_package_path.resolve())
    return handler


def deploy_skill_runtime(
    *,
    admin_workspace_id: int,
    skill_id: int,
    version_id: int,
    source_extracted_path: Path,
    handler_config: dict[str, Any],
    original_extracted_path: Path | None,
) -> dict[str, Any]:
    root = deployment_root(admin_workspace_id, skill_id, version_id)
    remove_tree(root)
    root.mkdir(parents=True, exist_ok=True)
    package_dir = root / "package"
    shutil.copytree(source_extracted_path, package_dir)

    dependencies = _dependency_manifest(package_dir)
    venv_path = root / ".venv"
    venv_command = [sys.executable, "-m", "venv"]
    if dependencies.get("kind") == "none":
        venv_command.append("--without-pip")
    venv_command.append(str(venv_path))
    _run(venv_command, cwd=root)
    python_path = venv_python_path(venv_path)
    if not python_path.exists():
        raise SkillDeploymentError(f"Virtual environment Python not found: {python_path}")

    _install_dependencies(python_path, package_dir, dependencies)
    runtime_python = _runtime_python_override() or python_path

    source_root = original_extracted_path.resolve() if original_extracted_path else source_extracted_path.resolve()
    deployed_handler = build_deployed_handler_config(
        handler_config,
        source_root,
        package_dir,
        runtime_python,
        venv_path,
    )
    return {
        "runtime_path": str(root.resolve()),
        "deployment_path": str(package_dir.resolve()),
        "venv_path": str(venv_path.resolve()),
        "venv_python": str(python_path.resolve()),
        "dependency_manifest": dependencies,
        "deployed_handler_config": deployed_handler,
    }
