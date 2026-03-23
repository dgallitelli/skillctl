"""Web UI router — server-rendered HTML pages with CRUD + optimize support.

Serves the browse page, skill detail pages, publish form, optimize form,
and htmx partials using Jinja2 templates with Pico CSS styling.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse

from skillctl.manifest import ManifestLoader
from skillctl.registry.db import MetadataDB, SkillRecord
from skillctl.validator import SchemaValidator

web_router = APIRouter()


def _get_github_user(request: Request) -> dict:
    """Get GitHub user info if a token is configured. Returns dict with user info or empty."""
    config = getattr(request.app.state, "registry_config", None)
    if not config or not config.github_token:
        return {}
    # Cache on app.state to avoid hitting the API on every request
    cached = getattr(request.app.state, "_github_user_cache", None)
    if cached is not None:
        return cached
    try:
        from skillctl.github_auth import verify_token
        user = verify_token(config.github_token)
        info = {
            "github_user": user.get("login", ""),
            "github_name": user.get("name", ""),
            "github_avatar": user.get("avatar_url", ""),
        }
    except SystemExit:
        info = {}
    request.app.state._github_user_cache = info
    return info


def _base_context(request: Request) -> dict:
    """Common template context for all pages."""
    return _get_github_user(request)


def _render(request: Request, template: str, context: dict, status_code: int = 200):
    """Render a template with base context merged in."""
    templates = request.app.state.templates
    merged = {**_base_context(request), **context}
    return templates.TemplateResponse(request, template, merged, status_code=status_code)


# ---------------------------------------------------------------------------
# Browse / Search
# ---------------------------------------------------------------------------

@web_router.get("/", response_class=HTMLResponse)
async def index(request: Request, q: str | None = None, namespace: str | None = None, tag: str | None = None, flash: str | None = None):
    db: MetadataDB = request.app.state.db
    templates = request.app.state.templates
    skills = db.search(query=q, namespace=namespace, tag=tag, limit=50, offset=0)
    has_filters = bool(q or namespace or tag)
    return templates.TemplateResponse(request, "index.html", {
        **_base_context(request),
        "skills": skills, "query": q, "namespace": namespace, "tag": tag,
        "has_filters": has_filters,
        **({"flash_message": flash} if flash else {}),
    })


@web_router.get("/skills", response_class=HTMLResponse)
async def skills_search(request: Request, q: str | None = None, namespace: str | None = None, tag: str | None = None):
    if request.headers.get("HX-Request"):
        db: MetadataDB = request.app.state.db
        templates = request.app.state.templates
        skills = db.search(query=q, namespace=namespace, tag=tag, limit=50, offset=0)
        has_filters = bool(q or namespace or tag)
        return templates.TemplateResponse(request, "_skill_list.html", {**_base_context(request), "skills": skills, "has_filters": has_filters})

    params = []
    if q:
        params.append(f"q={q}")
    if namespace:
        params.append(f"namespace={namespace}")
    if tag:
        params.append(f"tag={tag}")
    url = "/?" + "&".join(params) if params else "/"
    return RedirectResponse(url=url, status_code=302)


# ---------------------------------------------------------------------------
# Skill Detail
# ---------------------------------------------------------------------------

@web_router.get("/skills/{namespace}/{name}", response_class=HTMLResponse)
async def skill_detail(request: Request, namespace: str, name: str):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    versions = db.get_versions(full_name)
    if not versions:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    skill = versions[0]
    info = await _load_content_info(storage, skill.content_hash)

    flash = request.query_params.get("flash")
    return templates.TemplateResponse(request, "skill_detail.html", {
        **_base_context(request),
        "skill": skill, "versions": versions, **info,
        **({"flash_message": flash} if flash else {}),
    })


@web_router.get("/skills/{namespace}/{name}/{version}", response_class=HTMLResponse)
async def skill_version_detail(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    skill = db.get_skill(full_name, version)
    if skill is None:
        return _render(request, "404.html", {"message": "Skill version not found"}, status_code=404)

    versions = db.get_versions(full_name)
    info = await _load_content_info(storage, skill.content_hash)

    flash = request.query_params.get("flash")
    return templates.TemplateResponse(request, "skill_detail.html", {
        **_base_context(request),
        "skill": skill, "versions": versions, **info,
        **({"flash_message": flash} if flash else {}),
    })


# ---------------------------------------------------------------------------
# Publish (Create)
# ---------------------------------------------------------------------------

@web_router.get("/publish", response_class=HTMLResponse)
async def publish_form(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "publish.html", {**_base_context(request), "form": {}, "error": None})


@web_router.post("/publish", response_class=HTMLResponse)
async def publish_submit(
    request: Request,
    name: str = Form(...),
    version: str = Form(...),
    description: str = Form(""),
    tags: str = Form(""),
    license: str = Form(""),
    author: str = Form(""),
    content_file: UploadFile | None = File(None),
    content_text: str = Form(""),
):
    templates = request.app.state.templates
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit

    form_data = dict(name=name, version=version, description=description,
                     tags=tags, license=license, author=author, content_text=content_text)

    # Resolve content
    content_bytes = b""
    original_filename = None
    if content_file and content_file.filename:
        content_bytes = await content_file.read()
        original_filename = content_file.filename
    elif content_text.strip():
        content_bytes = content_text.strip().encode()
    else:
        return templates.TemplateResponse(request, "publish.html", {
            "form": form_data, "error": "Provide either a file upload or paste content.",
        }, status_code=400)

    # Determine content type
    is_archive = False
    if original_filename:
        lower = original_filename.lower()
        is_archive = lower.endswith('.zip') or lower.endswith('.tar.gz') or lower.endswith('.tgz')

    # Build manifest dict
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    authors_list = [{"name": author.strip()}] if author.strip() else []
    content_spec = {"inline": "uploaded"}
    if is_archive:
        content_spec = {"inline": "archive", "archive": original_filename}
    manifest_dict = {
        "apiVersion": "skillctl.io/v1",
        "kind": "Skill",
        "metadata": {
            "name": name.strip(),
            "version": version.strip(),
            "description": description.strip(),
            "tags": tag_list,
            "authors": authors_list,
            **({"license": license.strip()} if license.strip() else {}),
        },
        "spec": {"content": content_spec},
    }

    # Validate
    loader = ManifestLoader()
    try:
        parsed = loader._dict_to_manifest(manifest_dict)
    except Exception as exc:
        return templates.TemplateResponse(request, "publish.html", {
            "form": form_data, "error": f"Invalid manifest: {exc}",
        }, status_code=400)

    validator = SchemaValidator()
    result = validator.validate(parsed)
    if not result.valid:
        msgs = "; ".join(e.message for e in result.errors)
        return templates.TemplateResponse(request, "publish.html", {
            "form": form_data, "error": f"Validation failed: {msgs}",
        }, status_code=400)

    # Check duplicate
    namespace = name.strip().split("/")[0]
    if db.get_skill(name.strip(), version.strip()):
        return templates.TemplateResponse(request, "publish.html", {
            "form": form_data, "error": f"{name}@{version} already exists. Bump the version.",
        }, status_code=409)

    # Store
    github_backend = getattr(request.app.state, "github_backend", None)
    if github_backend is not None:
        from datetime import datetime, timezone as _tz
        now = _tz.utc
        now_str = datetime.now(now).isoformat()
        metadata = {"created_at": now_str, "updated_at": now_str, "eval_grade": None, "eval_score": None}
        content_hash = github_backend.store_skill(
            name=name.strip(), version=version.strip(),
            manifest_json=json.dumps(manifest_dict, indent=2),
            content=content_bytes, metadata=metadata,
        )
    else:
        content_hash = await storage.store_blob(content_bytes)

    record = SkillRecord(
        id=None, name=name.strip(), namespace=namespace,
        version=version.strip(), description=description.strip(),
        content_hash=content_hash, tags=tag_list, authors=authors_list,
        license=license.strip() or None, manifest_json=json.dumps(manifest_dict),
    )
    db.insert_skill(record)

    audit.log(
        action="skill.published", actor="web-ui",
        resource=f"{name.strip()}@{version.strip()}",
        details={"content_hash": content_hash, "size": len(content_bytes)},
    )

    skill_name_part = name.strip().split("/")[1]
    return RedirectResponse(url=f"/skills/{namespace}/{skill_name_part}?flash=Skill+published+successfully", status_code=303)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@web_router.delete("/web/skills/{namespace}/{name}/{version}")
async def delete_skill_web(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    try:
        await storage.delete_blob(record.content_hash)
    except Exception:
        pass
    db.delete_skill(full_name, version)

    audit.log(
        action="skill.deleted", actor="web-ui",
        resource=f"{full_name}@{version}",
        details={"content_hash": record.content_hash},
    )

    # If more versions remain, redirect to skill page; otherwise go home
    remaining = db.get_versions(full_name)
    if remaining:
        return RedirectResponse(url=f"/skills/{namespace}/{name}?flash=Skill+deleted", status_code=303)
    return RedirectResponse(url="/?flash=Skill+deleted", status_code=303)


# ---------------------------------------------------------------------------
# Eval (Update)
# ---------------------------------------------------------------------------

@web_router.put("/web/skills/{namespace}/{name}/{version}/eval")
async def update_eval_web(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    audit = request.app.state.audit

    form = await request.form()
    grade = form.get("grade", "").strip()
    score_str = form.get("score", "").strip()

    full_name = f"{namespace}/{name}"
    record = db.get_skill(full_name, version)
    if record is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    if not grade or grade not in "ABCDF":
        return RedirectResponse(url=f"/skills/{namespace}/{name}/{version}", status_code=303)

    try:
        score = float(score_str)
        score = max(0.0, min(100.0, score))
    except (ValueError, TypeError):
        return RedirectResponse(url=f"/skills/{namespace}/{name}/{version}", status_code=303)

    db.update_eval(full_name, version, grade, score)

    audit.log(
        action="eval.attached", actor="web-ui",
        resource=f"{full_name}@{version}",
        details={"grade": grade, "score": score},
    )

    return RedirectResponse(url=f"/skills/{namespace}/{name}/{version}?flash=Eval+score+saved", status_code=303)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _load_preview(storage, content_hash: str) -> str | None:
    try:
        blob = await storage.get_blob(content_hash)
        return blob.decode("utf-8", errors="replace")[:2000]
    except Exception:
        return None


def _detect_archive(blob: bytes) -> tuple[bool, list[str]]:
    """Detect if blob is an archive and list its files."""
    import io
    if blob[:2] == b'PK':  # ZIP
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                return True, sorted(zf.namelist())
        except Exception:
            return True, []
    elif blob[:2] == b'\x1f\x8b':  # gzip / tar.gz
        import tarfile
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tf:
                return True, sorted(tf.getnames())
        except Exception:
            return True, []
    return False, []


def _extract_skill_md(blob: bytes) -> str | None:
    """Extract SKILL.md content from an archive, or None if not found."""
    import io
    if blob[:2] == b'PK':
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for name in zf.namelist():
                    if name.endswith("SKILL.md") or name == "SKILL.md":
                        return zf.read(name).decode("utf-8", errors="replace")
        except Exception:
            pass
    elif blob[:2] == b'\x1f\x8b':
        import tarfile
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as tf:
                for member in tf.getmembers():
                    if member.name.endswith("SKILL.md") or member.name == "SKILL.md":
                        f = tf.extractfile(member)
                        if f:
                            return f.read().decode("utf-8", errors="replace")
        except Exception:
            pass
    return None


async def _load_content_info(storage, content_hash: str) -> dict:
    """Load content and return preview info (handles both text and archives)."""
    try:
        blob = await storage.get_blob(content_hash)
    except Exception:
        return {"content_preview": None, "is_archive": False, "archive_files": []}

    is_archive, archive_files = _detect_archive(blob)
    if is_archive:
        skill_md = _extract_skill_md(blob)
        return {
            "content_preview": skill_md[:2000] if skill_md else None,
            "is_archive": True,
            "archive_files": archive_files,
        }

    preview = blob.decode("utf-8", errors="replace")[:2000]
    if preview.count("\ufffd") > 10:
        preview = None
    return {"content_preview": preview, "is_archive": False, "archive_files": []}


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------

@web_router.get("/skills/{namespace}/{name}/{version}/evaluate", response_class=HTMLResponse)
async def evaluate_form(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    skill = db.get_skill(full_name, version)
    if skill is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    return templates.TemplateResponse(request, "evaluate.html", {
        "skill": skill, "error": None, "report": None,
        "evals_json": "", "trigger_queries_json": "",
    })


@web_router.post("/skills/{namespace}/{name}/{version}/evaluate", response_class=HTMLResponse)
async def evaluate_submit(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    audit = request.app.state.audit
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    skill = db.get_skill(full_name, version)
    if skill is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    form = await request.form()
    include_audit = bool(form.get("include_audit"))
    include_functional = bool(form.get("include_functional"))
    include_trigger = bool(form.get("include_trigger"))
    dry_run = bool(form.get("dry_run"))
    timeout = int(form.get("timeout", 120))
    agent = form.get("agent", "claude").strip()
    evals_json_raw = form.get("evals_json", "").strip()
    trigger_queries_raw = form.get("trigger_queries_json", "").strip()

    # Write skill content to a temp directory
    try:
        blob = await storage.get_blob(skill.content_hash)
    except Exception as exc:
        return templates.TemplateResponse(request, "evaluate.html", {
            "skill": skill, "error": f"Could not load skill content: {exc}", "report": None,
        }, status_code=500)

    import yaml as _yaml
    tmp_dir = Path(tempfile.mkdtemp(prefix="skillctl-eval-"))
    (tmp_dir / "SKILL.md").write_bytes(blob)

    manifest = json.loads(skill.manifest_json)
    manifest.setdefault("spec", {})["content"] = {"path": "./SKILL.md"}
    (tmp_dir / "skill.yaml").write_text(
        _yaml.dump(manifest, default_flow_style=False)
    )

    # Write eval test data if provided
    evals_dir = tmp_dir / "evals"
    if evals_json_raw:
        try:
            json.loads(evals_json_raw)  # validate JSON
            evals_dir.mkdir(exist_ok=True)
            (evals_dir / "evals.json").write_text(evals_json_raw)
        except json.JSONDecodeError:
            return templates.TemplateResponse(request, "evaluate.html", {
                "skill": skill, "error": "Invalid JSON in eval cases field.", "report": None,
                "evals_json": evals_json_raw, "trigger_queries_json": trigger_queries_raw,
            }, status_code=400)

    if trigger_queries_raw:
        try:
            json.loads(trigger_queries_raw)  # validate JSON
            evals_dir.mkdir(exist_ok=True)
            (evals_dir / "eval_queries.json").write_text(trigger_queries_raw)
        except json.JSONDecodeError:
            return templates.TemplateResponse(request, "evaluate.html", {
                "skill": skill, "error": "Invalid JSON in trigger queries field.", "report": None,
                "evals_json": evals_json_raw, "trigger_queries_json": trigger_queries_raw,
            }, status_code=400)

    # Run unified report
    from skillctl.eval.unified_report import run_unified_report
    import io, contextlib

    report_output = tmp_dir / "evals" / "report.json"

    try:
        # Capture stdout so it doesn't leak into the response
        with contextlib.redirect_stdout(io.StringIO()):
            run_unified_report(
                str(tmp_dir),
                format="json",
                output_path=str(report_output),
                include_audit=include_audit,
                include_functional=include_functional,
                include_trigger=include_trigger,
                dry_run=dry_run,
                timeout=timeout,
                agent=agent,
            )
    except Exception as exc:
        return templates.TemplateResponse(request, "evaluate.html", {
            "skill": skill, "error": f"Evaluation failed: {exc}", "report": None,
        }, status_code=500)

    # Read the report JSON
    try:
        report_data = json.loads(report_output.read_text())
    except Exception:
        return templates.TemplateResponse(request, "evaluate.html", {
            "skill": skill, "error": "Could not read evaluation report.", "report": None,
        }, status_code=500)

    # Auto-update the skill's eval grade/score in the registry
    if report_data.get("overall_grade") and report_data.get("overall_score") is not None:
        score_100 = round(report_data["overall_score"] * 100, 1)
        db.update_eval(full_name, version, report_data["overall_grade"], score_100)
        audit.log(
            action="eval.attached", actor="web-ui",
            resource=f"{full_name}@{version}",
            details={"grade": report_data["overall_grade"], "score": score_100, "source": "unified_report"},
        )

    return templates.TemplateResponse(request, "evaluate.html", {
        "skill": skill, "error": None, "report": report_data,
    })


# ---------------------------------------------------------------------------
# Optimize
# ---------------------------------------------------------------------------

@web_router.get("/skills/{namespace}/{name}/{version}/optimize", response_class=HTMLResponse)
async def optimize_form(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    skill = db.get_skill(full_name, version)
    if skill is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    return templates.TemplateResponse(request, "optimize.html", {
        "skill": skill, "error": None, "result": None,
    })


@web_router.post("/skills/{namespace}/{name}/{version}/optimize", response_class=HTMLResponse)
async def optimize_submit(request: Request, namespace: str, name: str, version: str):
    db: MetadataDB = request.app.state.db
    storage = request.app.state.storage
    templates = request.app.state.templates

    full_name = f"{namespace}/{name}"
    skill = db.get_skill(full_name, version)
    if skill is None:
        return _render(request, "404.html", {"message": "Skill not found"}, status_code=404)

    form = await request.form()

    # Extract config from form
    try:
        num_variants = int(form.get("variants", 3))
        threshold = float(form.get("threshold", 0.05))
        max_iterations = int(form.get("max_iterations", 50))
        plateau = int(form.get("plateau", 3))
        budget = float(form.get("budget", 10.0))
    except (ValueError, TypeError) as exc:
        return templates.TemplateResponse(request, "optimize.html", {
            "skill": skill, "error": f"Invalid parameter: {exc}", "result": None,
        }, status_code=400)

    provider = form.get("provider", "bedrock").strip()
    model = form.get("model", "").strip() or None
    region = form.get("region", "us-east-1").strip()
    agent = form.get("agent", "claude").strip()
    dry_run = bool(form.get("dry_run"))

    # Write skill content to a temp directory so the optimizer can find it
    try:
        blob = await storage.get_blob(skill.content_hash)
    except Exception as exc:
        return templates.TemplateResponse(request, "optimize.html", {
            "skill": skill, "error": f"Could not load skill content: {exc}", "result": None,
        }, status_code=500)

    tmp_dir = Path(tempfile.mkdtemp(prefix="skillctl-opt-"))
    skill_md = tmp_dir / "SKILL.md"
    skill_md.write_bytes(blob)

    # Build a minimal skill.yaml so the optimizer can load it
    manifest = json.loads(skill.manifest_json)
    manifest.setdefault("spec", {})["content"] = {"path": "./SKILL.md"}
    (tmp_dir / "skill.yaml").write_text(
        __import__("yaml").dump(manifest, default_flow_style=False)
    )

    # Run optimization (synchronous — blocks the request)
    from skillctl.optimize.loop import run_optimization
    from skillctl.optimize.types import OptimizeConfig

    if model is None:
        model = ("claude-sonnet-4-20250514" if provider == "anthropic"
                 else "us.anthropic.claude-sonnet-4p6-v1:0")

    config = OptimizeConfig(
        skill_path=str(tmp_dir),
        num_variants=num_variants,
        threshold=threshold,
        max_iterations=max_iterations,
        plateau_limit=plateau,
        budget_usd=budget,
        provider=provider,
        model=model,
        aws_region=region,
        approve=not dry_run,
        dry_run=dry_run,
        timeout=120,
        agent=agent,
    )

    try:
        run = run_optimization(config)
    except Exception as exc:
        return templates.TemplateResponse(request, "optimize.html", {
            "skill": skill, "error": f"Optimization failed: {exc}", "result": None,
        }, status_code=500)

    return templates.TemplateResponse(request, "optimize.html", {
        "skill": skill, "error": None, "result": run,
    })


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@web_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    db: MetadataDB = request.app.state.db
    config = getattr(request.app.state, "registry_config", None)
    skills_count = db.count_search()

    ctx = {
        **_base_context(request),
        "config": config,
        "skills_count": skills_count,
    }
    return _render(request, "settings.html", ctx)
