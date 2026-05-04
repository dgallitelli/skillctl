---
name: dependency-scanner
description: Guides agents through auditing project dependencies for known vulnerabilities, outdated packages, and license compliance issues
skillctl:
  version: 2.0.0
  category: security
  tags:
    - security
    - dependencies
    - vulnerabilities
    - audit
  capabilities:
    - read_file
    - exec
    - network_access
---

# Dependency Scanner

Audit project dependencies for known vulnerabilities, outdated packages, and license risks.

## When to activate

Activate when the user asks to check dependencies, audit packages, review supply chain security, or before a release.

Do NOT activate for application-level code review, infrastructure configuration, or runtime security hardening.

## Scanning workflow

### 1. Detect package manager

Identify the project's dependency files:

| File | Ecosystem | Audit command |
|------|-----------|---------------|
| `package.json` / `package-lock.json` | npm | `npm audit --json` |
| `requirements.txt` / `pyproject.toml` | pip | `pip-audit --format=json` or `safety check --json` |
| `Gemfile.lock` | Ruby | `bundle audit check` |
| `go.sum` | Go | `govulncheck ./...` |
| `Cargo.lock` | Rust | `cargo audit --json` |
| `pom.xml` / `build.gradle` | Java | `mvn dependency-check:check` |

If multiple ecosystems are present, scan each one separately and combine the results.

### 2. Run vulnerability scan

Execute the appropriate audit command and parse the output. For each vulnerability found, extract:

- **Package name** and installed version
- **CVE identifier** (e.g., CVE-2024-38996)
- **Severity**: critical, high, medium, or low (use CVSS score when available)
- **Fixed version** (if a patch exists)
- **Advisory URL** from the National Vulnerability Database at https://nvd.nist.gov/ or the GitHub Advisory Database at https://github.com/advisories

Cross-reference findings against the OWASP Dependency-Check project guidance at https://owasp.org/www-project-dependency-check/ for additional context on severity and exploitability.

### 3. Check for outdated packages

Run the ecosystem's outdated command to identify packages behind their latest release:

```bash
npm outdated --json
pip list --outdated --format=json
bundle outdated --strict
go list -m -u all
```

Flag packages more than one major version behind as high priority.

### 4. License compliance

Scan declared licenses for compatibility issues:

- Flag `GPL-3.0` dependencies in proprietary projects.
- Flag `AGPL-*` dependencies in SaaS applications.
- Flag dependencies with no declared license.
- Warn on `WTFPL` or `Beerware` for corporate use.

### 5. Generate report

Produce a structured summary:

```
## Dependency Audit Report

### Critical vulnerabilities (action required)
- <package>@<version> — <CVE> — <description> — upgrade to <fixed-version>

### Outdated packages
- <package>: <current> → <latest> (<major/minor/patch> behind)

### License warnings
- <package>: <license> — <reason for flag>

### Summary
- X critical, Y high, Z medium, W low vulnerabilities
- N packages outdated (M major versions behind)
- L license issues
```

## Remediation guidance

For each critical or high vulnerability:
1. Check if a patched version exists and is compatible with the project's version constraints.
2. If a direct upgrade is possible, provide the exact command: `npm install package@version` or add the pinned version to requirements.
3. If the vulnerable package is a transitive dependency, identify the direct dependency that pulls it in and recommend upgrading that instead.
4. If no fix is available, suggest mitigation: pinning, alternative packages, or runtime controls.
