SHELL := /bin/sh
UV := uv

# HaaS 开发与提交门禁。运行时阶段的 lint/type/test 目标随 S1 骨架补齐；
# integration/e2e 在 S2/S3 具备运行链路后再提供。
.PHONY: help setup install-hooks pre-commit secret-scan fmt lint type test-fast test-affected

help:
	@echo "HaaS 开发与提交门禁："
	@echo "  make setup          用 uv 创建 .venv 并安装可编辑依赖（dev 组）"
	@echo "  make install-hooks  安装 .git/hooks/pre-commit 拦截钩子"
	@echo "  make pre-commit     whitespace + secret scan"
	@echo "  make secret-scan    扫描暂存变更中的敏感信息"
	@echo "  make fmt            ruff format"
	@echo "  make lint           ruff check"
	@echo "  make type           mypy haas"
	@echo "  make test-fast      pytest 快速离线测试（默认跳过 integration/e2e）"
	@echo "  make test-affected  按 diff 影响面跑最小测试（S1 暂等价 test-fast）"

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
	$(UV) run ruff format .

lint:
	$(UV) run ruff check .

type:
	$(UV) run mypy haas

test-fast:
	$(UV) run pytest -q -m "not integration and not e2e"

test-affected:
	$(UV) run pytest -q -m "not integration and not e2e"
