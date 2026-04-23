import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from subprocess import CompletedProcess
from uuid import uuid4

import pytest

from app.models.tool import Tool
from app.services.skill_runner import _build_repo_exec_command, execute_tool


def _make_tool() -> Tool:
    return Tool(id=1, skill_id=7, name="demo", description=None, input_schema={})


def _make_temp_dir() -> Path:
    path = Path(__file__).parent / ".tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_build_repo_exec_command_supports_path_and_input(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_root = _make_temp_dir()
    plugin_root = tmp_root / "plugin"
    scripts_dir = plugin_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "runner.py").write_text("print('ok')", encoding="utf-8")

    plugin = {"source": str(plugin_root)}
    script = {
        "name": "runner.py",
        "positionals": [],
        "optionals": ["--json", "--path", "--input-file", "--flag"],
        "supports_sample_data": False,
        "default_mode": "json_input",
    }

    temp_inputs = tmp_root / "temp-inputs"
    temp_inputs.mkdir(parents=True, exist_ok=True)

    def local_mkdtemp(*args, **kwargs):
        path = temp_inputs / uuid4().hex
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    monkeypatch.setattr(tempfile, "mkdtemp", local_mkdtemp)

    try:
        command, temp_input_path = _build_repo_exec_command(
            plugin,
            _make_tool(),
            script,
            {
                "target_path": "examples/demo",
                "input": {"message": "hello"},
                "options": {"flag": "value"},
                "output_format": "json",
            },
        )

        assert "--json" in command
        assert "--path" in command
        assert "examples/demo" in command
        assert "--input-file" in command
        assert "--flag" in command
        assert "value" in command
        assert temp_input_path is not None
        payload = json.loads(temp_input_path.read_text(encoding="utf-8"))
        assert payload == {"message": "hello"}
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_execute_tool_runs_skill_repo_exec_script() -> None:
    tmp_root = _make_temp_dir()
    plugin_root = tmp_root / "plugin"
    scripts_dir = plugin_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "runner.py").write_text(
        "import argparse, json\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--json', action='store_true')\n"
        "args = parser.parse_args()\n"
        "print(json.dumps({'ok': True, 'json': args.json}))\n",
        encoding="utf-8",
    )

    handler_config = {
        "type": "skill_repo_exec",
        "plugins": {
            "demo": {
                "source": str(plugin_root),
                "default_script": "runner.py",
                "scripts": [
                    {
                        "name": "runner.py",
                        "positionals": [],
                        "optionals": ["--json"],
                        "supports_sample_data": False,
                        "default_mode": "no_args",
                    }
                ],
            }
        },
    }

    try:
        result = await execute_tool(handler_config, _make_tool(), {})

        assert result["isError"] is False
        assert any('"ok": true' in item["text"].lower() for item in result["content"])
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_execute_tool_reports_missing_repo_script() -> None:
    tmp_root = _make_temp_dir()
    plugin_root = tmp_root / "plugin"
    plugin_root.mkdir(parents=True, exist_ok=True)
    handler_config = {
        "type": "skill_repo_exec",
        "plugins": {
            "demo": {
                "source": str(plugin_root),
                "default_script": "missing.py",
                "scripts": [
                    {
                        "name": "missing.py",
                        "positionals": [],
                        "optionals": [],
                        "supports_sample_data": False,
                        "default_mode": "no_args",
                    }
                ],
            }
        },
    }

    try:
        result = await execute_tool(handler_config, _make_tool(), {})

        assert result["isError"] is True
        assert "Script not found" in result["content"][0]["text"]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_execute_tool_reports_repo_script_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_root = _make_temp_dir()
    plugin_root = tmp_root / "plugin"
    scripts_dir = plugin_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "runner.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    handler_config = {
        "type": "skill_repo_exec",
        "plugins": {
            "demo": {
                "source": str(plugin_root),
                "default_script": "runner.py",
                "scripts": [
                    {
                        "name": "runner.py",
                        "positionals": [],
                        "optionals": [],
                        "supports_sample_data": False,
                        "default_mode": "no_args",
                    }
                ],
            }
        },
    }

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None):
        return CompletedProcess(command, 1, stdout="", stderr="ModuleNotFoundError: No module named 'missing_dep'")

    monkeypatch.setattr("app.services.skill_runner.subprocess.run", fake_run)

    try:
        result = await execute_tool(handler_config, _make_tool(), {})

        assert result["isError"] is True
        assert "ModuleNotFoundError" in result["content"][0]["text"]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@pytest.mark.asyncio
async def test_execute_tool_reports_repo_script_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_root = _make_temp_dir()
    plugin_root = tmp_root / "plugin"
    scripts_dir = plugin_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "runner.py").write_text("print('slow')\n", encoding="utf-8")
    handler_config = {
        "type": "skill_repo_exec",
        "plugins": {
            "demo": {
                "source": str(plugin_root),
                "default_script": "runner.py",
                "scripts": [
                    {
                        "name": "runner.py",
                        "positionals": [],
                        "optionals": [],
                        "timeout_seconds": 1,
                        "supports_sample_data": False,
                        "default_mode": "no_args",
                    }
                ],
            }
        },
    }

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None):
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr("app.services.skill_runner.subprocess.run", fake_run)

    try:
        result = await execute_tool(handler_config, _make_tool(), {})

        assert result["isError"] is True
        assert "Execution timed out after 1s" in result["content"][0]["text"]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
