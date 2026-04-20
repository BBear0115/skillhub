import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

from app.models import Skill, Tool


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


async def execute_tool(skill: Skill, tool: Tool, arguments: dict) -> dict:
    handler = skill.handler_config or {}
    handler_type = handler.get("type", "http")

    if handler_type == "http":
        url = handler.get("url")
        if not url:
            return {"content": [{"type": "text", "text": "Missing handler URL"}], "isError": True}
        headers = handler.get("headers", {})
        async with httpx.AsyncClient(timeout=30) as client:
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

    if handler_type == "skill_repo":
        plugins = handler.get("plugins", {})
        plugin = plugins.get(tool.name)
        if not plugin:
            return {"content": [{"type": "text", "text": f"Unknown repository skill: {tool.name}"}], "isError": True}
        skill_doc = Path(plugin["skill_doc"])
        if not skill_doc.exists():
            return {"content": [{"type": "text", "text": f"Missing SKILL.md for {tool.name}"}], "isError": True}
        mode = arguments.get("mode", "summary")
        content = skill_doc.read_text(encoding="utf-8")
        if mode == "full":
            text = content
        else:
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            text = "\n".join(lines[:12])
        meta = {
            "name": plugin.get("name"),
            "source": plugin.get("source"),
            "homepage": plugin.get("homepage"),
            "version": plugin.get("version"),
            "category": plugin.get("category"),
        }
        return {
            "content": [
                {"type": "text", "text": text},
                {"type": "text", "text": json.dumps(meta, ensure_ascii=False)},
            ],
            "isError": False,
        }

    return {"content": [{"type": "text", "text": f"Unknown handler type: {handler_type}"}], "isError": True}
