from flask import has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog


def record(action: str, entity_type: str, entity_id=None, details=None, actor=None) -> AuditLog:
    if actor is None and has_request_context() and current_user.is_authenticated:
        actor = current_user
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details_json=details or {},
        ip_address=request.remote_addr if has_request_context() else None,
        user_agent=request.user_agent.string[:512] if has_request_context() else None,
    )
    db.session.add(entry)
    return entry
