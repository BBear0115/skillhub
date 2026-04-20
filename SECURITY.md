# Security Policy

## Supported Scope

This repository exposes skill execution and MCP endpoints. Security-sensitive areas include:

- authentication and API key handling
- uploaded ZIP package parsing
- execution of `python_package` handlers
- workspace and team permission boundaries

## Reporting

Please do not open public issues for security vulnerabilities.

Report vulnerabilities privately to the maintainer of the repository and include:

- a clear description of the issue
- impact and affected area
- reproduction steps or proof of concept
- suggested mitigation if available

## Operational Notes

- Do not use the default `SECRET_KEY` in production.
- Prefer running production deployments with PostgreSQL rather than local SQLite.
- Treat uploaded ZIP skills as untrusted content until reviewed.
- Restrict access to `python_package` execution in higher-trust deployments.
