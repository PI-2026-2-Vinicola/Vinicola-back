"""
Importação de dados reais: leituras históricas, cadastro de sensores e medições ambientais.

O mesmo arquivo passa duas vezes pela validação: na pré-visualização (nada é gravado)
e na confirmação — assim o servidor nunca confia em dados já "validados" pelo navegador.
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..deps import client_ip, require_roles
from ..models import ImportJob, User
from ..schemas import ImportJobOut, ImportKind, ImportPreviewOut, RowError
from ..services import imports as svc
from ..services.audit import audit
from ..services.timeutil import from_db

router = APIRouter(prefix="/imports", tags=["Importação"])
managers = require_roles("admin", "gestor")
log = logging.getLogger("oasis.imports")

KIND_LABEL = {"readings": "leituras", "sensors": "sensores", "environment": "medições ambientais"}


async def _read_file(file: UploadFile) -> tuple[str, bytes]:
    limit = get_settings().max_import_mb * 1_048_576
    data = await file.read(limit + 1)
    if not data:
        raise HTTPException(422, "O arquivo está vazio.")
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"O arquivo excede o limite de {get_settings().max_import_mb} MB.")
    name = (file.filename or "arquivo").replace("\\", "/").rsplit("/", 1)[-1][:200]
    return name, data


def _job_out(job: ImportJob, users: dict[int, str] | None = None) -> ImportJobOut:
    return ImportJobOut(
        id=job.id,
        kind=job.kind,  # type: ignore[arg-type]
        filename=job.filename,
        status=job.status,
        total_rows=job.total_rows,
        inserted=job.inserted,
        updated=job.updated,
        duplicates=job.duplicates,
        invalid=job.invalid,
        errors=[RowError(**e) for e in json.loads(job.errors_json or "[]")],
        created_by_name=(users or {}).get(job.created_by) if job.created_by else None,
        created_at=from_db(job.created_at),
    )


@router.get("/templates/{kind}")
def template(kind: ImportKind, _: User = Depends(managers)):
    """Modelo CSV com as colunas esperadas para cada tipo de importação."""
    return Response(
        content=("﻿" + svc.template_csv(kind)).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="oasis-modelo-{kind}.csv"'},
    )


@router.post("/preview", response_model=ImportPreviewOut, response_model_by_alias=True)
async def preview(kind: ImportKind = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db), _: User = Depends(managers)):
    """Valida o arquivo e mostra o que seria importado — nada é gravado."""
    name, data = await _read_file(file)
    try:
        result = svc.analyze_file(db, kind, name, data)
    except svc.ImportError_ as exc:
        raise HTTPException(422, str(exc))
    return ImportPreviewOut(
        kind=kind, filename=name, total=result.total, valid=len(result.valid), duplicates=len(result.duplicates),
        invalid=result.invalid_rows, columns=result.columns, sample=svc.sample(result.valid or result.duplicates), errors=[RowError(**e) for e in result.errors],
    )


@router.post("", response_model=ImportJobOut, response_model_by_alias=True, status_code=status.HTTP_201_CREATED)
async def run_import(
    request: Request,
    kind: ImportKind = Form(...),
    file: UploadFile = File(...),
    on_duplicate: Literal["skip", "update"] = Form(default="skip", alias="onDuplicate"),
    db: Session = Depends(get_db),
    user: User = Depends(managers),
):
    """Valida novamente e grava as linhas válidas em uma única transação. Toda execução fica registrada."""
    name, data = await _read_file(file)
    ip = client_ip(request)
    try:
        result = svc.analyze_file(db, kind, name, data)
    except svc.ImportError_ as exc:
        job = ImportJob(kind=kind, filename=name, status="falhou", errors_json=json.dumps([{"row": 0, "field": None, "message": str(exc)}], ensure_ascii=False), created_by=user.id)
        db.add(job)
        audit(db, "importacao_falhou", actor=user.email, target=name, details={"tipo": kind, "erro": str(exc)}, ip=ip)
        raise HTTPException(422, str(exc))

    try:
        inserted, updated = svc.persist(db, result, on_duplicate, user.id)
        db.flush()
    except IntegrityError:
        db.rollback()
        log.exception("Conflito ao gravar importação %s", name)
        raise HTTPException(status.HTTP_409_CONFLICT, "Os dados mudaram durante a importação (registros duplicados). Gere a pré-visualização novamente.")

    skipped = len(result.duplicates) - updated
    if inserted + updated == 0:
        job_status = "falhou" if result.invalid_rows else "sem_alteracoes"
    else:
        job_status = "parcial" if result.invalid_rows else "concluida"
    job = ImportJob(
        kind=kind, filename=name, status=job_status, total_rows=result.total, inserted=inserted, updated=updated,
        duplicates=skipped, invalid=result.invalid_rows, errors_json=json.dumps(result.errors, ensure_ascii=False), created_by=user.id,
    )
    db.add(job)
    audit(
        db, "importacao", actor=user.email, target=name, ip=ip,
        details={"tipo": KIND_LABEL[kind], "linhas": result.total, "inseridas": inserted, "atualizadas": updated, "duplicadas_ignoradas": skipped, "invalidas": result.invalid_rows},
    )
    db.refresh(job)
    return _job_out(job, {user.id: user.name})


@router.get("", response_model=list[ImportJobOut], response_model_by_alias=True)
def list_jobs(db: Session = Depends(get_db), _: User = Depends(managers)):
    jobs = db.query(ImportJob).order_by(ImportJob.created_at.desc(), ImportJob.id.desc()).limit(50).all()
    users = dict(db.query(User.id, User.name).filter(User.id.in_({j.created_by for j in jobs if j.created_by})).all())
    return [_job_out(j, users) for j in jobs]


@router.get("/{job_id}", response_model=ImportJobOut, response_model_by_alias=True)
def get_job(job_id: int, db: Session = Depends(get_db), _: User = Depends(managers)):
    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Importação não encontrada.")
    users = dict(db.query(User.id, User.name).filter(User.id == job.created_by).all()) if job.created_by else {}
    return _job_out(job, users)
