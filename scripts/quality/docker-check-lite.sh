#!/bin/sh
set -eu

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"
dockerfile=docker/Dockerfile.lite
entrypoint=docker/run-lite.sh

test -f "$dockerfile"
sh -n "$entrypoint"
base="$(sed -n 's/^ARG HAAS_LITE_BASE=//p' "$dockerfile" | head -1)"
node_base="$(sed -n 's/^ARG HAAS_LITE_NODE_BASE=//p' "$dockerfile" | head -1)"
printf '%s\n' "$base" | grep -Eq '@sha256:[0-9a-f]{64}$'
printf '%s\n' "$node_base" | grep -Eq '@sha256:[0-9a-f]{64}$'
grep -q 'ENTRYPOINT.*tini' "$dockerfile"
grep -q 'EXPOSE 8092' "$dockerfile"
grep -q '^USER haas$' "$dockerfile"
grep -q -- '--read-only' "$0"
grep -q -- '--security-opt no-new-privileges' "$0"
grep -q -- '--cap-drop ALL' "$0"
if grep -Ev '^[[:space:]]*#' "$dockerfile" "$entrypoint" \
  | grep -Eq '(/opt/gem/run.sh|nginx|EXPOSE 8080|chromium|VNC|jupyter)'; then
  echo "docker-check-lite: AIO-only dependency found" >&2
  exit 1
fi
echo "docker-check-lite: static checks PASSED"

if [ "${HAAS_DOCKER_BUILD:-0}" != 1 ]; then
  echo "docker-check-lite: build/smoke skipped (set HAAS_DOCKER_BUILD=1)"
  exit 0
fi

platform="${HAAS_PLATFORM:-}"
if [ -z "$platform" ]; then
  arch="$(docker version --format '{{.Server.Arch}}')"
  platform="linux/$arch"
fi
case "$platform" in linux/amd64|linux/arm64) ;; *) echo "unsupported Lite platform: $platform" >&2; exit 1;; esac
image=haas:docker-check-lite
name=haas-docker-check-lite-$$
volume=haas-docker-check-lite-$$
workspace=$(mktemp -d)
cleanup() {
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
  docker rmi "$image" >/dev/null 2>&1 || true
  rm -rf "$workspace"
}
trap cleanup EXIT INT TERM
docker build --platform "$platform" -f "$dockerfile" \
  --build-arg "HAAS_LITE_BASE=${HAAS_LITE_BASE:-$base}" \
  --build-arg "HAAS_LITE_NODE_BASE=${HAAS_LITE_NODE_BASE:-$node_base}" -t "$image" .
docker volume create "$volume" >/dev/null
chmod 777 "$workspace"
docker run -d --platform "$platform" --name "$name" \
  -e HAAS_ADAPTER_BASE=fake \
  --network none \
  --read-only --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 256 --memory 4g --cpus 2 \
  --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --mount "type=volume,source=${volume},target=/data/haas" \
  --mount "type=bind,source=${workspace},target=/workspace" \
  "$image" >/dev/null
i=0
until docker exec "$name" curl -fsS --max-time 3 http://127.0.0.1:8092/health >/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 60 ] || [ "$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)" != true ]; then
    docker logs "$name" 2>&1 || true
    exit 1
  fi
  sleep 2
done
test "$(docker inspect -f '{{.Os}}/{{.Architecture}}' "$image")" = "$platform"
docker exec "$name" sh -c '! command -v nginx && [ ! -e /opt/gem/run.sh ]'
test "$(docker inspect -f '{{.Config.User}}' "$name")" = haas
test "$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$name")" = true
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$name")" = none
if docker inspect -f '{{json .Config.Env}}' "$name" | grep -Ei '(token|secret|credential|authorization)'; then
  echo "docker-check-lite: secret-like worker environment key found" >&2
  exit 1
fi
docker exec "$name" curl -fsS --max-time 3 'http://127.0.0.1:8092/ready?scope=control' >/dev/null
docker exec "$name" curl -fsS --max-time 3 'http://127.0.0.1:8092/ready?scope=execution' >/dev/null
docker exec "$name" sh -c 'printf ok >/workspace/marker.txt'
test "$(cat "$workspace/marker.txt")" = ok
test "$(docker exec "$name" stat -c %a /data/haas/private/generation-0.token)" = 600
request='{"version":1,"generation":0,"executionId":"inv_docker_smoke","harnessBase":"fake","request":{"newMessage":{"parts":[{"text":"hello"}]}}}'
first=$(printf '%s\n' "$request" | docker exec -i "$name" /opt/haas/bin/private-worker)
second=$(printf '%s\n' "$request" | docker exec -i "$name" /opt/haas/bin/private-worker)
test "$first" = "$second"
printf '%s\n' "$first" | grep -q '"status":"completed"'
echo "docker-check-lite: build/smoke PASSED ($platform)"
