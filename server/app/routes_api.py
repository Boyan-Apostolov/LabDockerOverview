from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

from .db import SessionLocal
from .models import Host, Container, HostStat, Command, Image, Volume, Network, now

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

    seen_container_ids = set()
    for c in payload.get("containers", []):
        row_id = f"{host_id}:{c.get('id')}"
        seen_container_ids.add(row_id)
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
    # containers absent from this report (e.g. `docker rm`'d since the last cycle) are gone for
    # good, unlike a stopped-but-still-present container which is reported with status="exited"
    db.query(Container).filter(
        Container.host_id == host_id, ~Container.id.in_(seen_container_ids or [""])
    ).delete(synchronize_session=False)

    seen_image_ids = set()
    for img in payload.get("images", []):
        row_id = f"{host_id}:{img.get('id')}"
        seen_image_ids.add(row_id)
        existing = db.query(Image).get(row_id)
        tags = ",".join(img.get("tags", []))
        if existing:
            existing.tags = tags
            existing.size_mb = img.get("size_mb")
            existing.dangling = 1 if img.get("dangling") else 0
            existing.updated_at = now()
        else:
            db.add(
                Image(
                    id=row_id,
                    host_id=host_id,
                    image_id=img.get("id"),
                    tags=tags,
                    size_mb=img.get("size_mb"),
                    dangling=1 if img.get("dangling") else 0,
                    updated_at=now(),
                )
            )
    db.query(Image).filter(Image.host_id == host_id, ~Image.id.in_(seen_image_ids or [""])).delete(
        synchronize_session=False
    )

    seen_volume_ids = set()
    for v in payload.get("volumes", []):
        row_id = f"{host_id}:{v.get('name')}"
        seen_volume_ids.add(row_id)
        used_by = ",".join(v.get("used_by", []))
        existing = db.query(Volume).get(row_id)
        if existing:
            existing.driver = v.get("driver")
            existing.size_mb = v.get("size_mb")
            existing.used_by = used_by
            existing.docker_created_at = v.get("created_at")
            existing.updated_at = now()
        else:
            db.add(
                Volume(
                    id=row_id,
                    host_id=host_id,
                    name=v.get("name"),
                    driver=v.get("driver"),
                    size_mb=v.get("size_mb"),
                    used_by=used_by,
                    docker_created_at=v.get("created_at"),
                    updated_at=now(),
                )
            )
    db.query(Volume).filter(Volume.host_id == host_id, ~Volume.id.in_(seen_volume_ids or [""])).delete(
        synchronize_session=False
    )

    seen_network_ids = set()
    for n in payload.get("networks", []):
        row_id = f"{host_id}:{n.get('name')}"
        seen_network_ids.add(row_id)
        existing = db.query(Network).get(row_id)
        if existing:
            existing.driver = n.get("driver")
            existing.scope = n.get("scope")
            existing.updated_at = now()
        else:
            db.add(
                Network(
                    id=row_id,
                    host_id=host_id,
                    name=n.get("name"),
                    driver=n.get("driver"),
                    scope=n.get("scope"),
                    updated_at=now(),
                )
            )
    db.query(Network).filter(Network.host_id == host_id, ~Network.id.in_(seen_network_ids or [""])).delete(
        synchronize_session=False
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
            {"id": c.id, "action": c.action, "container_id": c.container_id, "payload": c.payload}
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
