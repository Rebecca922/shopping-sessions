CXX ?= g++
CXXFLAGS ?= -O3 -DNDEBUG -std=c++20 -pthread
IMAGE ?= shopping-sessions:v1
PLATFORM ?= linux/amd64

.PHONY: build fixtures binary-fixtures container-fixtures generator dev run-dev check-dev
core: src/main.cpp
	$(CXX) $(CXXFLAGS) $< -o $@

build:
	docker buildx build --platform $(PLATFORM) --load -t $(IMAGE) .

binary-fixtures: core
	python3 scripts/check_binary.py fixtures

fixtures: core
	SCRATCH_DIR="$(CURDIR)/scratch" python3 scripts/run_fixtures.py fixtures -- python3 run.py

container-fixtures: build
	python3 scripts/run_fixtures.py fixtures -- sh scripts/docker_run.sh

generator:
	docker buildx build --platform $(PLATFORM) --load -f Dockerfile.generator -t shopping-generator:v1 .

dev: generator
	mkdir -p dev
	docker run --rm --platform $(PLATFORM) -v "$(CURDIR)/dev:/generated" shopping-generator:v1 --seed 1 --events 1000000 --clicks 250000 --output /generated

run-dev: build
	sh scripts/docker_run.sh dev out

ANSWER ?= dev-answer
check-dev:
	python3 scripts/compare.py out $(ANSWER)
