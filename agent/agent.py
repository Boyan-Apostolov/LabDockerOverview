import argparse
import os
import socket
import time
from datetime import datetime, timezone

import docker
import psutil
import requests


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("SERVER_URL"))
    p.add_argument("--token", default=os.environ.get("AGENT_TOKEN"))
    p.add_argument("--host-id", default=os.environ.get("HOST_ID", socket.gethostname()))
    p.add_argument("--interval", type=int, default=int(os.environ.get("AGENT_INTERVAL", 12)))
    args = p.parse_args()
    if not args.server or not args.token:
        p.error("--server/SERVER_URL and --token/AGENT_TOKEN are required")
    return args


def collect_host_stats():
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "mem_used_mb": round(mem.used / (1024 ** 2)),
        "mem_total_mb": round(mem.total / (1024 ** 2)),
        "disk_used_gb": round(disk.used / (1024 ** 3), 1),
        "disk_total_gb": round(disk.total / (1024 ** 3), 1),
    }


def container_cpu_percent(stats):
    # docker's non-blocking stats snapshot includes both the current and
    # previous sample, so cpu% is derived from the delta between them
    # rather than sleeping between two live samples.
    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
    system_delta = stats["cpu_stats"].get("system_cpu_usage", 0) - stats["precpu_stats"].get("system_cpu_usage", 0)
    num_cpus = stats["cpu_stats"].get("online_cpus") or len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
    if system_delta > 0 and cpu_delta > 0:
        return round((cpu_delta / system_delta) * num_cpus * 100, 1)
    return 0.0


def container_ports(container):
    ports = []
    for container_port, bindings in (container.ports or {}).items():
        if not bindings:
            continue
        container_port_num = container_port.split("/")[0]
        for b in bindings:
            ports.append(f"{b.get('HostPort')}:{container_port_num}")
    return ports


def collect_containers(client):
    result = []
    for c in client.containers.list(all=True):
        try:
            if c.status == "running":
                stats = c.stats(stream=False)
                mem_mb = round(stats["memory_stats"].get("usage", 0) / (1024 ** 2))
                cpu_percent = container_cpu_percent(stats)
            else:
                mem_mb = 0
                cpu_percent = 0.0
            result.append({
                "id": c.id[:12],
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                "status": c.status,
                "cpu_percent": cpu_percent,
                "mem_usage_mb": mem_mb,
                "ports": container_ports(c),
            })
        except Exception as e:
            log(f"skipping container {c.name}: {e}")
    return result


def send_report(server, token, host_id, payload):
    resp = requests.post(
        f"{server}/api/v1/report",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()


LOG_TAIL_LINES = 200
LOG_MAX_CHARS = 20000


def run_command(container, action):
    if action == "logs":
        raw = container.logs(tail=LOG_TAIL_LINES, timestamps=True)
        return raw.decode("utf-8", errors="replace")[-LOG_MAX_CHARS:]
    getattr(container, action)()
    return None


def poll_commands(server, token, host_id, client):
    resp = requests.get(
        f"{server}/api/v1/commands",
        params={"host_id": host_id},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    for cmd in resp.json():
        status = "success"
        output = None
        try:
            container = client.containers.get(cmd["container_id"])
            output = run_command(container, cmd["action"])
        except Exception as e:
            log(f"command {cmd['id']} ({cmd['action']}) failed: {e}")
            status = "failed"
            output = str(e)
        requests.post(
            f"{server}/api/v1/commands/{cmd['id']}/ack",
            json={"status": status, "output": output},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )


def main():
    args = parse_args()
    try:
        client = docker.from_env()
        client.ping()
    except Exception as e:
        log(f"cannot reach docker socket, is /var/run/docker.sock mounted? {e}")
        return

    log(f"agent starting, host_id={args.host_id}, server={args.server}, interval={args.interval}s")

    while True:
        try:
            payload = {
                "host_id": args.host_id,
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "host_stats": collect_host_stats(),
                "containers": collect_containers(client),
            }
            send_report(args.server, args.token, args.host_id, payload)
            poll_commands(args.server, args.token, args.host_id, client)
        except requests.exceptions.RequestException as e:
            log(f"network error talking to server, will retry next cycle: {e}")
        except Exception as e:
            log(f"unexpected error in report cycle: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
