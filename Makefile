.PHONY: check lint test compose docs sync images
SHELL := /bin/bash

check: lint compose test docs   ## tudo que o CI roda (exceto gitleaks e images)

lint:                           ## yamllint + shellcheck
	yamllint -s .
	shellcheck scripts/*.sh

compose:                        ## docker compose config em todas as stacks
	scripts/validate_compose.sh

test:                           ## pytest
	python3 -m pytest tests -q

docs:                           ## README em sincronia?
	python3 scripts/sync_readme.py --check

sync:                           ## atualiza o README a partir dos arquivos
	python3 scripts/sync_readme.py

images:                         ## imagens existem para arm64/armv7? (precisa de rede + docker buildx)
	python3 scripts/check_images.py
