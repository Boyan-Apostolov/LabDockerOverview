import ipaddress
import os
import re
import secrets
import socket

from flask import Blueprint, render_template, request, redirect, url_for, send_from_directory, session

from .db import SessionLocal
from .models import Host, Container, HostStat, Command, Image, Volume, Network, now
from .auth import (
    is_setup_complete,
    set_admin_password,
    check_admin_password,
    get_server_url,
    set_server_url,
    is_local_url,
)

dashboard_bp = Blueprint("dashboard", __name__)

_SCRIPTS_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),  # local dev: server/app -> repo root/scripts
    os.path.join(os.path.dirname(__file__), "..", "scripts"),  # docker image: /app/app -> /app/scripts
]
SCRIPTS_DIR = next((p for p in _SCRIPTS_CANDIDATES if os.path.isdir(p)), _SCRIPTS_CANDIDATES[0])

# reachable without a session: the curl-fetched installer, and the setup/login flow itself
PUBLIC_ENDPOINTS = {"dashboard.install_agent_script", "dashboard.setup", "dashboard.login"}


@dashboard_bp.before_request
def require_auth():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None

    db = SessionLocal()
    if not is_setup_complete(db):
        return redirect(url_for("dashboard.setup"))

    if not session.get("authenticated"):
        return redirect(url_for("dashboard.login", next=request.path))
    return None


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "host"


def detect_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packet actually sent; just forces the OS to pick a real outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# in bridge-mode Docker (the default here), the outbound-socket trick only sees the
# container's own bridge IP, not the host's real LAN IP - filter that out rather than
# confidently presenting a wrong-but-plausible-looking address
def looks_container_internal(ip):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("172.16.0.0/12")
    except ValueError:
        return False


def auto_detected_url():
    # if you're already browsing via a real address (e.g. from another device on the LAN),
    # that's the ground truth - no guessing needed, and it beats any bridge-network guess
    request_url = request.host_url.rstrip("/")
    if not is_local_url(request_url):
        return request_url

    ip = detect_lan_ip()
    if ip and not is_local_url(ip) and not looks_container_internal(ip):
        return f"http://{ip}:8080"

    return request_url


def effective_server_url(db):
    return get_server_url(db) or auto_detected_url()


def parse_sort(default_key, allowed_keys, prefix=""):
    key = request.args.get(f"{prefix}sort", default_key)
    if key not in allowed_keys:
        key = default_key
    direction = request.args.get(f"{prefix}dir", "asc")
    if direction not in ("asc", "desc"):
        direction = "asc"
    return key, direction


def sorted_query(query, columns, sort_key, direction):
    column = columns[sort_key]
    return query.order_by(column.desc() if direction == "desc" else column.asc())


def sort_link(endpoint, current_sort, current_dir, key, param_prefix="", **url_kwargs):
    # merges in whatever's already in the URL (e.g. ?q=... on search, or the other
    # table's sort state on a page with two tables) so clicking one header doesn't
    # reset unrelated state
    next_dir = "desc" if current_sort == key and current_dir == "asc" else "asc"
    args = {**request.args.to_dict(), **url_kwargs, f"{param_prefix}sort": key, f"{param_prefix}dir": next_dir}
    return url_for(endpoint, **args)


def sort_arrow(current_sort, current_dir, key):
    if current_sort != key:
        return ""
    return " ▲" if current_dir == "asc" else " ▼"


@dashboard_bp.context_processor
def inject_sort_helpers():
    return {"sort_link": sort_link, "sort_arrow": sort_arrow}


@dashboard_bp.route("/install-agent.sh")
def install_agent_script():
    return send_from_directory(SCRIPTS_DIR, "install-agent.sh", mimetype="text/x-shellscript")


@dashboard_bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = SessionLocal()
    if is_setup_complete(db):
        return redirect(url_for("dashboard.hosts"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            set_admin_password(db, password)
            session["authenticated"] = True
            return redirect(url_for("dashboard.hosts", welcome=1))

    return render_template("setup.html", error=error)


@dashboard_bp.route("/login", methods=["GET", "POST"])
def login():
    db = SessionLocal()
    if not is_setup_complete(db):
        return redirect(url_for("dashboard.setup"))

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if check_admin_password(db, password):
            session["authenticated"] = True
            return redirect(request.form.get("next") or url_for("dashboard.hosts"))
        error = "Incorrect password."

    return render_template("login.html", error=error, next=request.args.get("next", ""))


@dashboard_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("dashboard.login"))


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

    show_welcome = request.args.get("welcome") == "1"
    return render_template("hosts.html", hosts_data=hosts_data, show_welcome=show_welcome)


CONTAINER_SORT_COLUMNS = {
    "name": Container.name,
    "image": Container.image,
    "status": Container.status,
    "cpu": Container.cpu_percent,
    "mem": Container.mem_usage_mb,
}


@dashboard_bp.route("/hosts/<host_id>")
def host_detail(host_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)
    sort, dir = parse_sort("name", CONTAINER_SORT_COLUMNS)
    containers = sorted_query(
        db.query(Container).filter_by(host_id=host_id), CONTAINER_SORT_COLUMNS, sort, dir
    ).all()
    return render_template("host_detail.html", host=host, containers=containers, sort=sort, dir=dir)


@dashboard_bp.route("/hosts/<host_id>/containers/<container_id>/commands/<action>", methods=["POST"])
def enqueue_command(host_id, container_id, action):
    if action not in ("restart", "stop", "start", "logs"):
        return redirect(url_for("dashboard.host_detail", host_id=host_id))

    db = SessionLocal()
    cmd = Command(
        host_id=host_id,
        action=action,
        container_id=container_id,
        status="pending",
        created_at=now(),
    )
    db.add(cmd)
    db.commit()

    if action == "logs":
        return redirect(url_for("dashboard.command_status", host_id=host_id, command_id=cmd.id))
    return redirect(url_for("dashboard.host_detail", host_id=host_id))


@dashboard_bp.route("/hosts/<host_id>/commands/<command_id>")
def command_status(host_id, command_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)
    cmd = db.query(Command).get(command_id)
    return render_template("command_result.html", host=host, host_id=host_id, cmd=cmd)


SEARCH_SORT_COLUMNS = {"host": Host.name, **CONTAINER_SORT_COLUMNS}


@dashboard_bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    db = SessionLocal()
    query = db.query(Container, Host).join(Host, Container.host_id == Host.id)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter((Container.name.ilike(like)) | (Container.image.ilike(like)))

    sort, dir = parse_sort("name", SEARCH_SORT_COLUMNS)
    rows = sorted_query(query, SEARCH_SORT_COLUMNS, sort, dir).all()
    results = [{"container": c, "host": h} for c, h in rows]

    return render_template("search.html", q=q, results=results, sort=sort, dir=dir)


HOST_SORT_COLUMNS = {"name": Host.name, "id": Host.id, "status": Host.status, "last_seen": Host.last_seen}


@dashboard_bp.route("/settings")
def settings():
    db = SessionLocal()
    sort, dir = parse_sort("name", HOST_SORT_COLUMNS)
    host_rows = sorted_query(db.query(Host), HOST_SORT_COLUMNS, sort, dir).all()
    server_url = get_server_url(db)
    detected_url = auto_detected_url()
    return render_template(
        "settings.html",
        hosts=host_rows,
        new_host=None,
        install_command=None,
        server_url=server_url,
        detected_url=detected_url,
        detected_url_is_local=is_local_url(detected_url),
        sort=sort,
        dir=dir,
    )


@dashboard_bp.route("/settings/server-url", methods=["POST"])
def update_server_url():
    url = request.form.get("server_url", "").strip()
    db = SessionLocal()
    set_server_url(db, url)  # empty clears the override, falling back to auto-detect
    return redirect(url_for("dashboard.settings"))


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

    base_url = effective_server_url(db)
    install_command = (
        f"curl -sSL {base_url}/install-agent.sh | sh -s -- "
        f"--server {base_url} --token {agent_token} --host-id {host_id}"
    )

    sort, dir = parse_sort("name", HOST_SORT_COLUMNS)
    host_rows = sorted_query(db.query(Host), HOST_SORT_COLUMNS, sort, dir).all()
    return render_template(
        "settings.html",
        hosts=host_rows,
        new_host=host,
        install_command=install_command,
        install_command_is_local=is_local_url(base_url),
        server_url=get_server_url(db),
        detected_url=auto_detected_url(),
        detected_url_is_local=is_local_url(auto_detected_url()),
        sort=sort,
        dir=dir,
    )


@dashboard_bp.route("/settings/hosts/<host_id>/remove", methods=["POST"])
def remove_host(host_id):
    db = SessionLocal()
    # the dashboard can only stop tracking this host - it has no way to reach out and stop
    # the agent running there (pull-only architecture, no inbound access to hosts), so the
    # agent's token is what stops working, not the agent process itself
    db.query(Container).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(Image).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(Volume).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(Network).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(HostStat).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(Command).filter_by(host_id=host_id).delete(synchronize_session=False)
    db.query(Host).filter_by(id=host_id).delete(synchronize_session=False)
    db.commit()
    return redirect(url_for("dashboard.settings"))


@dashboard_bp.route("/hosts/<host_id>/containers/<container_id>/exec", methods=["GET"])
def container_exec(host_id, container_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)
    return render_template("exec.html", host=host, container_id=container_id)


@dashboard_bp.route("/hosts/<host_id>/containers/<container_id>/exec", methods=["POST"])
def container_exec_submit(host_id, container_id):
    command = request.form.get("command", "").strip()
    if not command:
        return redirect(url_for("dashboard.container_exec", host_id=host_id, container_id=container_id))

    db = SessionLocal()
    cmd = Command(
        host_id=host_id,
        action="exec",
        container_id=container_id,
        payload=command,
        status="pending",
        created_at=now(),
    )
    db.add(cmd)
    db.commit()

    return redirect(url_for("dashboard.command_status", host_id=host_id, command_id=cmd.id))


IMAGE_SORT_COLUMNS = {"tags": Image.tags, "size": Image.size_mb}


@dashboard_bp.route("/hosts/<host_id>/images")
def host_images(host_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)
    sort, dir = parse_sort("tags", IMAGE_SORT_COLUMNS)
    images = sorted_query(db.query(Image).filter_by(host_id=host_id), IMAGE_SORT_COLUMNS, sort, dir).all()
    return render_template("images.html", host=host, images=images, sort=sort, dir=dir)


@dashboard_bp.route("/hosts/<host_id>/images/<image_id>/remove", methods=["POST"])
def remove_image(host_id, image_id):
    db = SessionLocal()
    db.add(Command(host_id=host_id, action="remove_image", container_id=image_id, status="pending", created_at=now()))
    db.commit()
    return redirect(url_for("dashboard.host_images", host_id=host_id))


BUILTIN_NETWORKS = {"bridge", "host", "none"}


VOLUME_SORT_COLUMNS = {
    "name": Volume.name,
    "driver": Volume.driver,
    "size": Volume.size_mb,
    "used_by": Volume.used_by,
    "created": Volume.docker_created_at,
}
NETWORK_SORT_COLUMNS = {"name": Network.name, "driver": Network.driver, "scope": Network.scope}


@dashboard_bp.route("/hosts/<host_id>/volumes")
def host_volumes(host_id):
    db = SessionLocal()
    host = db.query(Host).get(host_id)

    sort, dir = parse_sort("name", VOLUME_SORT_COLUMNS)
    volumes = sorted_query(db.query(Volume).filter_by(host_id=host_id), VOLUME_SORT_COLUMNS, sort, dir).all()

    nsort, ndir = parse_sort("name", NETWORK_SORT_COLUMNS, prefix="n")
    networks = sorted_query(db.query(Network).filter_by(host_id=host_id), NETWORK_SORT_COLUMNS, nsort, ndir).all()

    return render_template(
        "volumes.html", host=host, volumes=volumes, networks=networks, sort=sort, dir=dir, nsort=nsort, ndir=ndir
    )


@dashboard_bp.route("/hosts/<host_id>/volumes/<name>/remove", methods=["POST"])
def remove_volume(host_id, name):
    db = SessionLocal()
    db.add(Command(host_id=host_id, action="remove_volume", container_id=name, status="pending", created_at=now()))
    db.commit()
    return redirect(url_for("dashboard.host_volumes", host_id=host_id))


@dashboard_bp.route("/hosts/<host_id>/networks/<name>/remove", methods=["POST"])
def remove_network(host_id, name):
    if name in BUILTIN_NETWORKS:  # Docker refuses to remove these; don't even enqueue the attempt
        return redirect(url_for("dashboard.host_volumes", host_id=host_id))

    db = SessionLocal()
    db.add(Command(host_id=host_id, action="remove_network", container_id=name, status="pending", created_at=now()))
    db.commit()
    return redirect(url_for("dashboard.host_volumes", host_id=host_id))
