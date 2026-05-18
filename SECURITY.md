# Security Policy

`kgteach` is a local CLI and MCP tool that may read SGF files and invoke a local
KataGo binary. It should not require network access for normal analysis.

## Supported Versions

Security fixes target the latest release and the current `main` branch.

## Reporting a Vulnerability

Please report suspected vulnerabilities with GitHub Security Advisories when
available. If advisories are unavailable, open an issue with minimal public
detail and ask for a private follow-up channel.

Useful reports include:

- The affected command or MCP tool.
- Whether the issue requires a malicious SGF, malicious config, or local file
  access.
- Expected impact.
- Reproduction steps that avoid exposing private games or credentials.

## Scope

In scope:

- Unsafe file handling in SGF, config, cache, or daemon paths.
- Command behavior that leaks private local data.
- JSON contract violations that could mislead an agent into unsafe claims.
- MCP transport issues in the bundled local plugin.

Out of scope:

- KataGo engine vulnerabilities outside this repository.
- Problems caused by intentionally running untrusted third-party binaries.
- Social engineering, spam, or denial-of-service against GitHub-hosted project
  pages.
