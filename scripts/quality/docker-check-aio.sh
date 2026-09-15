#!/bin/sh
set -eu

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$repo_root"
sh -n docker/run.sh
base="$(sed -n 's/^ARG HAAS_BASE_IMAGE=//p' Dockerfile | head -1)"
printf '%s\n' "$base" | grep -Eq '@sha256:[0-9a-f]{64}$'
grep -q '/opt/gem/run.sh' docker/run.sh
grep -q 'EXPOSE 8080' Dockerfile
grep -q 'NODEJS_REPL_PORT_22=8093' Dockerfile
echo "docker-check-aio: static checks PASSED"

if [ "${HAAS_DOCKER_BUILD:-0}" != 1 ]; then
  echo "docker-check-aio: build/smoke skipped (set HAAS_DOCKER_BUILD=1)"
  exit 0
fi

image=haas:docker-check-aio
name=haas-docker-check-aio-$$
port="${HAAS_DOCKER_CHECK_PORT:-18098}"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; docker rmi "$image" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
docker build --platform linux/amd64 --build-arg "HAAS_BASE_IMAGE=${HAAS_AIO_BASE:-$base}" -t "$image" .
docker run -d --platform linux/amd64 --name "$name" -p "127.0.0.1:${port}:8080" "$image" >/dev/null
i=0
until curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null; do
  i=$((i + 1)); [ "$i" -lt 60 ] || { docker logs "$name"; exit 1; }; sleep 2
done
docker exec "$name" curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:8092/health
docker exec "$name" supervisorctl status nginx | grep -q RUNNING
docker exec "$name" supervisorctl status python-server | grep -q RUNNING
echo "docker-check-aio: build/smoke PASSED (linux/amd64)"
