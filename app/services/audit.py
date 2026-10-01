import json
import logging

from sqlalchemy.orm import Session

from ..models import AuditLog

log = logging.getLogger("oasis.audit")


def audit(db: Session, action: str, *, actor: str | None = None, target: str | None = None, details: dict | str | None = None, ip: str | None = None, commit: bool = True) -> None:
    text = json.dumps(details, ensure_ascii=False, default=str) if isinstance(details, dict) else details
    db.add(AuditLog(action=action, actor=actor, target=target, details=text, ip=ip))
    log.info("%s actor=%s target=%s %s", action, actor, target, text or "")
    if commit:
        db.commit()
