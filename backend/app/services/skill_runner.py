import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings
from app.models import Skill, Tool

DEFAULT_EXEC_TIMEOUT_SECONDS = 30
DNSMOS_EXEC_TIMEOUT_SECONDS = 300


def _resolve_bash_executable() -> list[str] | None:
    if os.name != "nt":
        return ["bash"]
    candidates = [
        Path("C:/tools/msys64/usr/bin/bash.exe"),
        Path("C:/tools/msys64/usr/bin/sh.exe"),
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files/Git/usr/bin/bash.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    return None


def _load_module(module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"skillhub_skill_{module_path.stem}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _execute_python_package(handler: dict[str, Any], tool: Tool, arguments: dict) -> dict:
    package_dir = handler.get("package_dir")
    entrypoint = handler.get("entrypoint")
    if not package_dir or not entrypoint:
        return {"content": [{"type": "text", "text": "Missing package_dir or entrypoint"}], "isError": True}

    module_name, _, function_name = entrypoint.partition(":")
    if not module_name or not function_name:
        return {"content": [{"type": "text", "text": "Entrypoint must be formatted as path.py:function"}], "isError": True}

    module_path = (Path(package_dir) / module_name).resolve()
    if not module_path.exists():
        return {"content": [{"type": "text", "text": f"Module not found: {module_name}"}], "isError": True}

    try:
        module = _load_module(module_path)
        func = getattr(module, function_name)
        context = {"tool": tool.name, "arguments": arguments, "skill_id": tool.skill_id}
        if asyncio.iscoroutinefunction(func):
            result = await func(context)
        else:
            result = func(context)
        if isinstance(result, dict) and "content" in result:
            return {"content": result["content"], "isError": result.get("isError", False)}
        return {"content": [{"type": "text", "text": str(result)}], "isError": False}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"Execution error: {exc}"}], "isError": True}


def _normalize_flag_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _build_option_index(flags: list[str]) -> dict[str, str]:
    index: dict[str, str] = {}
    for flag in flags:
        if not flag.startswith("--"):
            continue
        normalized = _normalize_flag_name(flag[2:])
        index[normalized] = flag
    return index


def _resolve_repo_script(plugin: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
    scripts = plugin.get("scripts") or []
    if not scripts:
        return None
    requested = arguments.get("script")
    if isinstance(requested, str) and requested:
        for script in scripts:
            if script.get("name") == requested:
                return script
        return None
    default_name = plugin.get("default_script")
    for script in scripts:
        if script.get("name") == default_name:
            return script
    return scripts[0]


def _script_uses_sample_data(script: dict[str, Any], arguments: dict[str, Any]) -> bool:
    if "use_sample_data" in arguments:
        return bool(arguments.get("use_sample_data"))
    return bool(script.get("supports_sample_data"))


def _managed_temp_dir(tool: Tool) -> Path:
    base_dir = Path(settings.storage_root).resolve() / "runtime" / "exec-inputs" / f"skill-{tool.skill_id}"
    base_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = base_dir / f"{tool.name}-{uuid4().hex}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _build_repo_exec_command(plugin: dict[str, Any], tool: Tool, script: dict[str, Any], arguments: dict[str, Any]) -> tuple[list[str], Path | None]:
    script_path = (Path(plugin["source"]) / "scripts" / script["name"]).resolve()
    plugin_root = Path(plugin["source"]).resolve()
    if script_path.suffix == ".sh":
        bash = _resolve_bash_executable()
        if bash is None:
            raise RuntimeError("Bash is required to execute shell-based skills on this host")
        command = [*bash, str(script_path)]
    else:
        command = [sys.executable, str(script_path)]
    temp_input_path: Path | None = None

    option_index = _build_option_index(script.get("optionals", []))
    output_format = arguments.get("output_format", "auto")
    if output_format in ("auto", "json"):
        if "json" in option_index:
            command.append(option_index["json"])
        elif "format" in option_index:
            command.extend([option_index["format"], "json"])
    elif output_format == "text" and "format" in option_index:
        command.extend([option_index["format"], "text"])

    options = arguments.get("options")
    if isinstance(options, dict):
        for key, value in options.items():
            normalized = _normalize_flag_name(str(key))
            flag = option_index.get(normalized)
            if not flag:
                continue
            if isinstance(value, bool):
                if value:
                    command.append(flag)
                continue
            if isinstance(value, list):
                for item in value:
                    command.extend([flag, str(item)])
                continue
            if value is not None:
                command.extend([flag, str(value)])

    target_path = arguments.get("target_path")
    input_payload = arguments.get("input")
    positional_args = [name.lower() for name in script.get("positionals", [])]
    consumed_positional = False

    if isinstance(target_path, str) and target_path:
        target_flag = None
        for candidate in ("path", "project", "project-dir", "dir", "directory", "root"):
            if candidate in option_index:
                target_flag = option_index[candidate]
                break
        if target_flag:
            command.extend([target_flag, target_path])
        elif positional_args and any(token in positional_args[0] for token in ("path", "project", "dir", "directory", "root")):
            command.append(target_path)
            consumed_positional = True

    if isinstance(input_payload, dict):
        temp_dir = _managed_temp_dir(tool)
        temp_input_path = temp_dir / "input.json"
        temp_input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        input_flag = None
        for candidate in ("input-file", "input", "file", "data-file", "data", "json-file"):
            if candidate in option_index:
                input_flag = option_index[candidate]
                break
        if input_flag:
            command.extend([input_flag, str(temp_input_path)])
        elif positional_args and not consumed_positional and any(token in positional_args[0] for token in ("file", "input", "data")):
            command.append(str(temp_input_path))
            consumed_positional = True

    if _script_uses_sample_data(script, arguments) and "sample" in option_index:
        command.append(option_index["sample"])

    return command, temp_input_path


def _cleanup_temp_input(temp_input_path: Path | None) -> None:
    if temp_input_path is None:
        return
    temp_dir = temp_input_path.parent
    try:
        if temp_input_path.exists():
            temp_input_path.unlink()
        if temp_dir.exists():
            temp_dir.rmdir()
    except OSError:
        pass


def _resolve_repo_exec_timeout(plugin: dict[str, Any], script: dict[str, Any]) -> int | float:
    for candidate in (script.get("timeout_seconds"), plugin.get("timeout_seconds")):
        if isinstance(candidate, (int, float)) and candidate > 0:
            return candidate
    if script.get("name") == "dnsmos_batch_filter.py":
        return DNSMOS_EXEC_TIMEOUT_SECONDS
    return DEFAULT_EXEC_TIMEOUT_SECONDS


async def _execute_repo_python_script(plugin: dict[str, Any], tool: Tool, arguments: dict[str, Any]) -> dict:
    script = _resolve_repo_script(plugin, arguments)
    if script is None:
        return {"content": [{"type": "text", "text": f"Unknown script for {tool.name}"}], "isError": True}

    script_path = (Path(plugin["source"]) / "scripts" / script["name"]).resolve()
    plugin_root = Path(plugin["source"]).resolve()
    if not str(script_path).startswith(str(plugin_root)) or not script_path.exists():
        return {"content": [{"type": "text", "text": f"Script not found: {script.get('name')}"}], "isError": True}

    command, temp_input_path = _build_repo_exec_command(plugin, tool, script, arguments)
    timeout_seconds = _resolve_repo_exec_timeout(plugin, script)
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=str(plugin_root),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        _cleanup_temp_input(temp_input_path)
        return {
            "content": [{"type": "text", "text": f"Execution timed out after {timeout_seconds}s: {script['name']}"}],
            "isError": True,
        }
    except Exception as exc:
        _cleanup_temp_input(temp_input_path)
        return {"content": [{"type": "text", "text": f"Execution error: {exc}"}], "isError": True}

    _cleanup_temp_input(temp_input_path)

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    content: list[dict[str, str]] = []
    if stdout:
        try:
            parsed = json.loads(stdout)
            content.append({"type": "text", "text": json.dumps(parsed, ensure_ascii=False, indent=2)})
        except json.JSONDecodeError:
            content.append({"type": "text", "text": stdout})
    if stderr and (completed.returncode != 0 or not stdout):
        content.append({"type": "text", "text": stderr})
    content.append(
        {
            "type": "text",
            "text": json.dumps(
                {
                    "tool": tool.name,
                    "script": script["name"],
                    "return_code": completed.returncode,
                    "default_mode": script.get("default_mode"),
                },
                ensure_ascii=False,
            ),
        }
    )

    is_error = False
    if completed.returncode != 0 and not stdout:
        is_error = True
    if stderr and not stdout:
        is_error = True
    if not content:
        content = [{"type": "text", "text": f"No output from {script['name']}"}]
        is_error = completed.returncode != 0

    return {"content": content, "isError": is_error}


async def _execute_marketplace_plugin(plugin: dict[str, Any], tool: Tool, arguments: dict[str, Any]) -> dict:
    if plugin.get("scripts"):
        return await _execute_repo_python_script(plugin, tool, arguments)

    skill_doc = plugin.get("skill_doc")
    content: list[dict[str, str]] = []
    if isinstance(skill_doc, str) and Path(skill_doc).exists():
        content.append({"type": "text", "text": Path(skill_doc).read_text(encoding="utf-8")})
    content.append(
        {
            "type": "text",
            "text": json.dumps(
                {
                    "tool": tool.name,
                    "mode": "docs_only",
                    "return_code": 0,
                },
                ensure_ascii=False,
            ),
        }
    )
    return {"content": content, "isError": False}


async def execute_tool(skill: Skill, tool: Tool, arguments: dict) -> dict:
    handler = skill.handler_config or {}
    handler_type = handler.get("type", "http")

    if handler_type == "http":
        url = handler.get("url")
        if not url:
            return {"content": [{"type": "text", "text": "Missing handler URL"}], "isError": True}
        headers = handler.get("headers", {})
        async with httpx.AsyncClient(timeout=DEFAULT_EXEC_TIMEOUT_SECONDS) as client:
            try:
                resp = await client.post(url, json={"tool": tool.name, "arguments": arguments}, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                # Assume webhook returns either MCP content list or plain JSON
                if isinstance(data, list):
                    return {"content": data, "isError": False}
                if isinstance(data, dict) and "content" in data:
                    return {"content": data["content"], "isError": data.get("isError", False)}
                return {"content": [{"type": "text", "text": str(data)}], "isError": False}
            except httpx.HTTPStatusError as e:
                return {"content": [{"type": "text", "text": f"HTTP error: {e.response.status_code}"}], "isError": True}
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Execution error: {e}"}], "isError": True}

    if handler_type == "python_package":
        return await _execute_python_package(handler, tool, arguments)

    if handler_type == "inline":
        responses = handler.get("responses", {})
        payload = responses.get(tool.name) or responses.get("*")
        if payload is None:
            return {"content": [{"type": "text", "text": f"No inline response configured for {tool.name}"}], "isError": True}
        if isinstance(payload, list):
            return {"content": payload, "isError": False}
        return {"content": [{"type": "text", "text": str(payload)}], "isError": False}

    if handler_type == "skill_repo_exec":
        plugins = handler.get("plugins", {})
        plugin = plugins.get(tool.name)
        if not plugin:
            return {"content": [{"type": "text", "text": f"Unknown executable repository skill: {tool.name}"}], "isError": True}
        return await _execute_repo_python_script(plugin, tool, arguments)

    if handler_type == "marketplace_repo":
        plugins = handler.get("plugins", {})
        plugin = plugins.get(tool.name)
        if not plugin:
            return {"content": [{"type": "text", "text": f"Unknown marketplace skill: {tool.name}"}], "isError": True}
        return await _execute_marketplace_plugin(plugin, tool, arguments)

    return {"content": [{"type": "text", "text": f"Unknown handler type: {handler_type}"}], "isError": True}
