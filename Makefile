SHELL := /bin/sh

# HaaS 质量与提交门禁。运行时阶段的 lint/type/test 等目标在 S1 初始化后补齐。
.PHONY: help pre-commit secret-scan install-hooks

help:
	@echo "HaaS 提交门禁："
	@echo "  make pre-commit     运行提交门禁（whitespace + secret scan）"
	@echo "  make secret-scan    扫描已暂存变更中的敏感信息"
	@echo "  make install-hooks  安装 .git/hooks/pre-commit 拦截钩子"

pre-commit:
	./scripts/quality/pre-commit.sh

secret-scan:
	python3 scripts/quality/secret-scan.py

install-hooks:
	./scripts/quality/install-hooks.sh
