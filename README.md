# LabDockerOverview

**Status: early WIP, v1 in progress.**

Single-pane-of-glass monitoring for a handful of Docker hosts. Portainer CE has no real
aggregated multi-host view (that's Business Edition, paywalled). Rancher is Kubernetes-first
and heavy for a homelab. This is for the common case: a few scattered Docker hosts (a NAS, a
Proxmox LXC, a VPS or two) that just need one simple dashboard.

## Architecture

- **Server** — a single Flask + SQLite container. Holds all state, serves the dashboard UI and
  a small JSON API, and issues per-host tokens.
- **Agents** — a lightweight Python container per Docker host. Pull-only: each agent polls its
  local `/var/run/docker.sock` for host/container stats, pushes a report to the server on an
  interval, and picks up any queued commands (start/stop/restart a container) on the same poll.
  Agents never accept inbound connections.

## Quick Start

1. Start the server:

   ```sh
   docker compose up -d
   ```

   The dashboard is now at `http://<this-host>:8080`. Data persists in `./data`.

2. Go to the Settings page in the dashboard, add a host, and copy the generated one-line
   install command. It looks like:

   ```sh
   curl -sSL http://<server>:8080/install-agent.sh | sh -s -- --server https://yourserver:8080 --token <token> --host-id <host-id>
   ```

   Run that on each Docker host you want to monitor. It pulls and starts the agent container
   for you.

Once a pre-built image is published, the server itself can also be run directly without
cloning the repo:

```sh
docker run -d -p 8080:8080 -v ./data:/data --name labdockeroverview yourname/labdockeroverview
```

For now, `docker compose up -d` (building from source) is the primary install path.

## v1 scope

Deliberately minimal: single shared admin password (set on first run), no TLS termination (put
it behind a reverse proxy if you need HTTPS), no multi-arch build matrix yet.
