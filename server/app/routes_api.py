from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

from .db import SessionLocal
from .models import Host, Container, HostStat, Command, now

api_bp = Blueprint("api", __name__)


def require_agent_token(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "missing bearer token"}), 401
        token = auth.removeprefix("Bearer ").strip()

        db = SessionLocal()
        host = db.query(Host).filter_by(agent_token=token).first()
        if host is None:
            return jsonify({"error": "invalid token"}), 401

        request.host_record = host
        return fn(*args, **kwargs)

    return wrapper


@api_bp.route("/report", methods=["POST"])
@require_agent_token
def report():
    payload = request.get_json(silent=True) or {}
    db = SessionLocal()
    host = request.host_record

    host_id = payload.get("host_id", host.id)
    host.last_seen = now()
    host.status = "online"

    stats = payload.get("host_stats") or {}
    if stats:
        db.add(
            HostStat(
                host_id=host_id,
                cpu_percent=stats.get("cpu_percent"),
                mem_used_mb=stats.get("mem_used_mb"),
                mem_total_mb=stats.get("mem_total_mb"),
                disk_used_gb=stats.get("disk_used_gb"),
                disk_total_gb=stats.get("disk_total_gb"),
                recorded_at=now(),
            )
        )

    seen_ids = set()
    for c in payload.get("containers", []):
        row_id = f"{host_id}:{c.get('id')}"
        seen_ids.add(row_id)
        existing = db.query(Container).get(row_id)
        ports = ",".join(c.get("ports", []))
        if existing:
            existing.name = c.get("name")
            existing.image = c.get("image")
            existing.status = c.get("status")
            existing.cpu_percent = c.get("cpu_percent")
            existing.mem_usage_mb = c.get("mem_usage_mb")
            existing.ports = ports
            existing.updated_at = now()
        else:
            db.add(
                Container(
                    id=row_id,
                    host_id=host_id,
                    container_id=c.get("id"),
                    name=c.get("name"),
                    image=c.get("image"),
                    status=c.get("status"),
                    cpu_percent=c.get("cpu_percent"),
                    mem_usage_mb=c.get("mem_usage_mb"),
                    ports=ports,
                    updated_at=now(),
                )
            )

    db.commit()
    return jsonify({"ok": True})


@api_bp.route("/commands", methods=["GET"])
@require_agent_token
def get_commands():
    db = SessionLocal()
    host_id = request.args.get("host_id", request.host_record.id)
    pending = (
        db.query(Command)
        .filter_by(host_id=host_id, status="pending")
        .order_by(Command.created_at.asc())
        .all()
    )
    return jsonify(
        [
            {"id": c.id, "action": c.action, "container_id": c.container_id}
            for c in pending
        ]
    )


@api_bp.route("/commands/<command_id>/ack", methods=["POST"])
@require_agent_token
def ack_command(command_id):
    db = SessionLocal()
    payload = request.get_json(silent=True) or {}
    cmd = db.query(Command).get(command_id)
    if cmd is None or cmd.host_id != request.host_record.id:
        return jsonify({"error": "not found"}), 404

    cmd.status = payload.get("status", "success")
    cmd.result = payload.get("output")
    cmd.acked_at = now()
    db.commit()
    return jsonify({"ok": True})
