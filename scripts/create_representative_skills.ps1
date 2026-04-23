$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$workRoot = Join-Path $root ".tmp\representative-skills"
$srcRoot = Join-Path $workRoot "src"
$zipRoot = Join-Path $workRoot "zips"

if (Test-Path $workRoot) {
    Remove-Item -LiteralPath $workRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $srcRoot | Out-Null
New-Item -ItemType Directory -Path $zipRoot | Out-Null

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)][string] $Path,
        [Parameter(Mandatory = $true)][string] $Content
    )
    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Content, [System.Text.UTF8Encoding]::new($false))
}

function New-ZipFromDirectory {
    param(
        [Parameter(Mandatory = $true)][string] $SourceDir,
        [Parameter(Mandatory = $true)][string] $ZipPath
    )
    if (Test-Path $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Compress-Archive -Path (Join-Path $SourceDir "*") -DestinationPath $ZipPath
}

# inline
$inlineDir = Join-Path $srcRoot "inline-representative"
New-Item -ItemType Directory -Path $inlineDir | Out-Null
Write-Utf8File (Join-Path $inlineDir "skill.json") @'
{
  "name": "inline-representative",
  "description": "Representative inline skill. Use when testing static MCP responses without runtime execution.",
  "visibility": "private",
  "handler": {
    "type": "inline",
    "responses": {
      "inline_status": [
        {
          "type": "text",
          "text": "{\"mode\":\"inline\",\"status\":\"ok\"}"
        }
      ]
    }
  },
  "tools": [
    {
      "name": "inline_status",
      "description": "Return a static inline response.",
      "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  ]
}
'@
Write-Utf8File (Join-Path $inlineDir "SKILL.md") @'
---
name: inline-representative
description: Return a deterministic static response to verify SkillHub inline handler execution with no arguments.
---

# Inline Representative

This package represents SkillHub's inline handler type.
'@
New-ZipFromDirectory $inlineDir (Join-Path $zipRoot "inline-representative.zip")

# python_package
$pythonPackageDir = Join-Path $srcRoot "python-package-representative"
New-Item -ItemType Directory -Path $pythonPackageDir | Out-Null
Write-Utf8File (Join-Path $pythonPackageDir "skill.json") @'
{
  "name": "python-package-representative",
  "description": "Representative python_package skill. Use when testing in-process Python function execution.",
  "visibility": "private",
  "handler": {
    "type": "python_package",
    "entrypoint": "main.py:run"
  },
  "tools": [
    {
      "name": "python_package_status",
      "description": "Run a Python package entrypoint and return deterministic JSON.",
      "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  ]
}
'@
Write-Utf8File (Join-Path $pythonPackageDir "SKILL.md") @'
---
name: python-package-representative
description: Return a deterministic response from a Python entrypoint to verify SkillHub python_package execution.
---

# Python Package Representative

This package represents SkillHub's python_package handler type.
'@
Write-Utf8File (Join-Path $pythonPackageDir "main.py") @'
from __future__ import annotations

import json


def run(context: dict) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "mode": "python_package",
                        "tool": context["tool"],
                        "arguments": context["arguments"],
                        "status": "ok",
                    },
                    ensure_ascii=False,
                ),
            }
        ],
        "isError": False,
    }
'@
New-ZipFromDirectory $pythonPackageDir (Join-Path $zipRoot "python-package-representative.zip")

# http
$httpDir = Join-Path $srcRoot "http-representative"
New-Item -ItemType Directory -Path $httpDir | Out-Null
Write-Utf8File (Join-Path $httpDir "skill.json") @'
{
  "name": "http-representative",
  "description": "Representative HTTP skill inspired by generic webhook MCP patterns. Use when testing SkillHub outbound HTTP execution.",
  "visibility": "private",
  "handler": {
    "type": "http",
    "url": "http://127.0.0.1:8765/"
  },
  "tools": [
    {
      "name": "http_status",
      "description": "POST a no-argument call to a local webhook and return the webhook payload.",
      "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": false
      }
    }
  ]
}
'@
Write-Utf8File (Join-Path $httpDir "SKILL.md") @'
---
name: http-representative
description: Return a deterministic response from a webhook endpoint to verify SkillHub HTTP handler execution.
---

# HTTP Representative

This package represents SkillHub's HTTP handler type and is inspired by public webhook-based MCP tooling.
'@
New-ZipFromDirectory $httpDir (Join-Path $zipRoot "http-representative.zip")

# marketplace_repo
$marketplaceDir = Join-Path $srcRoot "marketplace-representative"
New-Item -ItemType Directory -Path $marketplaceDir | Out-Null
Write-Utf8File (Join-Path $marketplaceDir "marketplace.json") @'
{
  "name": "representative-marketplace",
  "description": "Representative marketplace_repo package based on public skill marketplace conventions.",
  "plugins": [
    {
      "name": "public-marketplace-skill",
      "description": "Representative marketplace skill inspired by public marketplace repos.",
      "version": "1.0.0",
      "category": "development",
      "homepage": "https://github.com/DiversioTeam/agent-skills-marketplace",
      "source": "./public-marketplace-skill"
    }
  ]
}
'@
Write-Utf8File (Join-Path $marketplaceDir "public-marketplace-skill\SKILL.md") @'
---
name: public-marketplace-skill
description: Print a deterministic success payload to verify marketplace_repo execution. Inspired by public marketplace-style skill repositories.
---

# Public Marketplace Skill

This representative package is adapted for SkillHub import from the public marketplace-style skill ecosystem.
'@
Write-Utf8File (Join-Path $marketplaceDir "public-marketplace-skill\scripts\marketplace_ping.py") @'
#!/usr/bin/env python
from __future__ import annotations

import json


if __name__ == "__main__":
    print(json.dumps({"mode": "marketplace_repo", "status": "ok"}, ensure_ascii=False))
'@
New-ZipFromDirectory $marketplaceDir (Join-Path $zipRoot "marketplace-representative.zip")

# skill_repo_exec
$execRepoDir = Join-Path $srcRoot "skill-repo-exec-representative"
New-Item -ItemType Directory -Path $execRepoDir | Out-Null
Write-Utf8File (Join-Path $execRepoDir "skills-index.json") @'
{
  "name": "representative-skill-repo-exec",
  "description": "Representative executable skills repository based on public skill repo conventions.",
  "skills": [
    {
      "name": "public-exec-skill",
      "description": "Representative executable repo skill inspired by public skill repositories.",
      "source": "./public-exec-skill"
    }
  ]
}
'@
Write-Utf8File (Join-Path $execRepoDir "public-exec-skill\SKILL.md") @'
---
name: public-exec-skill
description: Print a deterministic success payload to verify skill_repo_exec execution. Inspired by public executable skill repositories.
---

# Public Exec Skill

This representative package is adapted for SkillHub import from the public skill repository ecosystem.
'@
Write-Utf8File (Join-Path $execRepoDir "public-exec-skill\scripts\exec_ping.py") @'
#!/usr/bin/env python
from __future__ import annotations

import json


if __name__ == "__main__":
    print(json.dumps({"mode": "skill_repo_exec", "status": "ok"}, ensure_ascii=False))
'@
New-ZipFromDirectory $execRepoDir (Join-Path $zipRoot "skill-repo-exec-representative.zip")

Get-ChildItem -Path $zipRoot | Select-Object Name, FullName
