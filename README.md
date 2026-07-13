# LabDockerOverview

**Status: early WIP, v1 in progress.**

Single-pane-of-glass monitoring for a handful of Docker hosts. Portainer CE has no real
aggregated multi-host view (that's Business Edition, paywalled). Rancher is Kubernetes-first
and heavy for a homelab. This is for the common case: a few scattered Docker hosts (a NAS, a
Proxmox LXC, a VPS or two) that just need one simple dashboard.

## Features

**Host grid** — every registered host at a glance: online/offline status, CPU/RAM/disk usage
bars, container count.

**Container management** — per host: name, image, live status, CPU %, memory. Actions run
through the same pull-only command queue the agent already polls:
- Start / Stop / Restart
- Logs — tails the last 200 lines
- Exec — run one shell command inside a container and see the output (not a live/interactive
  shell — see [Limitations](#v1-scope--known-limitations))

**Cross-host container search** — one page listing every container on every host, filterable
by name or image, no query needed to browse everything.

**Image management** — per host: tags, size, dangling status, remove unused images.

**Volumes & networks** — per host: size, driver, which container(s) currently have a volume
mounted (Docker doesn't track "last used" anywhere, so this shows the closest real signal
instead of faking one), remove unused ones. Built-in networks (`bridge`/`host`/`none`) aren't
offered a remove button since Docker would just reject it.

**Sortable everything** — every table header is clickable (name, status, CPU, size, whatever
applies), toggling ascending/descending. Plain links/query params, no JS framework.

**Settings & onboarding** — first run prompts for an admin password before anything else is
reachable. The Settings page generates a one-line agent install command per host (auto-detects
the right address to put in it — see below) and lets you manage registered hosts.

## Architecture

- **Server** — a single Flask + SQLite container. Holds all state, serves the dashboard UI and
  a small JSON API, and issues per-host tokens.
- **Agents** — a lightweight Python container per Docker host. Pull-only: each agent polls its
  local `/var/run/docker.sock` for host/container/image/volume/network stats, pushes a report
  to the server on an interval, and picks up any queued commands (start/stop/restart/logs/exec/
  remove image/volume/network) on the same poll. Agents never accept inbound connections —
  home servers and VPSes behind NAT don't need to open anything up.

## Quick Start

1. Start the server — no cloning required, pulls the pre-built image straight from Docker Hub:

   ```sh
   mkdir -p ./data
   docker run -d -p 8080:8080 -v ./data:/data --restart unless-stopped --name labdockeroverview bobby156/labdockeroverview-dashboard
   ```

   The dashboard is now at `http://<this-host>:8080`. Data persists in `./data`. First visit
   prompts you to set an admin password.

   Prefer building from source, or want the `network_mode: host` tweak for exact LAN-IP
   auto-detection on Linux (see the note below)? Clone the repo and run `docker compose up -d`
   instead — same result, just built locally rather than pulled.

2. Go to the Settings page in the dashboard, add a host, and copy the generated one-line
   install command. It looks like:

   ```sh
   curl -sSL http://<server>:8080/install-agent.sh | sh -s -- --server https://yourserver:8080 --token <token> --host-id <host-id>
   ```

   Run that on each Docker host you want to monitor — it pulls `bobby156/labdockeroverview-agent`
   from Docker Hub and starts it, no local build needed. The server address in that command is
   auto-detected (falls back to asking you to set it explicitly in Settings if it can only see a
   Docker-internal address, e.g. behind Docker Desktop's bridge network) — on Linux, uncommenting
   `network_mode: host` in `docker-compose.yml` makes that detection exact.

Both images are built for `linux/amd64` and `linux/arm64` (Raspberry Pi, ARM-based Proxmox
setups, etc.) and published automatically on every push to `main` via GitHub Actions.

## v1 scope / known limitations

Deliberately minimal: single shared admin password (set on first run), no TLS termination (put
it behind a reverse proxy if you need HTTPS).

Exec is a one-shot non-interactive command (`sh -c '<command>'`), not a real terminal — agents
are poll-only and never hold a connection open, so a live PTY session would need a WebSocket
relay layer that doesn't exist yet. Things needing a real TTY (`vim`, `top`, ...) won't work.
