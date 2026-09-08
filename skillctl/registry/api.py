"""REST API router — ``/api/v1`` endpoints.

Exposes CRUD operations on skills, search, content download, eval attachment,
token management, and health check as JSON endpoints.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import NoReturn

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile  # type: ignore[import-untyped]
from fastapi.responses import Response  # type: ignore[import-untyped]
from pydantic import BaseModel, Field  # type: ignore[import-untyped]

from skillctl.artifact import artifact_hash, build_minimal_artifact, inspect_artifact
from skillctl.errors import SkillctlError
from skillctl.manifest import ManifestLoader
from skillctl.registry.auth import AuthManager, validate_permissions
from skillctl.registry.db import MetadataDB, SkillRecord
from skillctl.registry.rbac.middleware import authorize, resolve_identity
from skillctl.registry.rbac.models import ROLE_PERMISSIONS, Identity, Permission, Role, role_from_str
from skillctl.registry.rbac.store import RBACStore
from skillctl.validator import SchemaValidator
from skillctl.version import __version__

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SkillSummary(BaseModel):
    name: str
    version: str
    description: str
    tags: list[str]
    eval_grade: str | None
    eval_score: float | None
    status: str
    created_at: str


class SkillDetail(BaseModel):
    name: str
    namespace: str
    version: str
    description: str
    content_hash: str
    artifact_hash: str | None
    tags: list[str]
    authors: list[dict]
    license: str | None
    eval_grade: str | None
    eval_score: float | None
    status: str
    manifest: dict
    versions: list[str]
    created_at: str


class SearchResponse(BaseModel):
    skills: list[SkillSummary]
    total: int
    limit: int
    offset: int


class EvalAttachment(BaseModel):
    grade: str = Field(pattern=r"^[A-F]$")
    score: float = Field(ge=0.0, le=100.0)


class TokenCreateRequest(BaseModel):
    name: str
    permissions: list[str]
    expires_in_days: int | None = None


class TokenCreateResponse(BaseModel):
    token: str
    token_id: str
    name: str
    permissions: list[str]
    expires_at: str | None


class HealthResponse(BaseModel):
    status: str
    version: str
    skills_count: int
    storage_status: str


class ErrorResponse(BaseModel):
    code: str
    what: str
    why: str
    fix: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_summary(r: SkillRecord) -> SkillSummary:
    return SkillSummary(
        name=r.name,
        version=r.version,
        description=r.description,
        tags=r.tags,
        eval_grade=r.eval_grade,
        eval_score=r.eval_score,
        status=r.status,
        created_at=r.created_at,
    )


def _record_to_detail(r: SkillRecord, versions: list[str]) -> SkillDetail:
    return SkillDetail(
        name=r.name,
        namespace=r.namespace,
        version=r.version,
        description=r.description,
        content_hash=r.content_hash,
        artifact_hash=r.artifact_hash,
        tags=r.tags,
        authors=r.authors,
        license=r.license,
        eval_grade=r.eval_grade,
        eval_score=r.eval_score,
        status=r.status,
        manifest=json.loads(r.manifest_json),
        versions=versions,
        created_at=r.created_at,
    )


def _error_response(status: int, code: str, what: str, why: str, fix: str) -> NoReturn:
    raise HTTPException(
        status_code=status,
        detail=ErrorResponse(code=code, what=what, why=why, fix=fix).model_dump(),
    )


_RBAC_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9-]+(?:/[a-z0-9-]+)*$")


def _validate_rbac_namespace(namespace: str) -> None:
    if not _RBAC_NAMESPACE_PATTERN.fullmatch(namespace):
        _error_response(
            400,
            "E_INVALID_NAMESPACE",
            f"Invalid RBAC namespace '{namespace}'",
            "Namespaces must contain slash-delimited lowercase alphanumeric or hyphen segments",
            "Use a namespace such as 'my-org' or 'org/acme/team-ml'.",
        )


def _authorize_record_read(request: Request, identity: Identity, record: SkillRecord) -> None:
    """Authorize a record read without exposing drafts to read-only users."""
    permission = Permission.SKILL_READ if record.status == "published" else Permission.SKILL_UPDATE
    decision = authorize(
        request,
        identity,
        permission,
        record.namespace,
        resource=f"{record.name}@{record.version}",
        raise_on_deny=False,
    )
    if not decision:
        if record.status == "draft":
            _error_response(
                404,
                "E_NOT_FOUND",
                f"Skill '{record.name}@{record.version}' not found",
                "No published skill with this name and version is visible",
                "Check the name and version, or authenticate with draft access.",
            )
        authorize(
            request,
            identity,
            permission,
            record.namespace,
            resource=f"{record.name}@{record.version}",
        )


def _get_skill_record_or_404(
    db: MetadataDB,
    name: str,
    version: str,
    *,
    fix: str = "Check the name and version",
) -> SkillRecord:
    """Resolve one immutable registry record with a consistent not-found response."""
    record = db.get_skill(name, version)
    if record is None:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{name}@{version}' not found",
            "No skill with this name and version exists",
            fix,
        )
    return record


def _authorize_record_action(
    request: Request,
    identity: Identity,
    record: SkillRecord,
    permission: Permission,
) -> None:
    """Authorize a mutation against the record's stored namespace."""
    authorize(
        request,
        identity,
        permission,
        record.namespace,
        resource=f"{record.name}@{record.version}",
    )


def _enforce_legacy_token_delegation(request: Request, identity: Identity, permissions: list[str]) -> None:
    """Prevent a legacy token grant from exceeding the caller's RBAC authority."""
    for legacy_permission in permissions:
        if legacy_permission == "admin":
            required = [(Permission.RBAC_ASSIGN, "*")]
        elif legacy_permission == "read":
            required = [(Permission.SKILL_READ, "*")]
        elif legacy_permission.startswith("read:"):
            required = [(Permission.SKILL_READ, legacy_permission.split(":", 1)[1])]
        else:
            namespace = legacy_permission.split(":", 1)[1]
            required = [(permission, namespace) for permission in ROLE_PERMISSIONS[Role.PUBLISHER]]

        for permission, namespace in required:
            decision = authorize(
                request,
                identity,
                permission,
                namespace,
                resource=f"legacy-token-grant:{legacy_permission}",
                raise_on_deny=False,
            )
            if not decision:
                _error_response(
                    403,
                    "E_TOKEN_ESCALATION",
                    f"Cannot delegate legacy permission '{legacy_permission}'",
                    f"The caller does not hold '{permission.value}' in namespace '{namespace}'",
                    "Request only permissions within your effective roles and namespace scopes.",
                )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

api_router = APIRouter(prefix="/api/v1")


# -- 6.7 Health check -------------------------------------------------------


@api_router.get("/health", response_model=HealthResponse)
async def health(request: Request):
    db: MetadataDB = request.app.state.db
    count = db.count_search()
    consistency = getattr(request.app.state, "storage_consistency", None)
    storage_status = consistency.status if consistency is not None else "unknown"
    status = "degraded" if storage_status == "degraded" else "ok"
    return HealthResponse(
        status=status,
        version=__version__,
        skills_count=count,
        storage_status=storage_status,
    )


# -- 6.1 Publish skill ------------------------------------------------------


@api_router.post("/skills", status_code=201, response_model=SkillDetail)
async def publish_skill(
    request: Request,
    manifest: str = Form(...),
    content: UploadFile = File(...),  # type: ignore[assignment]
    artifact: UploadFile | None = File(None),  # type: ignore[assignment]
    namespace: str | None = Form(None),
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit

    # Parse manifest JSON
    try:
        manifest_dict = json.loads(manifest)
    except json.JSONDecodeError as exc:
        _error_response(
            400,
            "E_INVALID_JSON",
            "Manifest is not valid JSON",
            str(exc),
            "Provide a valid JSON string in the manifest field",
        )

    # Validate manifest using ManifestLoader + SchemaValidator
    loader = ManifestLoader()
    try:
        parsed = loader._dict_to_manifest(manifest_dict)
    except Exception as exc:
        _error_response(
            400,
            "E_INVALID_MANIFEST",
            "Failed to parse manifest",
            str(exc),
            "Check manifest structure matches skill.yaml schema",
        )

    validator = SchemaValidator()
    result = validator.validate(parsed)
    if not result.valid:
        errors = [{"code": e.code, "message": e.message, "path": e.path, "hint": e.hint} for e in result.errors]
        _error_response(
            400, "E_VALIDATION", "Manifest validation failed", json.dumps(errors), "Fix the validation errors and retry"
        )

    # Remote registry names are always distribution-qualified. Local-only
    # operations may still use bare names.
    name_parts = parsed.metadata.name.split("/", 1)
    if len(name_parts) != 2:
        _error_response(
            400,
            "E_NO_NAMESPACE",
            f"Skill '{parsed.metadata.name}' has no distribution namespace",
            "Remote registries require namespaced skill names to prevent collisions",
            "Use metadata.name in the form '<namespace>/<skill>'.",
        )

    # The authorization namespace is immutable registry metadata. It may be
    # more specific than the distribution-name prefix.
    skill_segment = name_parts[0]
    rbac_namespace = namespace or skill_segment
    _validate_rbac_namespace(rbac_namespace)
    authorize(
        request,
        identity,
        Permission.SKILL_CREATE,
        rbac_namespace,
        resource=f"{parsed.metadata.name}@{parsed.metadata.version}",
    )

    # Check duplicate and prevent an artifact identity moving between RBAC
    # namespaces across versions.
    existing = db.get_skill(parsed.metadata.name, parsed.metadata.version)
    if existing is not None:
        _error_response(
            409,
            "E_ALREADY_EXISTS",
            f"Skill {parsed.metadata.name}@{parsed.metadata.version} already exists",
            "A skill with this name and version is already published",
            "Bump the version in your manifest and retry",
        )
    existing_versions = db.get_versions(parsed.metadata.name)
    if existing_versions and any(item.namespace != rbac_namespace for item in existing_versions):
        _error_response(
            409,
            "E_NAMESPACE_IMMUTABLE",
            f"Skill '{parsed.metadata.name}' is already bound to another RBAC namespace",
            "All versions of a skill must share one immutable authorization boundary",
            "Publish under the existing RBAC namespace or choose a different skill name.",
        )

    # Store blob (enforce 50 MB upload limit)
    max_size = 50 * 1024 * 1024
    content_bytes = await content.read(max_size + 1)
    if len(content_bytes) > max_size:
        _error_response(
            413,
            "E_TOO_LARGE",
            f"Upload exceeds maximum size of {max_size // (1024 * 1024)} MB",
            "Skill content files should be small text files",
            "Reduce the size of your SKILL.md and related content",
        )

    if artifact is None:
        artifact_bytes = build_minimal_artifact(parsed, content_bytes)
    else:
        artifact_bytes = await artifact.read(max_size + 1)
        if len(artifact_bytes) > max_size:
            _error_response(
                413,
                "E_TOO_LARGE",
                f"Artifact exceeds maximum size of {max_size // (1024 * 1024)} MB",
                "Complete artifact bundles have a bounded upload size",
                "Remove generated or unnecessary large files and rebuild the artifact",
            )
    try:
        inspect_artifact(
            artifact_bytes,
            expected_name=parsed.metadata.name,
            expected_version=parsed.metadata.version,
            expected_content=content_bytes,
        )
    except SkillctlError as exc:
        _error_response(400, exc.code, exc.what, exc.why, exc.fix)
    bundle_hash = artifact_hash(artifact_bytes)

    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        from datetime import datetime, timezone as _tz

        now = datetime.now(_tz.utc).isoformat()
        metadata = {
            "created_at": now,
            "updated_at": now,
            "eval_grade": None,
            "eval_score": None,
            "status": "draft",
            "rbac_namespace": rbac_namespace,
            "artifact_hash": bundle_hash,
        }
        content_hash = github_backend.store_skill(
            name=parsed.metadata.name,
            version=parsed.metadata.version,
            manifest_json=json.dumps(manifest_dict, indent=2),
            content=content_bytes,
            metadata=metadata,
            artifact=artifact_bytes,
        )
    else:
        content_hash = await storage.store_blob(content_bytes)
        stored_artifact_hash = await storage.store_blob(artifact_bytes)
        if stored_artifact_hash != bundle_hash:
            _error_response(
                500,
                "E_ARTIFACT_INTEGRITY",
                "Stored artifact digest does not match the verified upload",
                f"Expected {bundle_hash}, got {stored_artifact_hash}",
                "Check the configured storage backend for corruption",
            )

    # Insert metadata
    record = SkillRecord(
        id=None,
        name=parsed.metadata.name,
        namespace=rbac_namespace,
        version=parsed.metadata.version,
        description=parsed.metadata.description,
        content_hash=content_hash,
        artifact_hash=bundle_hash,
        tags=parsed.metadata.tags,
        authors=[{"name": a.name, "email": a.email} for a in parsed.metadata.authors],
        license=parsed.metadata.license,
        status="draft",
        manifest_json=json.dumps(manifest_dict),
    )
    try:
        db.insert_skill(record)
    except sqlite3.IntegrityError:
        # Clean up orphaned blob only if the winner has different content
        if github_backend is None:
            winner = db.get_skill(parsed.metadata.name, parsed.metadata.version)
            if winner and winner.content_hash != content_hash:
                try:
                    await storage.delete_blob(content_hash)
                except Exception:
                    pass
            if winner and winner.artifact_hash != bundle_hash:
                try:
                    await storage.delete_blob(bundle_hash)
                except Exception:
                    pass
        _error_response(
            409,
            "E_ALREADY_EXISTS",
            f"Skill {parsed.metadata.name}@{parsed.metadata.version} already exists",
            "A concurrent publish created this version first",
            "Bump the version in your manifest and retry",
        )

    # Audit log
    audit.log(
        action="skill.created",
        actor=identity.username,
        resource=f"{parsed.metadata.name}@{parsed.metadata.version}",
        details={
            "content_hash": content_hash,
            "artifact_hash": bundle_hash,
            "size": len(content_bytes),
            "artifact_size": len(artifact_bytes),
            "status": "draft",
            "namespace": rbac_namespace,
            "token_id": identity.token_id,
        },
    )

    # Return detail
    inserted = db.get_skill(parsed.metadata.name, parsed.metadata.version)
    if inserted is None:
        _error_response(
            500,
            "E_INTERNAL",
            "Failed to retrieve newly published skill",
            "Skill was inserted but could not be read back",
            "Retry the publish operation",
        )
    versions = [v.version for v in db.get_versions(parsed.metadata.name)]
    return _record_to_detail(inserted, versions)


# -- 6.2 List/search skills -------------------------------------------------


@api_router.get("/skills", response_model=SearchResponse)
async def list_skills(
    request: Request,
    q: str | None = None,
    namespace: str | None = None,
    tag: str | None = None,
    include_drafts: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db

    # Authorize read on the requested namespace (or globally when unfiltered).
    authorize(request, identity, Permission.SKILL_READ, namespace or "*")

    status = "published"
    if include_drafts:
        authorize(request, identity, Permission.SKILL_UPDATE, namespace or "*")
        status = None

    results = db.search(query=q, namespace=namespace, tag=tag, status=status, limit=limit, offset=offset)
    total = db.count_search(query=q, namespace=namespace, tag=tag, status=status)

    return SearchResponse(
        skills=[_record_to_summary(r) for r in results],
        total=total,
        limit=limit,
        offset=offset,
    )


# -- 6.3 Skill detail -------------------------------------------------------


@api_router.get("/skills/{namespace}/{name}", response_model=SkillDetail)
async def get_skill(
    request: Request,
    namespace: str,
    name: str,
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db

    full_name = f"{namespace}/{name}"
    versions_list = db.get_versions(full_name)
    if not versions_list:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{full_name}' not found",
            "No skill with this name exists",
            "Check the namespace and name",
        )

    # Authors may inspect drafts; read-only callers receive only published
    # versions and cannot infer the existence of a draft-only skill.
    draft_namespace = versions_list[0].namespace
    can_manage = authorize(
        request,
        identity,
        Permission.SKILL_UPDATE,
        draft_namespace,
        resource=full_name,
        raise_on_deny=False,
    )
    visible_versions = versions_list if can_manage else [v for v in versions_list if v.status == "published"]
    if not visible_versions:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{full_name}' not found",
            "No published versions are visible",
            "Check the namespace and name, or authenticate with draft access.",
        )
    record = visible_versions[0]
    _authorize_record_read(request, identity, record)
    version_strings = [v.version for v in visible_versions]
    return _record_to_detail(record, version_strings)


@api_router.get("/skills/{namespace}/{name}/{version}", response_model=SkillDetail)
async def get_skill_version(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{full_name}@{version}' not found",
            "No skill with this name and version exists",
            "Check the namespace, name, and version",
        )

    _authorize_record_read(request, identity, record)
    versions = db.get_versions(full_name)
    can_manage = authorize(
        request,
        identity,
        Permission.SKILL_UPDATE,
        record.namespace,
        resource=full_name,
        raise_on_deny=False,
    )
    version_strings = [v.version for v in versions if can_manage or v.status == "published"]
    return _record_to_detail(record, version_strings)


# -- 6.4 Content download ---------------------------------------------------


@api_router.get("/skills/{namespace}/{name}/{version}/content")
async def download_content(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{full_name}@{version}' not found",
            "No skill with this name and version exists",
            "Check the namespace, name, and version",
        )
    _authorize_record_read(request, identity, record)

    from skillctl.registry.storage import NotFoundError as BlobNotFound

    try:
        blob = await storage.get_blob(record.content_hash)
    except BlobNotFound:
        _error_response(
            404,
            "E_BLOB_MISSING",
            f"Content blob for '{full_name}@{version}' is missing from storage",
            "The blob may have been deleted or the storage is corrupted",
            "Re-publish the skill to restore its content",
        )

    # Detect content type from magic bytes for proper download
    media_type = "application/octet-stream"
    filename = f"{name}-{version}"
    if blob[:2] == b"PK":  # ZIP magic bytes
        media_type = "application/zip"
        filename += ".zip"
    elif blob[:2] == b"\x1f\x8b":  # gzip magic bytes
        media_type = "application/gzip"
        filename += ".tar.gz"
    else:
        # Assume single-file text content
        media_type = "text/markdown"
        filename += ".md"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=blob, media_type=media_type, headers=headers)


@api_router.get("/skills/{namespace}/{name}/{version}/artifact")
async def download_artifact(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    identity: Identity = Depends(resolve_identity),
):
    """Download the complete, immutable artifact bundle."""
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{full_name}@{version}' not found",
            "No skill with this name and version exists",
            "Check the namespace, name, and version",
        )
    _authorize_record_read(request, identity, record)

    github_backend = getattr(request.app.state, "github_backend", None)
    try:
        if github_backend is not None:
            bundle = github_backend.get_skill_artifact(full_name, version)
        elif record.artifact_hash:
            bundle = await storage.get_blob(record.artifact_hash)
        else:
            content = await storage.get_blob(record.content_hash)
            parsed = ManifestLoader()._dict_to_manifest(json.loads(record.manifest_json))
            bundle = build_minimal_artifact(parsed, content)
    except Exception as exc:
        from skillctl.registry.storage import NotFoundError as BlobNotFound

        if not isinstance(exc, BlobNotFound):
            raise
        _error_response(
            404,
            "E_BLOB_MISSING",
            f"Artifact blob for '{full_name}@{version}' is missing from storage",
            "The blob may have been deleted or the storage is corrupted",
            "Re-publish the skill to restore its complete artifact",
        )

    try:
        inspect_artifact(
            bundle,
            expected_name=full_name,
            expected_version=version,
        )
    except SkillctlError as exc:
        _error_response(500, exc.code, exc.what, exc.why, exc.fix)

    headers = {"Content-Disposition": f'attachment; filename="{name}-{version}.artifact.zip"'}
    return Response(content=bundle, media_type="application/vnd.skillctl.artifact.v1+zip", headers=headers)


# -- 6.5 Delete skill -------------------------------------------------------


@api_router.delete("/skills/{namespace}/{name}/{version}", status_code=204)
async def delete_skill(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit

    full_name = f"{namespace}/{name}"
    record = _get_skill_record_or_404(
        db,
        full_name,
        version,
        fix="Check the namespace, name, and version",
    )
    _authorize_record_action(request, identity, record, Permission.SKILL_DELETE)

    # Delete from DB first (so index is consistent even if blob delete fails)
    db.delete_skill(full_name, version)

    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        try:
            github_backend.delete_skill(full_name, version)
        except Exception:
            pass
    else:
        # Content-addressed blobs are shared across records and are deleted
        # only after their final reference disappears.
        other_content_refs = db.conn.execute(
            "SELECT COUNT(*) FROM skills WHERE content_hash = ?",
            (record.content_hash,),
        ).fetchone()[0]
        if other_content_refs == 0:
            try:
                await storage.delete_blob(record.content_hash)
            except Exception:
                pass
        if record.artifact_hash:
            other_artifact_refs = db.conn.execute(
                "SELECT COUNT(*) FROM skills WHERE artifact_hash = ?",
                (record.artifact_hash,),
            ).fetchone()[0]
            if other_artifact_refs == 0:
                try:
                    await storage.delete_blob(record.artifact_hash)
                except Exception:
                    pass

    # Audit log
    audit.log(
        action="skill.deleted",
        actor=identity.username,
        resource=f"{full_name}@{version}",
        details={
            "content_hash": record.content_hash,
            "artifact_hash": record.artifact_hash,
            "token_id": identity.token_id,
        },
    )

    return Response(status_code=204)


# -- 6.6 Attach eval --------------------------------------------------------


@api_router.put("/skills/{namespace}/{name}/{version}/eval", response_model=SkillDetail)
async def attach_eval(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    body: EvalAttachment,
    identity: Identity = Depends(resolve_identity),
):
    db: MetadataDB = request.app.state.db
    audit = request.app.state.audit

    full_name = f"{namespace}/{name}"
    record = _get_skill_record_or_404(
        db,
        full_name,
        version,
        fix="Check the namespace, name, and version",
    )
    _authorize_record_action(request, identity, record, Permission.SKILL_UPDATE)

    db.update_eval(full_name, version, body.grade, body.score)

    # Update GitHub metadata if using git backend
    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        from datetime import datetime, timezone as _tz

        try:
            github_backend.update_metadata(
                full_name,
                version,
                {
                    "eval_grade": body.grade,
                    "eval_score": body.score,
                    "updated_at": datetime.now(_tz.utc).isoformat(),
                },
            )
        except Exception:
            pass  # Non-fatal — SQLite is already updated

    # Audit log
    audit.log(
        action="eval.attached",
        actor=identity.username,
        resource=f"{full_name}@{version}",
        details={"grade": body.grade, "score": body.score, "token_id": identity.token_id},
    )

    updated = db.get_skill(full_name, version)
    if updated is None:
        _error_response(
            500,
            "E_INTERNAL",
            "Failed to retrieve updated skill",
            "Eval was attached but the skill could not be read back",
            "Retry the eval attachment",
        )
    version_strings = [v.version for v in db.get_versions(full_name)]
    return _record_to_detail(updated, version_strings)


# -- 6.8 Token management ---------------------------------------------------


@api_router.post("/tokens", status_code=201, response_model=TokenCreateResponse)
async def create_token(
    request: Request,
    body: TokenCreateRequest,
    identity: Identity = Depends(resolve_identity),
):
    auth_manager: AuthManager = request.app.state.auth_manager
    audit = request.app.state.audit

    authorize(request, identity, Permission.TOKEN_CREATE, "*", resource=f"token:{body.name}")

    try:
        # Validate strings before checking delegation so malformed values are
        # reported as a client error rather than causing parser ambiguity.
        validate_permissions(body.permissions)
        _enforce_legacy_token_delegation(request, identity, body.permissions)
        raw_token = auth_manager.create_token(
            name=body.name,
            permissions=body.permissions,
            expires_in_days=body.expires_in_days,
        )
    except ValueError as exc:
        _error_response(
            400,
            "E_INVALID_PERMISSION",
            "One or more permission strings are invalid",
            str(exc),
            "Use 'admin', 'read', 'read:<namespace>', or 'write:<namespace>'.",
        )

    # Look up the created token to get its ID and expiry from the DB directly
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    db: MetadataDB = request.app.state.db
    row = db.conn.execute(
        "SELECT id, expires_at FROM tokens WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()

    # Audit log
    audit.log(
        action="token.created",
        actor=identity.username,
        resource=f"token:{body.name}",
        details={"permissions": body.permissions, "token_id": identity.token_id},
    )

    return TokenCreateResponse(
        token=raw_token,
        token_id=row["id"],
        name=body.name,
        permissions=body.permissions,
        expires_at=row["expires_at"],
    )


# -- 6.9 Audit log read (admin) --------------------------------------------


class AuditEventResponse(BaseModel):
    timestamp: str
    action: str
    actor: str
    resource: str
    details: dict
    prev_signature: str
    hmac_signature: str


class AuditReadResponse(BaseModel):
    events: list[AuditEventResponse]
    integrity: dict  # {"valid": int, "invalid": int, "parse_errors": int}


@api_router.get("/audit", response_model=AuditReadResponse)
async def read_audit(
    request: Request,
    since: str | None = None,
    until: str | None = None,
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    identity: Identity = Depends(resolve_identity),
):
    """Return recent audit events.  Requires audit:read."""
    audit = request.app.state.audit

    authorize(request, identity, Permission.AUDIT_READ, "*")

    events = audit.read(since=since, until=until, action=action, limit=limit)
    valid, invalid, parse_errors = audit.verify_integrity()
    return AuditReadResponse(
        events=[
            AuditEventResponse(
                timestamp=e.timestamp,
                action=e.action,
                actor=e.actor,
                resource=e.resource,
                details=e.details,
                prev_signature=e.prev_signature,
                hmac_signature=e.hmac_signature,
            )
            for e in events
        ],
        integrity={"valid": valid, "invalid": invalid, "parse_errors": parse_errors},
    )


@api_router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    request: Request,
    token_id: str,
    identity: Identity = Depends(resolve_identity),
):
    auth_manager: AuthManager = request.app.state.auth_manager
    audit = request.app.state.audit

    # Self-revocation (a user revoking their own token) is always allowed;
    # revoking someone else's token requires token:revoke.
    owner_id = None
    try:
        row = request.app.state.db.conn.execute("SELECT user_id FROM tokens WHERE id = ?", (token_id,)).fetchone()
        owner_id = row["user_id"] if row else None
    except Exception:
        owner_id = None  # legacy schema without user_id column

    if owner_id is not None and owner_id == identity.user_id:
        audit.log(
            action="auth_decision",
            actor=identity.username,
            resource=f"token:{token_id}",
            details={
                "permission": Permission.TOKEN_REVOKE.value,
                "namespace": "*",
                "decision": "allowed",
                "reason": "self-revocation",
                "token_id": identity.token_id,
            },
        )
    else:
        authorize(request, identity, Permission.TOKEN_REVOKE, "*", resource=f"token:{token_id}")

    revoked = auth_manager.revoke_token(token_id)
    if not revoked:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Token '{token_id}' not found",
            "No active token with this ID exists",
            "Check the token ID",
        )

    # Audit log
    audit.log(
        action="token.revoked",
        actor=identity.username,
        resource=f"token:{token_id}",
        details={"token_id": identity.token_id},
    )

    return Response(status_code=204)


# ===========================================================================
# Milestone 1 — RBAC: publish/unpublish, auth, users, roles, namespaces
# ===========================================================================


def _require_store(request: Request) -> RBACStore:
    store = getattr(request.app.state, "rbac_store", None)
    if store is None:
        _error_response(
            501,
            "E_RBAC_DISABLED",
            "RBAC is not enabled on this registry",
            "No rbac_store is configured on the server",
            "Start the server with RBAC enabled (default) to use this endpoint",
        )
    return store


def _identity_payload(request: Request, identity: Identity) -> dict:
    store = getattr(request.app.state, "rbac_store", None)
    expires_at = None
    if store is not None and identity.token_id:
        row = request.app.state.db.conn.execute(
            "SELECT expires_at FROM tokens WHERE id = ?", (identity.token_id,)
        ).fetchone()
        if row:
            expires_at = row["expires_at"]
    return {
        "username": identity.username,
        "user_id": identity.user_id,
        "roles": [r.value for r in identity.roles],
        "namespaces": identity.namespaces,
        "token_id": identity.token_id,
        "token_expires_at": expires_at,
        "is_anonymous": identity.is_anonymous,
    }


# -- skill publish / unpublish (RBAC create/publish split) ------------------


class PublishRequest(BaseModel):
    name: str
    version: str
    namespace: str | None = None


@api_router.post("/skills/publish")
async def publish_skill_version(
    request: Request,
    body: PublishRequest,
    identity: Identity = Depends(resolve_identity),
):
    """Mark an existing (draft) skill version as published. Requires skill:publish."""
    db: MetadataDB = request.app.state.db
    audit = request.app.state.audit

    record = _get_skill_record_or_404(
        db,
        body.name,
        body.version,
        fix="Create it first via POST /skills",
    )
    _authorize_record_action(request, identity, record, Permission.SKILL_PUBLISH)
    if body.namespace is not None and body.namespace != record.namespace:
        _error_response(
            409,
            "E_NAMESPACE_IMMUTABLE",
            "Publish namespace does not match the artifact's stored namespace",
            "The authorization namespace is fixed when the first version is created",
            "Omit namespace or use the namespace returned by POST /skills.",
        )

    transition = db.transition_skill_status(
        body.name,
        body.version,
        expected="draft",
        target="published",
    )
    if transition == "missing":
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{body.name}@{body.version}' not found",
            "The skill was deleted during the publish request",
            "Create the version again before publishing it.",
        )
    if transition == "conflict":
        _error_response(
            409,
            "E_LIFECYCLE_CONFLICT",
            f"Cannot publish '{body.name}@{body.version}'",
            "The stored lifecycle state is not draft or published",
            "Inspect and repair the registry metadata before retrying.",
        )
    if transition == "already":
        return {
            "name": body.name,
            "version": body.version,
            "status": "published",
            "changed": False,
        }

    github_synced = True
    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        from datetime import datetime, timezone as _tz

        try:
            github_backend.update_metadata(
                body.name,
                body.version,
                {
                    "status": "published",
                    "rbac_namespace": record.namespace,
                    "updated_at": datetime.now(_tz.utc).isoformat(),
                },
            )
        except Exception:
            github_synced = False
    audit.log(
        action="skill.published",
        actor=identity.username,
        resource=f"{body.name}@{body.version}",
        details={
            "namespace": record.namespace,
            "token_id": identity.token_id,
            "github_metadata_synced": github_synced,
        },
    )
    return {
        "name": body.name,
        "version": body.version,
        "status": "published",
        "changed": True,
    }


@api_router.post("/skills/unpublish")
async def unpublish_skill_version(
    request: Request,
    body: PublishRequest,
    identity: Identity = Depends(resolve_identity),
):
    """Revert a published skill version to draft. Requires skill:unpublish."""
    db: MetadataDB = request.app.state.db
    audit = request.app.state.audit

    record = _get_skill_record_or_404(db, body.name, body.version)
    _authorize_record_action(request, identity, record, Permission.SKILL_UNPUBLISH)
    if body.namespace is not None and body.namespace != record.namespace:
        _error_response(
            409,
            "E_NAMESPACE_IMMUTABLE",
            "Unpublish namespace does not match the artifact's stored namespace",
            "The authorization namespace is fixed when the first version is created",
            "Omit namespace or use the namespace returned by POST /skills.",
        )

    transition = db.transition_skill_status(
        body.name,
        body.version,
        expected="published",
        target="draft",
    )
    if transition == "missing":
        _error_response(
            404,
            "E_NOT_FOUND",
            f"Skill '{body.name}@{body.version}' not found",
            "The skill was deleted during the unpublish request",
            "Check the name and version.",
        )
    if transition == "conflict":
        _error_response(
            409,
            "E_LIFECYCLE_CONFLICT",
            f"Cannot unpublish '{body.name}@{body.version}'",
            "The stored lifecycle state is not published or draft",
            "Inspect and repair the registry metadata before retrying.",
        )
    if transition == "already":
        return {
            "name": body.name,
            "version": body.version,
            "status": "draft",
            "changed": False,
        }

    github_synced = True
    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        from datetime import datetime, timezone as _tz

        try:
            github_backend.update_metadata(
                body.name,
                body.version,
                {
                    "status": "draft",
                    "rbac_namespace": record.namespace,
                    "updated_at": datetime.now(_tz.utc).isoformat(),
                },
            )
        except Exception:
            github_synced = False
    audit.log(
        action="skill.unpublished",
        actor=identity.username,
        resource=f"{body.name}@{body.version}",
        details={
            "namespace": record.namespace,
            "token_id": identity.token_id,
            "github_metadata_synced": github_synced,
        },
    )
    return {
        "name": body.name,
        "version": body.version,
        "status": "draft",
        "changed": True,
    }


# -- authentication ---------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str
    token_name: str = "login"
    expires_in_days: int | None = 30


@api_router.post("/auth/login")
async def auth_login(request: Request, body: LoginRequest):
    """Authenticate with username+password; mint an identity-bound token."""
    store = _require_store(request)
    audit = request.app.state.audit

    user = store.verify_user(body.username, body.password)
    if user is None:
        audit.log(action="auth.login_failed", actor=body.username, resource="auth", details={})
        _error_response(
            401,
            "E_AUTH_FAILED",
            "Invalid username or password",
            "Credentials did not match an active user",
            "Check your username and password",
        )

    raw_token, token_id = store.create_token(
        user["user_id"], name=body.token_name, scopes=["*"], expires_in_days=body.expires_in_days
    )
    assignments = store.get_assignments(user["user_id"])
    row = request.app.state.db.conn.execute("SELECT expires_at FROM tokens WHERE id = ?", (token_id,)).fetchone()
    audit.log(action="auth.login", actor=body.username, resource="auth", details={"token_id": token_id})
    return {
        "token": raw_token,
        "token_id": token_id,
        "user_id": user["user_id"],
        "username": user["username"],
        "roles": sorted({a.role.value for a in assignments}),
        "namespaces": sorted({a.namespace for a in assignments}),
        "expires_at": row["expires_at"] if row else None,
    }


@api_router.get("/auth/whoami")
async def auth_whoami(request: Request, identity: Identity = Depends(resolve_identity)):
    """Return the caller's resolved identity."""
    return _identity_payload(request, identity)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@api_router.post("/auth/change-password")
async def auth_change_password(
    request: Request,
    body: ChangePasswordRequest,
    identity: Identity = Depends(resolve_identity),
):
    """Change the authenticated user's password (verifies the current one)."""
    from skillctl.registry.rbac.store import verify_password

    store = _require_store(request)
    audit = request.app.state.audit
    if identity.is_anonymous or identity.user_id == "anonymous":
        _error_response(
            400,
            "E_NO_USER",
            "No user bound to this session",
            "Auth is disabled or the principal is anonymous",
            "Authenticate as a real user first",
        )
    user = store.get_user(identity.user_id)
    if user is None or not verify_password(body.old_password, user["password_hash"]):
        _error_response(
            401,
            "E_AUTH_FAILED",
            "Current password is incorrect",
            "The old_password did not match",
            "Re-enter your current password",
        )
    if not body.new_password:
        _error_response(
            400,
            "E_BAD_PASSWORD",
            "New password must not be empty",
            "Empty passwords are not allowed",
            "Choose a non-empty password",
        )
    store.set_password(identity.user_id, body.new_password)
    audit.log(action="auth.password_changed", actor=identity.username, resource="auth", details={})
    return {"changed": True}


class AuthTokenCreateRequest(BaseModel):
    name: str
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    expires_in_days: int | None = None
    expires_in_seconds: int | None = None  # fine-grained expiry (CI / tests)


@api_router.post("/auth/tokens", status_code=201)
async def auth_create_token(
    request: Request,
    body: AuthTokenCreateRequest,
    identity: Identity = Depends(resolve_identity),
):
    """Create a scoped, identity-bound token for the current user."""
    store = _require_store(request)
    audit = request.app.state.audit

    if identity.is_anonymous or identity.user_id == "anonymous":
        _error_response(
            400,
            "E_NO_USER",
            "Cannot mint a user token for an anonymous principal",
            "Auth is disabled or no user is bound",
            "Authenticate as a real user first",
        )
    # A self-minted token can only NARROW the user's privileges (the engine
    # re-checks roles at use time), so minting requires TOKEN_CREATE in any
    # namespace the user already holds — not global.
    engine = request.app.state.rbac_engine
    candidate_ns = identity.namespaces or ["*"]
    grant_ns = next((ns for ns in candidate_ns if engine.check(identity, Permission.TOKEN_CREATE, ns)), None)
    authorize(request, identity, Permission.TOKEN_CREATE, grant_ns or candidate_ns[0])

    if body.expires_in_seconds is not None:
        from datetime import datetime, timedelta, timezone as _tz

        expires_at = (datetime.now(_tz.utc) + timedelta(seconds=body.expires_in_seconds)).isoformat()
        raw_token, token_id = store.create_token_with_expiry_iso(
            identity.user_id, name=body.name, scopes=body.scopes, expires_at=expires_at
        )
    else:
        raw_token, token_id = store.create_token(
            identity.user_id, name=body.name, scopes=body.scopes, expires_in_days=body.expires_in_days
        )
    row = request.app.state.db.conn.execute("SELECT expires_at FROM tokens WHERE id = ?", (token_id,)).fetchone()
    audit.log(
        action="token.created",
        actor=identity.username,
        resource=f"token:{body.name}",
        details={"scopes": body.scopes, "token_id": token_id},
    )
    return {
        "token": raw_token,
        "token_id": token_id,
        "name": body.name,
        "scopes": body.scopes,
        "expires_at": row["expires_at"] if row else None,
    }


@api_router.get("/auth/tokens")
async def auth_list_tokens(request: Request, identity: Identity = Depends(resolve_identity)):
    """List the current user's tokens (no secrets)."""
    store = _require_store(request)
    if identity.is_anonymous:
        return {"tokens": []}
    toks = store.list_tokens(identity.user_id)
    return {
        "tokens": [
            {
                "token_id": t.token_id,
                "name": t.name,
                "scopes": t.scopes,
                "created_at": t.created_at,
                "expires_at": t.expires_at,
                "last_used_at": t.last_used_at,
                "revoked": t.revoked,
            }
            for t in toks
        ]
    }


# -- user + role + namespace administration ---------------------------------


class UserCreateRequest(BaseModel):
    username: str
    password: str


@api_router.post("/users", status_code=201)
async def create_user(request: Request, body: UserCreateRequest, identity: Identity = Depends(resolve_identity)):
    """Create a user. Requires rbac:assign (admin)."""
    store = _require_store(request)
    audit = request.app.state.audit
    authorize(request, identity, Permission.RBAC_ASSIGN, "*", resource=f"user:{body.username}")

    if store.get_user_by_username(body.username) is not None:
        _error_response(
            409,
            "E_USER_EXISTS",
            f"User '{body.username}' already exists",
            "A user with this username is already registered",
            "Choose a different username",
        )
    uid = store.create_user(body.username, body.password)
    audit.log(action="user.created", actor=identity.username, resource=f"user:{body.username}", details={})
    return {"user_id": uid, "username": body.username}


class AssignRequest(BaseModel):
    username: str
    role: str
    namespace: str
    expires_at: str | None = None


@api_router.post("/rbac/assign", status_code=201)
async def rbac_assign(request: Request, body: AssignRequest, identity: Identity = Depends(resolve_identity)):
    """Assign a role to a user within a namespace. Requires rbac:assign."""
    store = _require_store(request)
    audit = request.app.state.audit
    authorize(request, identity, Permission.RBAC_ASSIGN, body.namespace, resource=f"user:{body.username}")

    user = store.get_user_by_username(body.username)
    if user is None:
        _error_response(
            404,
            "E_NOT_FOUND",
            f"User '{body.username}' not found",
            "No such user",
            "Create the user first",
        )
    try:
        role = role_from_str(body.role)
    except ValueError as exc:
        _error_response(400, "E_INVALID_ROLE", "Invalid role", str(exc), "Use viewer, author, publisher, or admin")

    store.add_assignment(
        user["user_id"], role, body.namespace, assigned_by=identity.username, expires_at=body.expires_at
    )
    audit.log(
        action="rbac.assigned",
        actor=identity.username,
        resource=f"user:{body.username}",
        details={"role": body.role, "namespace": body.namespace},
    )
    return {"username": body.username, "role": body.role, "namespace": body.namespace}


@api_router.post("/rbac/revoke")
async def rbac_revoke(request: Request, body: AssignRequest, identity: Identity = Depends(resolve_identity)):
    """Revoke a role assignment. Requires rbac:revoke."""
    store = _require_store(request)
    audit = request.app.state.audit
    authorize(request, identity, Permission.RBAC_REVOKE, body.namespace, resource=f"user:{body.username}")

    user = store.get_user_by_username(body.username)
    if user is None:
        _error_response(404, "E_NOT_FOUND", f"User '{body.username}' not found", "No such user", "Check the username")
    try:
        role = role_from_str(body.role)
    except ValueError as exc:
        _error_response(400, "E_INVALID_ROLE", "Invalid role", str(exc), "Use a valid role")

    removed = store.remove_assignment(user["user_id"], role, body.namespace)
    audit.log(
        action="rbac.revoked",
        actor=identity.username,
        resource=f"user:{body.username}",
        details={"role": body.role, "namespace": body.namespace, "removed": removed},
    )
    return {"username": body.username, "role": body.role, "namespace": body.namespace, "removed": removed}


@api_router.get("/rbac/assignments")
async def rbac_list(request: Request, username: str, identity: Identity = Depends(resolve_identity)):
    """List a user's role assignments. Requires rbac:assign."""
    store = _require_store(request)
    authorize(request, identity, Permission.RBAC_ASSIGN, "*", resource=f"user:{username}")
    user = store.get_user_by_username(username)
    if user is None:
        _error_response(404, "E_NOT_FOUND", f"User '{username}' not found", "No such user", "Check the username")
    assignments = store.get_assignments(user["user_id"])
    return {
        "username": username,
        "assignments": [
            {"role": a.role.value, "namespace": a.namespace, "assigned_by": a.assigned_by, "expires_at": a.expires_at}
            for a in assignments
        ],
    }


class CheckRequest(BaseModel):
    username: str
    permission: str
    namespace: str


@api_router.post("/rbac/check")
async def rbac_check(request: Request, body: CheckRequest, identity: Identity = Depends(resolve_identity)):
    """Dry-run a permission check for a user (debugging). Requires rbac:assign."""
    from skillctl.registry.rbac.engine import RBACEngine
    from skillctl.registry.rbac.models import Identity as _Id, permission_from_str

    store = _require_store(request)
    authorize(request, identity, Permission.RBAC_ASSIGN, "*", resource=f"user:{body.username}")

    user = store.get_user_by_username(body.username)
    if user is None:
        _error_response(404, "E_NOT_FOUND", f"User '{body.username}' not found", "No such user", "Check the username")
    try:
        perm = permission_from_str(body.permission)
    except ValueError as exc:
        _error_response(400, "E_INVALID_PERMISSION", "Invalid permission", str(exc), "Use e.g. 'skill:publish'")

    engine: RBACEngine = request.app.state.rbac_engine
    decision = engine.check(_Id(user_id=user["user_id"], username=body.username), perm, body.namespace)
    return {"allowed": bool(decision), "reason": decision.reason}


class NamespaceCreateRequest(BaseModel):
    path: str
    description: str = ""


@api_router.post("/namespaces", status_code=201)
async def create_namespace(
    request: Request, body: NamespaceCreateRequest, identity: Identity = Depends(resolve_identity)
):
    """Create a namespace. Requires namespace:create on the parent (or global)."""
    store = _require_store(request)
    audit = request.app.state.audit
    parent = body.path.rsplit("/", 1)[0] if "/" in body.path else "*"
    authorize(request, identity, Permission.NAMESPACE_CREATE, parent, resource=f"namespace:{body.path}")

    if store.get_namespace(body.path) is not None:
        _error_response(
            409,
            "E_EXISTS",
            f"Namespace '{body.path}' already exists",
            "Already created",
            "Use a different path",
        )
    ns = store.create_namespace(body.path, owner_id=identity.user_id, description=body.description)
    audit.log(action="namespace.created", actor=identity.username, resource=f"namespace:{body.path}", details={})
    return {"path": ns.path, "parent": ns.parent, "description": ns.description}


@api_router.get("/namespaces")
async def list_namespaces(request: Request, identity: Identity = Depends(resolve_identity)):
    """List namespaces the caller can read."""
    store = _require_store(request)
    namespaces = store.list_namespaces()
    out = []
    for ns in namespaces:
        decision = request.app.state.rbac_engine.check(identity, Permission.SKILL_READ, ns.path)
        if decision:
            out.append({"path": ns.path, "parent": ns.parent, "description": ns.description})
    return {"namespaces": out}
