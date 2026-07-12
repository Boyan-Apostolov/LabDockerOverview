import os
import re
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory

from .db import SessionLocal
from .models import Host, Container, HostStat, Command, now

dashboard_bp = Blueprint("dashboard", __name__)

_SCRIPTS_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),  # local dev: server/app -> repo root/scripts
    os.path.join(os.path.dirname(__file__), "..", "scripts"),  # docker image: /app/app -> /app/scripts
]
SCRIPTS_DIR = next((p for p in _SCRIPTS_CANDIDATES if os.path.isdir(p)), _SCRIPTS_CANDIDATES[0])


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "host"


@dashboard_bp.route("/install-agent.sh")
def install_agent_script():
    return send_from_directory(SCRIPTS_DIR, "install-agent.sh", mimetype="text/x-shellscript")


@dashboard_bp.route("/")
def hosts():
    db = SessionLocal()
    host_rows = db.query(Host).order_by(Host.name.asc()).all()

    hosts_data = []
    for host in host_rows:
        latest_stat = (
            db.query(HostStat)
            .filter_by(host_id=host.id)
            .order_by(HostStat.recorded_at.desc())
            .first()
        )
        container_count = db.query(Container).filter_by(host_id=host.id).count()
        hosts_data.append(
            {
                "host": host,
                "stat": latest_stat,
                "container_count": container_count,
            }
        )

    return render_template("hosts.html", hosts_data=hosts_data)


@dashboard_bp.route("/hosts/<host_id>")
def host_detail(host_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)
    containers = (
        db.query(Container)
        .filter_by(host_id=host_id)
        .order_by(Container.name.asc())
        .all()
    )
    return render_template("host_detail.html", host=host, containers=containers)


@dashboard_bp.route("/hosts/<host_id>/containers/<container_id>/commands/<action>", methods=["POST"])
def enqueue_command(host_id, container_id, action):
    if action not in ("restart", "stop"):
        return redirect(url_for("dashboard.host_detail", host_id=host_id))

    db = SessionLocal()
    db.add(
        Command(
            host_id=host_id,
            action=action,
            container_id=container_id,
            status="pending",
            created_at=now(),
        )
    )
    db.commit()
    return redirect(url_for("dashboard.host_detail", host_id=host_id))


@dashboard_bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    db = SessionLocal()
    results = []
    if q:
        like = f"%{q.lower()}%"
        rows = (
            db.query(Container, Host)
            .join(Host, Container.host_id == Host.id)
            .filter((Container.name.ilike(like)) | (Container.image.ilike(like)))
            .order_by(Host.name.asc(), Container.name.asc())
            .all()
        )
        results = [{"container": c, "host": h} for c, h in rows]

    return render_template("search.html", q=q, results=results)


@dashboard_bp.route("/settings")
def settings():
    db = SessionLocal()
    host_rows = db.query(Host).order_by(Host.name.asc()).all()
    return render_template("settings.html", hosts=host_rows, new_host=None, install_command=None)


@dashboard_bp.route("/settings/hosts", methods=["POST"])
def add_host():
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(url_for("dashboard.settings"))

    db = SessionLocal()
    host_id = f"{slugify(name)}-{secrets.token_urlsafe(4)}"
    agent_token = secrets.token_urlsafe(32)

    host = Host(
        id=host_id,
        name=name,
        agent_token=agent_token,
        status="unknown",
        created_at=now(),
    )
    db.add(host)
    db.commit()

    install_command = (
        f"curl -sSL {request.host_url.rstrip('/')}/install-agent.sh | sh -s -- "
        f"--server {request.host_url.rstrip('/')} --token {agent_token} --host-id {host_id}"
    )

    host_rows = db.query(Host).order_by(Host.name.asc()).all()
    return render_template(
        "settings.html", hosts=host_rows, new_host=host, install_command=install_command
    )
