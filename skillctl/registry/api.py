"""REST API router — ``/api/v1`` endpoints.

Exposes CRUD operations on skills, search, content download, eval attachment,
token management, and health check as JSON endpoints.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from skillctl.manifest import ManifestLoader
from skillctl.registry.auth import AuthManager, TokenInfo, get_auth_manager, get_current_token
from skillctl.registry.db import MetadataDB, SkillRecord
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
    created_at: str


class SkillDetail(BaseModel):
    name: str
    namespace: str
    version: str
    description: str
    content_hash: str
    tags: list[str]
    authors: list[dict]
    license: str | None
    eval_grade: str | None
    eval_score: float | None
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
        created_at=r.created_at,
    )


def _record_to_detail(r: SkillRecord, versions: list[str]) -> SkillDetail:
    return SkillDetail(
        name=r.name,
        namespace=r.namespace,
        version=r.version,
        description=r.description,
        content_hash=r.content_hash,
        tags=r.tags,
        authors=r.authors,
        license=r.license,
        eval_grade=r.eval_grade,
        eval_score=r.eval_score,
        manifest=json.loads(r.manifest_json),
        versions=versions,
        created_at=r.created_at,
    )


def _error_response(status: int, code: str, what: str, why: str, fix: str):
    raise HTTPException(
        status_code=status,
        detail=ErrorResponse(code=code, what=what, why=why, fix=fix).model_dump(),
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
    return HealthResponse(status="ok", version=__version__, skills_count=count)


# -- 6.1 Publish skill ------------------------------------------------------

@api_router.post("/skills", status_code=201, response_model=SkillDetail)
async def publish_skill(
    request: Request,
    manifest: str = Form(...),
    content: UploadFile = ...,
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit
    auth_manager: AuthManager = request.app.state.auth_manager

    # Parse manifest JSON
    try:
        manifest_dict = json.loads(manifest)
    except json.JSONDecodeError as exc:
        _error_response(400, "E_INVALID_JSON", "Manifest is not valid JSON",
                        str(exc), "Provide a valid JSON string in the manifest field")

    # Validate manifest using ManifestLoader + SchemaValidator
    loader = ManifestLoader()
    try:
        parsed = loader._dict_to_manifest(manifest_dict)
    except Exception as exc:
        _error_response(400, "E_INVALID_MANIFEST", "Failed to parse manifest",
                        str(exc), "Check manifest structure matches skill.yaml schema")

    validator = SchemaValidator()
    result = validator.validate(parsed)
    if not result.valid:
        errors = [{"code": e.code, "message": e.message, "path": e.path, "hint": e.hint}
                  for e in result.errors]
        _error_response(400, "E_VALIDATION", "Manifest validation failed",
                        json.dumps(errors), "Fix the validation errors and retry")

    # Check auth: token needs write:<namespace> or admin
    namespace = parsed.metadata.name.split("/")[0]
    if not auth_manager.check_permission(token, "write", namespace):
        _error_response(403, "E_FORBIDDEN",
                        f"Insufficient permissions for namespace '{namespace}'",
                        "Token lacks write scope for this namespace",
                        f"Use a token with 'write:{namespace}' or 'admin' permission")

    # Check duplicate
    existing = db.get_skill(parsed.metadata.name, parsed.metadata.version)
    if existing is not None:
        _error_response(409, "E_ALREADY_EXISTS",
                        f"Skill {parsed.metadata.name}@{parsed.metadata.version} already exists",
                        "A skill with this name and version is already published",
                        "Bump the version in your manifest and retry")

    # Store blob
    content_bytes = await content.read()
    content_hash = await storage.store_blob(content_bytes)

    # Insert metadata
    record = SkillRecord(
        id=None,
        name=parsed.metadata.name,
        namespace=namespace,
        version=parsed.metadata.version,
        description=parsed.metadata.description,
        content_hash=content_hash,
        tags=parsed.metadata.tags,
        authors=[{"name": a.name, "email": a.email} for a in parsed.metadata.authors],
        license=parsed.metadata.license,
        manifest_json=json.dumps(manifest_dict),
    )
    db.insert_skill(record)

    # Audit log
    audit.log(
        action="skill.published",
        actor=token.name,
        resource=f"{parsed.metadata.name}@{parsed.metadata.version}",
        details={"content_hash": content_hash, "size": len(content_bytes)},
    )

    # Return detail
    inserted = db.get_skill(parsed.metadata.name, parsed.metadata.version)
    versions = [v.version for v in db.get_versions(parsed.metadata.name)]
    return _record_to_detail(inserted, versions)


# -- 6.2 List/search skills -------------------------------------------------

@api_router.get("/skills", response_model=SearchResponse)
async def list_skills(
    request: Request,
    q: str | None = None,
    namespace: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "read"):
        _error_response(403, "E_FORBIDDEN", "Insufficient permissions",
                        "Token lacks read scope", "Use a token with 'read' permission")

    results = db.search(query=q, namespace=namespace, tag=tag, limit=limit, offset=offset)
    total = db.count_search(query=q, namespace=namespace, tag=tag)

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
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "read"):
        _error_response(403, "E_FORBIDDEN", "Insufficient permissions",
                        "Token lacks read scope", "Use a token with 'read' permission")

    full_name = f"{namespace}/{name}"
    versions_list = db.get_versions(full_name)
    if not versions_list:
        _error_response(404, "E_NOT_FOUND", f"Skill '{full_name}' not found",
                        "No skill with this name exists", "Check the namespace and name")

    # Return latest version
    record = versions_list[0]
    version_strings = [v.version for v in versions_list]
    return _record_to_detail(record, version_strings)


@api_router.get("/skills/{namespace}/{name}/{version}", response_model=SkillDetail)
async def get_skill_version(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "read"):
        _error_response(403, "E_FORBIDDEN", "Insufficient permissions",
                        "Token lacks read scope", "Use a token with 'read' permission")

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(404, "E_NOT_FOUND",
                        f"Skill '{full_name}@{version}' not found",
                        "No skill with this name and version exists",
                        "Check the namespace, name, and version")

    version_strings = [v.version for v in db.get_versions(full_name)]
    return _record_to_detail(record, version_strings)


# -- 6.4 Content download ---------------------------------------------------

@api_router.get("/skills/{namespace}/{name}/{version}/content")
async def download_content(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "read"):
        _error_response(403, "E_FORBIDDEN", "Insufficient permissions",
                        "Token lacks read scope", "Use a token with 'read' permission")

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(404, "E_NOT_FOUND",
                        f"Skill '{full_name}@{version}' not found",
                        "No skill with this name and version exists",
                        "Check the namespace, name, and version")

    blob = await storage.get_blob(record.content_hash)

    # Detect content type from magic bytes for proper download
    media_type = "application/octet-stream"
    filename = f"{name}-{version}"
    if blob[:2] == b'PK':  # ZIP magic bytes
        media_type = "application/zip"
        filename += ".zip"
    elif blob[:2] == b'\x1f\x8b':  # gzip magic bytes
        media_type = "application/gzip"
        filename += ".tar.gz"
    else:
        # Assume single-file text content
        media_type = "text/markdown"
        filename += ".md"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=blob, media_type=media_type, headers=headers)


# -- 6.5 Delete skill -------------------------------------------------------

@api_router.delete("/skills/{namespace}/{name}/{version}", status_code=204)
async def delete_skill(
    request: Request,
    namespace: str,
    name: str,
    version: str,
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "write", namespace):
        _error_response(403, "E_FORBIDDEN",
                        f"Insufficient permissions for namespace '{namespace}'",
                        "Token lacks write scope for this namespace",
                        f"Use a token with 'write:{namespace}' or 'admin' permission")

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(404, "E_NOT_FOUND",
                        f"Skill '{full_name}@{version}' not found",
                        "No skill with this name and version exists",
                        "Check the namespace, name, and version")

    # Delete from storage and db
    try:
        await storage.delete_blob(record.content_hash)
    except Exception:
        pass  # Blob may already be gone
    db.delete_skill(full_name, version)

    # Audit log
    audit.log(
        action="skill.deleted",
        actor=token.name,
        resource=f"{full_name}@{version}",
        details={"content_hash": record.content_hash},
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
    token: TokenInfo = Depends(get_current_token),
):
    db: MetadataDB = request.app.state.db
    audit = request.app.state.audit
    auth_manager: AuthManager = request.app.state.auth_manager

    if not auth_manager.check_permission(token, "write", namespace):
        _error_response(403, "E_FORBIDDEN",
                        f"Insufficient permissions for namespace '{namespace}'",
                        "Token lacks write scope for this namespace",
                        f"Use a token with 'write:{namespace}' or 'admin' permission")

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        _error_response(404, "E_NOT_FOUND",
                        f"Skill '{full_name}@{version}' not found",
                        "No skill with this name and version exists",
                        "Check the namespace, name, and version")

    db.update_eval(full_name, version, body.grade, body.score)

    # Audit log
    audit.log(
        action="eval.attached",
        actor=token.name,
        resource=f"{full_name}@{version}",
        details={"grade": body.grade, "score": body.score},
    )

    updated = db.get_skill(full_name, version)
    version_strings = [v.version for v in db.get_versions(full_name)]
    return _record_to_detail(updated, version_strings)


# -- 6.8 Token management ---------------------------------------------------

@api_router.post("/tokens", status_code=201, response_model=TokenCreateResponse)
async def create_token(
    request: Request,
    body: TokenCreateRequest,
    token: TokenInfo = Depends(get_current_token),
):
    auth_manager: AuthManager = request.app.state.auth_manager
    audit = request.app.state.audit

    if not auth_manager.check_permission(token, "admin"):
        _error_response(403, "E_FORBIDDEN", "Admin permission required",
                        "Token lacks admin scope",
                        "Use a token with 'admin' permission")

    raw_token = auth_manager.create_token(
        name=body.name,
        permissions=body.permissions,
        expires_in_days=body.expires_in_days,
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
        actor=token.name,
        resource=f"token:{body.name}",
        details={"permissions": body.permissions},
    )

    return TokenCreateResponse(
        token=raw_token,
        token_id=row["id"],
        name=body.name,
        permissions=body.permissions,
        expires_at=row["expires_at"],
    )


@api_router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    request: Request,
    token_id: str,
    token: TokenInfo = Depends(get_current_token),
):
    auth_manager: AuthManager = request.app.state.auth_manager
    audit = request.app.state.audit

    if not auth_manager.check_permission(token, "admin"):
        _error_response(403, "E_FORBIDDEN", "Admin permission required",
                        "Token lacks admin scope",
                        "Use a token with 'admin' permission")

    revoked = auth_manager.revoke_token(token_id)
    if not revoked:
        _error_response(404, "E_NOT_FOUND", f"Token '{token_id}' not found",
                        "No active token with this ID exists",
                        "Check the token ID")

    # Audit log
    audit.log(
        action="token.revoked",
        actor=token.name,
        resource=f"token:{token_id}",
        details={},
    )

    return Response(status_code=204)
