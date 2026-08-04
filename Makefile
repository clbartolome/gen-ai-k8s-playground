PROJECT := gen-ai-k8s-playground
PODMAN ?= podman

AGENT_IMAGE := $(PROJECT)/agent:local
CHAT_IMAGE := $(PROJECT)/chat:local
MONITOR_IMAGE := $(PROJECT)/monitor:local
NETWORK := $(PROJECT)-net
AGENT_CONTAINER := $(PROJECT)-agent
CHAT_CONTAINER := $(PROJECT)-chat
MONITOR_CONTAINER := $(PROJECT)-monitor

QUAY_REGISTRY ?= quay.io
QUAY_USER ?= calopezb
QUAY_TAG ?= latest
AGENT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-agent:$(QUAY_TAG)
CHAT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-chat:$(QUAY_TAG)
MONITOR_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-monitor:$(QUAY_TAG)

AGENT_PORT ?= 8080
CHAT_PORT ?= 5000
MONITOR_PORT ?= 5100
AGENT_TIMEOUT ?= 180
ENV_FILE := .env
DATA_DIR := $(CURDIR)/.data
TRACE_DB := $(DATA_DIR)/agent-traces.db

.PHONY: local-build local-run local-clean quay-login quay-upload openshift-deploy openshift-delete

local-build:
	$(PODMAN) build -t $(AGENT_IMAGE) -f components/agent/Containerfile components/agent
	$(PODMAN) build -t $(CHAT_IMAGE) -f components/chat/Containerfile components/chat
	$(PODMAN) build -t $(MONITOR_IMAGE) -f components/monitor/Containerfile components/monitor

local-run:
	@test -f $(ENV_FILE) || (echo "Missing $(ENV_FILE). Copy .env.example to .env and fill in your values." && exit 1)
	@$(PODMAN) network exists $(NETWORK) || $(PODMAN) network create $(NETWORK)
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(MONITOR_CONTAINER) $(AGENT_CONTAINER)
	@mkdir -p $(DATA_DIR)
	@chmod 777 $(DATA_DIR)
	@rm -f $(TRACE_DB)
	$(PODMAN) run -d --name $(AGENT_CONTAINER) --network $(NETWORK) \
		-p $(AGENT_PORT):8080 --env-file $(ENV_FILE) \
		-v $(DATA_DIR):/data:Z \
		-e TRACE_DB_PATH=/data/agent-traces.db \
		$(AGENT_IMAGE)
	$(PODMAN) run -d --name $(CHAT_CONTAINER) --network $(NETWORK) \
		-p $(CHAT_PORT):5000 \
		-e AGENT_URL=http://$(AGENT_CONTAINER):8080 \
		-e AGENT_TIMEOUT=$(AGENT_TIMEOUT) $(CHAT_IMAGE)
	$(PODMAN) run -d --name $(MONITOR_CONTAINER) --network $(NETWORK) \
		-p $(MONITOR_PORT):5100 \
		-e AGENT_URL=http://$(AGENT_CONTAINER):8080 \
		-e AGENT_TIMEOUT=$(AGENT_TIMEOUT) $(MONITOR_IMAGE)
	@echo "Chat:    http://localhost:$(CHAT_PORT)"
	@echo "Monitor: http://localhost:$(MONITOR_PORT)"
	@echo "Agent:   http://localhost:$(AGENT_PORT)/health"

local-clean:
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(MONITOR_CONTAINER) $(AGENT_CONTAINER)
	-$(PODMAN) rmi $(AGENT_IMAGE) $(CHAT_IMAGE) $(MONITOR_IMAGE)
	-$(PODMAN) network rm $(NETWORK)
	@rm -f $(TRACE_DB)

quay-login:
	@if [ -n "$$QUAY_TOKEN" ]; then \
		echo "$$QUAY_TOKEN" | $(PODMAN) login $(QUAY_REGISTRY) -u $(QUAY_USER) --password-stdin; \
	else \
		$(PODMAN) login $(QUAY_REGISTRY) -u $(QUAY_USER); \
	fi

quay-upload: local-build quay-login
	$(PODMAN) tag $(AGENT_IMAGE) $(AGENT_QUAY_IMAGE)
	$(PODMAN) tag $(CHAT_IMAGE) $(CHAT_QUAY_IMAGE)
	$(PODMAN) tag $(MONITOR_IMAGE) $(MONITOR_QUAY_IMAGE)
	$(PODMAN) push $(AGENT_QUAY_IMAGE)
	$(PODMAN) push $(CHAT_QUAY_IMAGE)
	$(PODMAN) push $(MONITOR_QUAY_IMAGE)
	@echo ""
	@echo "Agent:   $(AGENT_QUAY_IMAGE)"
	@echo "Chat:    $(CHAT_QUAY_IMAGE)"
	@echo "Monitor: $(MONITOR_QUAY_IMAGE)"

OC ?= oc
KUSTOMIZE_OVERLAY := deploy/overlays/openshift

openshift-deploy:
	$(OC) apply -k $(KUSTOMIZE_OVERLAY)
	@echo ""
	@echo "Demo label: demo=gen-ai-k8s-playground"
	@echo "  $(OC) get all,route -l demo=gen-ai-k8s-playground -n gen-ai-playground"
	@echo ""
	@echo "Routes:"
	@$(OC) get route -n gen-ai-playground -o custom-columns=NAME:.metadata.name,URL:.spec.host --no-headers 2>/dev/null || true

openshift-delete:
	$(OC) delete -k $(KUSTOMIZE_OVERLAY) --ignore-not-found
