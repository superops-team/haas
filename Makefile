SHELL := /bin/sh
UV := uv
# dev 依赖在 [project.optional-dependencies]；uv run 需显式带上 --extra dev，
# 否则会把 .venv 同步成仅含运行时依赖，pytest/ruff/mypy 全部消失。
UVRUN := uv run --extra dev

# linux/amd64 是本项目硬性平台约定（AGENTS.md「Docker 与 OpenSandbox AIO」）。
# 构建/运行镜像统一走这个变量，避免在 Apple Silicon 等主机上每次重新试探架构。
HAAS_PLATFORM := linux/amd64
HAAS_BASE_IMAGE_DEFAULT := ghcr.io/agent-infra/sandbox@sha256:5ca2cd5619ee1e18c5479301e740c1e35307ce85d4142a145aec65d459655eee
HAAS_IMAGE_DEFAULT := haas:local

# HaaS 开发与提交门禁。
.PHONY: help setup install-hooks pre-commit secret-scan fmt lint type \
        test-fast test-affected test-integration test-e2e adk-compat \
        coverage docker-build docker-check full-check

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
	@echo "  make docker-check     Dockerfile/AIO/health/ready 检查；HAAS_DOCKER_BUILD=1 追加真实 build + 容器 smoke"
	@echo "  make docker-build     linux/amd64 构建；可选 HAAS_BASE_IMAGE，默认生产 digest，BuildKit 复用依赖缓存"
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

docker-build:
	@base_image="$(if $(HAAS_BASE_IMAGE),$(HAAS_BASE_IMAGE),$(HAAS_BASE_IMAGE_DEFAULT))"; \
	if ! printf "%s\n" "$$base_image" | grep -Eq "@sha256:[0-9a-f]{64}$$"; then echo "docker-build: HAAS_BASE_IMAGE must be digest-pinned" >&2; exit 1; fi; \
	image="$(if $(HAAS_IMAGE),$(HAAS_IMAGE),$(HAAS_IMAGE_DEFAULT))"; \
	echo "==> docker-build: platform=$(HAAS_PLATFORM) image=$$image base=$$base_image"; \
	if docker buildx version >/dev/null 2>&1; then \
	  docker buildx build --platform=$(HAAS_PLATFORM) --load \
	    --build-arg HAAS_BASE_IMAGE="$$base_image" -t "$$image" .; \
	else \
	  docker build --platform=$(HAAS_PLATFORM) \
	    --build-arg HAAS_BASE_IMAGE="$$base_image" -t "$$image" .; \
	fi

docker-check:
	./scripts/quality/docker-check.sh

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
