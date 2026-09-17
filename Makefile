SHELL := /bin/sh
UV := uv
# dev 依赖在 [project.optional-dependencies]；uv run 需显式带上 --extra dev，
# 否则会把 .venv 同步成仅含运行时依赖，pytest/ruff/mypy 全部消失。
UVRUN := uv run --extra dev

# Lite follows the Docker execution node unless HAAS_PLATFORM is explicit.
HAAS_PLATFORM ?=
HAAS_LITE_BASE_DEFAULT := python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
HAAS_LITE_NODE_BASE_DEFAULT := node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
HAAS_AIO_BASE_DEFAULT := ghcr.io/agent-infra/sandbox@sha256:9a597aaa3716aca2fd42a517ceedc41063e5ceedcef43eb68bf7c059c0128b7a
HAAS_IMAGE_DEFAULT := haas:lite-local

# HaaS 开发与提交门禁。
.PHONY: help setup install-hooks pre-commit secret-scan fmt lint type \
        test-fast test-affected test-integration test-e2e adk-compat \
        coverage packaged-smoke docker-build docker-build-lite docker-build-aio docker-release-lite docker-check \
        docker-check-lite docker-check-aio full-check

help:
	@echo "HaaS 开发与提交门禁："
	@echo "  make setup            用 uv 创建 .venv 并安装可编辑依赖（dev 组）"
	@echo "  make install-hooks    安装 .git/hooks/pre-commit 拦截钩子"
	@echo "  make pre-commit       whitespace + secret scan"
	@echo "  make secret-scan      扫描暂存变更中的敏感信息"
	@echo "  make fmt              ruff format"
	@echo "  make lint             ruff check"
	@echo "  make type             mypy haas"
	@echo "  make test-fast        pytest 快速离线测试（默认跳过 integration/e2e）"
	@echo "  make test-affected    按 diff 影响面跑最小测试（当前等价 test-fast）"
	@echo "  make test-integration API/SSE/session/adapter 集成测试（含 integration 标记）"
	@echo "  make test-e2e         本机 E2E；需 HAAS_E2E=1 或分项开关，否则相关用例 skip"
	@echo "  make adk-compat       ADK 2.0 协议兼容性套件（adk 标记）"
	@echo "  make coverage         覆盖率报告（门禁：核心 >=90%，安全路径 >=95%）"
	@echo "  make packaged-smoke   验证打包 .app 能启动本地 HaaS 并接收一次任务"
	@echo "  make docker-check     默认 Lite 静态检查；HAAS_DOCKER_BUILD=1 追加真实 build + smoke"
	@echo "  make docker-check-aio AIO amd64 静态检查；显式开关追加 build + smoke"
	@echo "  make docker-build     默认构建 Lite（本地 Docker 节点平台）"
	@echo "  make docker-build-aio 显式构建 AIO linux/amd64"
	@echo "  make docker-release-lite HAAS_RELEASE_IMAGE=<registry/ref> 构建并推送双架构 OCI index"
	@echo "  make full-check       完整本机准出：lint + type + 全量测试 + 覆盖率 + docker + secret"

setup:
	$(UV) venv
	$(UV) pip install -e ".[dev]"

install-hooks:
	./scripts/quality/install-hooks.sh

pre-commit:
	./scripts/quality/pre-commit.sh

secret-scan:
	python3 scripts/quality/secret-scan.py

fmt:
	$(UVRUN) ruff format .

lint:
	$(UVRUN) ruff check .

type:
	$(UVRUN) mypy haas

test-fast:
	$(UVRUN) pytest -q -m "not integration and not e2e"

test-affected:
	$(UVRUN) pytest -q -m "not integration and not e2e"

# Integration covers the in-process loopback harness paths (no real Codex).
test-integration:
	$(UVRUN) pytest -q -m "integration" -rs

# E2E requires explicit switches (HAAS_E2E=1 / HAAS_E2E_CODEX=1 /
# HAAS_E2E_OPEN_SANDBOX=1). Without them the gated cases report as skipped,
# which must be recorded as `not_run` rather than passed.
test-e2e:
	$(UVRUN) pytest -q -m "e2e" -rs

adk-compat:
	$(UVRUN) pytest -q -m "adk" -rs

coverage:
	$(UVRUN) coverage run -m pytest -q
	$(UVRUN) coverage report

packaged-smoke:
	manager/packaging/smoke_packaged_app.sh

docker-build: docker-build-lite

docker-build-lite:
	@platform="$(HAAS_PLATFORM)"; \
	if [ -z "$$platform" ]; then platform="linux/$$(docker version --format '{{.Server.Arch}}')"; fi; \
	case "$$platform" in linux/amd64|linux/arm64) ;; *) echo "docker-build-lite: unsupported platform $$platform" >&2; exit 1;; esac; \
	base_image="$(if $(HAAS_LITE_BASE),$(HAAS_LITE_BASE),$(HAAS_LITE_BASE_DEFAULT))"; \
	if ! printf "%s\n" "$$base_image" | grep -Eq "@sha256:[0-9a-f]{64}$$"; then echo "docker-build-lite: HAAS_LITE_BASE must be digest-pinned" >&2; exit 1; fi; \
	node_base="$(if $(HAAS_LITE_NODE_BASE),$(HAAS_LITE_NODE_BASE),$(HAAS_LITE_NODE_BASE_DEFAULT))"; \
	if ! printf "%s\n" "$$node_base" | grep -Eq "@sha256:[0-9a-f]{64}$$"; then echo "docker-build-lite: HAAS_LITE_NODE_BASE must be digest-pinned" >&2; exit 1; fi; \
	image="$(if $(HAAS_IMAGE),$(HAAS_IMAGE),$(HAAS_IMAGE_DEFAULT))"; \
	echo "==> docker-build-lite: platform=$$platform image=$$image base=$$base_image"; \
	if docker buildx version >/dev/null 2>&1; then \
	  docker buildx build --platform=$$platform --load -f docker/Dockerfile.lite \
	    --build-arg HAAS_LITE_BASE="$$base_image" --build-arg HAAS_LITE_NODE_BASE="$$node_base" -t "$$image" .; \
	else \
	  docker build --platform=$$platform -f docker/Dockerfile.lite \
	    --build-arg HAAS_LITE_BASE="$$base_image" --build-arg HAAS_LITE_NODE_BASE="$$node_base" -t "$$image" .; \
	fi

docker-release-lite:
	@test -n "$(HAAS_RELEASE_IMAGE)" || { echo "docker-release-lite: HAAS_RELEASE_IMAGE is required" >&2; exit 1; }
	@base_image="$(if $(HAAS_LITE_BASE),$(HAAS_LITE_BASE),$(HAAS_LITE_BASE_DEFAULT))"; \
	node_base="$(if $(HAAS_LITE_NODE_BASE),$(HAAS_LITE_NODE_BASE),$(HAAS_LITE_NODE_BASE_DEFAULT))"; \
	printf '%s\n%s\n' "$$base_image" "$$node_base" | grep -Eqv '@sha256:[0-9a-f]{64}$$' && { echo "docker-release-lite: bases must be digest-pinned" >&2; exit 1; } || true; \
	docker buildx build --platform=linux/amd64,linux/arm64 --push -f docker/Dockerfile.lite \
	  --build-arg HAAS_LITE_BASE="$$base_image" --build-arg HAAS_LITE_NODE_BASE="$$node_base" \
	  -t "$(HAAS_RELEASE_IMAGE)" .

docker-build-aio:
	@base_image="$(if $(HAAS_AIO_BASE),$(HAAS_AIO_BASE),$(HAAS_AIO_BASE_DEFAULT))"; \
	if ! printf "%s\n" "$$base_image" | grep -Eq "@sha256:[0-9a-f]{64}$$"; then echo "docker-build-aio: HAAS_AIO_BASE must be digest-pinned" >&2; exit 1; fi; \
	image="$(if $(HAAS_IMAGE),$(HAAS_IMAGE),haas:aio-local)"; \
	docker buildx build --platform=linux/amd64 --load --build-arg HAAS_BASE_IMAGE="$$base_image" -t "$$image" .

docker-check: docker-check-lite

docker-check-lite:
	./scripts/quality/docker-check-lite.sh

docker-check-aio:
	./scripts/quality/docker-check-aio.sh

full-check:
	@echo "==> full-check 1/6: lint"
	@$(MAKE) lint
	@echo "==> full-check 2/6: type"
	@$(MAKE) type
	@echo "==> full-check 3/6: adk-compat"
	@$(MAKE) adk-compat
	@echo "==> full-check 4/6: coverage (full suite)"
	@$(MAKE) coverage
	@echo "==> full-check 5/6: docker-check"
	@$(MAKE) docker-check
	@echo "==> full-check 6/6: secret-scan"
	@$(MAKE) secret-scan
	@echo "full-check: PASSED"
