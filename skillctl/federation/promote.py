"""Multi-registry federation (Milestone 4).

Promote a skill version from one registry to another (dev → staging → prod) with
a role gate (enforced by the target registry's RBAC) and an optional compliance
gate. Works over HTTP clients so it is testable against in-process registries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


@dataclass
class PromotionResult:
    promoted: bool
    reason: str
    name: str
    version: str
    target_namespace: str
    create_status: Optional[int] = None
    publish_status: Optional[int] = None


class FederationError(Exception):
    pass


def _split_ref(name: str) -> tuple[str, str]:
    ns, _, skill = name.partition("/")
    return ns, skill


def promote_skill(
    *,
    source_client,
    target_client,
    name: str,
    version: str,
    target_namespace: str,
    require_compliance: bool = False,
    compliance_ok: bool = True,
    compliance_reason: str = "",
) -> PromotionResult:
    """Pull ``name@version`` from *source_client* and publish it via *target_client*.

    The target registry's RBAC enforces *who* may publish/promote into
    ``target_namespace``. The compliance gate (``require_compliance``) blocks
    promotion to higher environments unless ``compliance_ok`` is True.
    """
    if require_compliance and not compliance_ok:
        return PromotionResult(
            promoted=False,
            reason=f"Compliance gate failed: {compliance_reason or 'report not passing'}",
            name=name,
            version=version,
            target_namespace=target_namespace,
        )

    ns, skill = _split_ref(name)

    # 1. Pull manifest + content from the source registry.
    detail = source_client.get(f"/api/v1/skills/{ns}/{skill}/{version}")
    if detail.status_code != 200:
        raise FederationError(f"Source skill {name}@{version} not found ({detail.status_code})")
    manifest = detail.json().get("manifest", {})
    content_resp = source_client.get(f"/api/v1/skills/{ns}/{skill}/{version}/content")
    if content_resp.status_code != 200:
        raise FederationError(f"Source content for {name}@{version} missing ({content_resp.status_code})")
    content = content_resp.content

    # 2. Create on the target registry (RBAC: requires skill:create).
    create = target_client.post(
        "/api/v1/skills",
        data={"manifest": json.dumps(manifest), "namespace": target_namespace},
        files={"content": ("SKILL.md", content, "application/octet-stream")},
    )
    if create.status_code == 403:
        return PromotionResult(
            promoted=False,
            reason="RBAC denied create on target",
            name=name,
            version=version,
            target_namespace=target_namespace,
            create_status=403,
        )
    if create.status_code not in (201, 409):  # 409 = already present, treat as idempotent
        raise FederationError(f"Create on target failed ({create.status_code}): {create.text}")

    # 3. Publish on the target registry (RBAC: requires skill:publish).
    publish = target_client.post(
        "/api/v1/skills/publish",
        json={"name": name, "version": version, "namespace": target_namespace},
    )
    if publish.status_code == 403:
        return PromotionResult(
            promoted=False,
            reason="RBAC denied publish on target",
            name=name,
            version=version,
            target_namespace=target_namespace,
            create_status=create.status_code,
            publish_status=403,
        )
    if publish.status_code != 200:
        raise FederationError(f"Publish on target failed ({publish.status_code}): {publish.text}")

    return PromotionResult(
        promoted=True,
        reason="promoted",
        name=name,
        version=version,
        target_namespace=target_namespace,
        create_status=create.status_code,
        publish_status=publish.status_code,
    )
