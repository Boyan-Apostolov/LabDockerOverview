#!/bin/sh
# Installs and starts the homelab-dashboard agent as a Docker container.
# Usage: curl -sSL http://<server>/install-agent.sh | sh -s -- --server https://yourserver:8080 --token <token> --host-id <host-id>

set -e

IMAGE="homelab-dashboard-agent:latest"
INTERVAL="12"

usage() {
    echo "Usage: $0 --server <url> --token <token> --host-id <id> [--interval <seconds>]" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --server)
            SERVER="$2"
            shift 2
            ;;
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --host-id)
            HOST_ID="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage
            ;;
    esac
done

if [ -z "$SERVER" ] || [ -z "$TOKEN" ] || [ -z "$HOST_ID" ]; then
    echo "Error: --server, --token, and --host-id are all required." >&2
    usage
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed or not on PATH. Install Docker first: https://docs.docker.com/engine/install/" >&2
    exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "Note: image '$IMAGE' not found locally. Until a published image is available," >&2
    echo "build it yourself from the homelab-dashboard repo, e.g.:" >&2
    echo "  git clone <repo-url> && cd homelab-dashboard/agent && docker build -t $IMAGE ." >&2
    exit 1
fi

docker run -d \
    --name homelab-agent \
    --restart unless-stopped \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -e SERVER_URL="$SERVER" \
    -e AGENT_TOKEN="$TOKEN" \
    -e HOST_ID="$HOST_ID" \
    -e AGENT_INTERVAL="$INTERVAL" \
    "$IMAGE"

echo "homelab-dashboard agent installed and running as host_id '$HOST_ID', reporting to $SERVER"
