# skillctl vs AWS Agent Registry — Comparison Analysis

> **Methodology note:** AWS Agent Registry claims are sourced from the [AWS blog announcement](https://aws.amazon.com/blogs/machine-learning/the-future-of-managing-agents-at-scale-aws-agent-registry-now-in-preview/) (April 2026). Agent Registry is in preview — announced capabilities have not been independently verified. skillctl claims are verified against the codebase at commit `2a87506` (v0.1.0b1). Both products are pre-production.

## Overview

| | **skillctl** | **AWS Agent Registry** |
|---|---|---|
| **What it is** | Open-source CLI + self-hostable server for governing agent **skills** (SKILL.md instructions + manifests) | Managed AWS service for cataloging and discovering **agents, tools, MCP servers, and skills** across an enterprise |
| **Scope** | Skill lifecycle: author → validate → evaluate → optimize → publish → distribute | Agent/tool lifecycle: register → approve → discover → reuse → deprecate |
| **Status** | Beta (0.1.0b1), open source, MPL-2.0 | Preview, AWS managed service (5 regions) |
| **Runtime coupling** | Runtime-agnostic (Claude, GPT, Gemini, any SKILL.md-based agent) | Runtime-agnostic (agents built on any platform, including non-AWS) |
| **Cost** | Free (open source, self-hosted) | Pricing not yet announced (preview) |
| **Operational model** | Self-hosted (operator manages infra, backups, availability) | Fully managed by AWS |

---

## 1. What Agent Registry does that skillctl does not

### 1.1 Multi-resource-type catalog
Agent Registry manages **agents, tools, MCP servers, agent skills, and custom resources**. skillctl manages only **skills** (SKILL.md + skill.yaml pairs). Agent Registry is an enterprise catalog for the entire agentic landscape; skillctl is a governance tool for one artifact type. This is an architecturally significant difference — Agent Registry's scope is broader by design.

### 1.2 Semantic discovery
Agent Registry provides hybrid search combining keyword and semantic matching — a natural language query like "payment processing" surfaces tools tagged "billing." skillctl has FTS5 full-text search on name/description/tags, but no semantic/embedding-based search.

### 1.3 Protocol-native integration (MCP, A2A)
Agent Registry natively supports MCP and A2A protocol endpoints. It can auto-extract metadata from a running MCP server or A2A endpoint. skillctl has no protocol awareness — it stores skills as static files, not as live service endpoints.

### 1.4 IAM-based governance at enterprise scale
Agent Registry leverages AWS IAM for fine-grained access control, plus OAuth-based access for custom identity providers. skillctl has permission-scoped tokens with namespace-level access control and HMAC-signed audit logs. skillctl's auth system doesn't integrate with enterprise identity providers (no IAM, no OAuth).

### 1.5 Cross-cloud visibility
Agent Registry indexes agents "regardless of where they're built or hosted — on AWS, other cloud providers, or on premises." skillctl's registry is self-hosted and only knows about skills explicitly pushed to it.

### 1.6 Auto-indexing from deployment (roadmap)
Agent Registry's announced roadmap includes automatic indexing when agents are deployed. This is a future capability, not available in the current preview. skillctl requires explicit `skillctl apply` — there's no deployment-triggered registration.

### 1.7 MCP-compatible client access
Agent Registry is queryable from MCP-compatible clients (Kiro, Claude Code mentioned in the blog). Deeper IDE integration (search from IDE) is listed as a future direction. skillctl has no MCP server endpoint or IDE integration — it's CLI-only.

### 1.8 Multi-tenancy and scale
Agent Registry is a managed service presumably designed for enterprise-scale multi-account patterns via AWS Organizations. skillctl uses SQLite (single-writer) and filesystem storage — practical limits are in the low thousands of skills per instance, with no built-in multi-tenancy.

---

## 2. What skillctl does that Agent Registry does not

### 2.1 Security evaluation and grading
skillctl's eval suite scans skills for secrets, prompt injection, data exfiltration, unsafe deserialization, and more — producing an A-F grade on a 100-point scale across 9 threat categories. **Agent Registry has no evaluation, testing, or quality scoring mechanism.** This is the core differentiator.

### 2.2 Functional evaluation
skillctl runs skills against live agent runtimes, measures output quality with LLM-as-judge grading, computes outcome/process/style/efficiency scores, and compares with/without-skill baselines. Agent Registry has no functional testing capability.

### 2.3 Trigger evaluation
skillctl measures skill activation recall and specificity — whether a skill fires when it should and stays silent when it shouldn't. Agent Registry doesn't evaluate agent behavior.

### 2.4 Automated skill optimization
skillctl's optimizer runs an iterative loop: eval → LLM failure analysis → variant generation → re-eval → promotion. It automatically improves skill instructions using any LLM provider (via LiteLLM). Agent Registry has no optimization or improvement mechanism.

### 2.5 Security gate on publish
skillctl runs `scan_security` before remote publishing — skills with CRITICAL findings are blocked. Agent Registry has approval workflows but no automated security scanning.

### 2.6 Version diffing and regression detection
`skillctl diff ref_a ref_b` shows structural changes between two skill versions, including breaking change detection (removed parameters, capabilities). skillctl can also snapshot audit baselines and detect score regressions across versions. Agent Registry tracks versions but provides no diff or regression capabilities.

### 2.7 Schema validation
skillctl validates skill manifests against the `skillctl.io/v1` schema — checking semver, name format, parameter types, capability declarations, content references. Agent Registry supports custom metadata schemas but the blog does not describe a standard enforced schema.

### 2.8 Self-hosting
skillctl's registry server runs anywhere with Python 3.10+ (`skillctl serve`), stores data in SQLite + filesystem, and optionally syncs to GitHub. This is both an advantage (data residency control, no vendor lock-in, no cost) and a burden (operator manages availability, backups, security). Agent Registry is fully managed with no self-hosting option.

### 2.9 Content-addressed storage
skillctl stores actual skill content with SHA-256 hashing and integrity verification on pull. Agent Registry stores metadata records about resources, not the resources themselves.

---

## 3. Where they compete

These four capabilities overlap directly. An enterprise choosing both systems would need to decide which is the source of truth for each.

### 3.1 Skill/agent registration and metadata
Both provide structured metadata for skills/agents (name, version, description, ownership, capabilities). For the "single source of truth" use case, Agent Registry wins at enterprise scale; skillctl wins for developer-local workflows.

### 3.2 Discovery and search
Both offer search. Agent Registry has semantic search with superior recall; skillctl has FTS5 keyword search. For an enterprise with hundreds of skills, Agent Registry's discovery is materially better.

### 3.3 Governance
Both implement governance. Agent Registry uses IAM + approval states (draft → pending → discoverable). skillctl uses permission-scoped tokens + namespace scoping + HMAC-signed audit logs. For enterprises already on AWS, Agent Registry's IAM integration is a significant advantage — it leverages existing identity infrastructure.

### 3.4 Version management
Both track versions with deprecation support. skillctl adds diffing and regression detection on top.

---

## 4. Where they are complementary

### 4.1 skillctl as the quality gate, Agent Registry as the catalog

The strongest complementary pattern — skillctl handles what Agent Registry explicitly doesn't: evaluating whether a skill is *good enough* to register.

```
Author writes skill
    → skillctl validate (schema check)
    → skillctl eval audit (security scan, A-F grade)
    → skillctl eval functional (behavioral testing)
    → skillctl optimize (automated improvement)
    → skillctl apply (push to local store)
    → Register in Agent Registry (enterprise-wide discovery)
```

### 4.2 skillctl produces quality metadata that Agent Registry stores

Agent Registry stores metadata records — it knows a skill exists, who owns it, and how to invoke it. But it doesn't know if the skill is *safe* or *effective*. skillctl produces that data (grade, score, findings, test results), which Agent Registry can store as custom metadata fields. The eval results become a trust signal visible to anyone browsing the catalog.

### 4.3 Local development vs enterprise distribution

skillctl's local store is a developer's workbench — push, pull, diff, iterate. Agent Registry is the enterprise catalog — discover, approve, distribute. They serve different phases: skillctl for authoring and quality assurance, Agent Registry for organizational visibility.

### 4.4 Protocol bridge

Agent Registry speaks MCP natively. A skillctl MCP server could expose its registry to Agent Registry, making skillctl-managed skills discoverable through any MCP-compatible client without requiring custom API integration.

---

## 5. What "support for Agent Registry" would look like in skillctl

> **API verification:** The `bedrock-agentcore-control` boto3 service was inspected directly. All API operations below are confirmed to exist in the SDK as of April 2026. Agent Registry is in preview — APIs may change before GA.

### 5.1 `skillctl apply --agent-registry` (push to Agent Registry)

After a skill passes validation and eval, skillctl registers it in Agent Registry via `CreateRegistryRecord`. The API has a dedicated `AGENT_SKILLS` descriptor type with fields for SKILL.md content:

```python
client.create_registry_record(
    registryId="<registry-id>",
    name="my-org/code-reviewer",
    description="Reviews PRs for security issues",
    descriptorType="AGENT_SKILLS",
    descriptors={
        "agentSkills": {
            "skillMd": {"inlineContent": skill_md_content},
            "skillDefinition": {
                "schemaVersion": "skillctl.io/v1",
                "inlineContent": skill_yaml_content,
            },
        }
    },
    recordVersion="1.0.0",
)
```

**API confirmed:** `CreateRegistryRecord`, `UpdateRegistryRecord`, `DeleteRegistryRecord` all exist in `bedrock-agentcore-control`.

### 5.2 `skillctl search --agent-registry` (discover from Agent Registry)

skillctl queries Agent Registry via `ListRegistryRecords` with filters:

```python
client.list_registry_records(
    registryId="<registry-id>",
    descriptorType="AGENT_SKILLS",
    name="code-reviewer",  # optional filter
    status="APPROVED",
)
```

**API confirmed:** `ListRegistryRecords` supports filtering by `name`, `status`, and `descriptorType`.

### 5.3 Approval workflow integration

Agent Registry's lifecycle is confirmed: `DRAFT → PENDING_APPROVAL → APPROVED → REJECTED → DEPRECATED`. After `CreateRegistryRecord`, skillctl can:

1. Run eval and attach results to the record description
2. Call `SubmitRegistryRecordForApproval` to move it to pending
3. If the registry has `autoApproval: true`, it moves directly to `APPROVED`

```python
client.submit_registry_record_for_approval(
    registryId="<registry-id>",
    recordId="<record-id>",
)
```

**API confirmed:** `SubmitRegistryRecordForApproval` and `UpdateRegistryRecordStatus` both exist.

### 5.4 Implementation priority

| Priority | Feature | Effort | Value | Dependency risk |
|----------|---------|--------|-------|-----------------|
| **P0** | `skillctl apply --agent-registry` — push skill via `CreateRegistryRecord(AGENT_SKILLS)` | Medium | High | **None** — API confirmed in boto3 |
| **P1** | `skillctl search --agent-registry` — discover via `ListRegistryRecords` | Low | Medium | **None** — API confirmed |
| **P2** | Submit for approval after eval passes — `SubmitRegistryRecordForApproval` | Low | High | **None** — API confirmed |
| **P3** | Eval metadata in record description (grade, score, findings summary) | Low | Medium | **None** — description field exists |

---

## 6. Risks

### 6.1 Agent Registry adds evaluation capabilities
The entire complementary framing depends on Agent Registry lacking quality evaluation. If AWS adds security scanning, functional testing, or grading to Agent Registry (either natively or via Bedrock AgentCore), the integration value proposition weakens. skillctl's advantage would then narrow to: open-source portability, self-hosting, LLM-powered optimization, and deeper skill-specific analysis.

### 6.2 API instability in preview
Building integrations against a preview API carries the risk of breaking changes at GA. Integration work should target the MCP protocol (which is a standard, not an AWS-specific API) first.

### 6.3 Scale mismatch
skillctl's SQLite backend is designed for individual developers and small teams, not for enterprise-scale concurrent access. An integration where Agent Registry drives traffic to skillctl's API could expose scaling limits.

---

## Summary

skillctl and Agent Registry address different problems in the agent governance space. Agent Registry answers "what agents and skills exist across the organization?" — it's an **enterprise catalog**. skillctl answers "is this skill safe, correct, and good enough to deploy?" — it's a **quality workbench**.

They compete on registration, search, governance, and versioning. For enterprises already on AWS, Agent Registry's IAM integration and semantic search are superior for those overlapping capabilities.

They complement each other where Agent Registry has an explicit gap: **quality evaluation**. Agent Registry has no security scanning, no functional testing, no grading, no optimization. skillctl provides all of these.

The integration story is: **skillctl is the quality gate that feeds Agent Registry.** The most durable integration path is exposing skillctl as an MCP server, since Agent Registry already consumes MCP endpoints natively. This avoids dependency on unconfirmed preview APIs and uses an established protocol standard.
