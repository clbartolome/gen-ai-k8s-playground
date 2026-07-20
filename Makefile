PROJECT := gen-ai-k8s-playground
PODMAN ?= podman

AGENT_IMAGE := $(PROJECT)/agent:local
CHAT_IMAGE := $(PROJECT)/chat:local
TOOLS_IMAGE := $(PROJECT)/tools:local
NETWORK := $(PROJECT)-net
AGENT_CONTAINER := $(PROJECT)-agent
CHAT_CONTAINER := $(PROJECT)-chat
TOOLS_CONTAINER := $(PROJECT)-tools

QUAY_REGISTRY ?= quay.io
QUAY_USER ?= calopezb
QUAY_TAG ?= latest
AGENT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-agent:$(QUAY_TAG)
CHAT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-chat:$(QUAY_TAG)
TOOLS_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-tools:$(QUAY_TAG)

AGENT_PORT ?= 8080
CHAT_PORT ?= 5000
TOOLS_PORT ?= 9000
AGENT_TIMEOUT ?= 180
ENV_FILE := .env

.PHONY: local-build local-run local-clean quay-login quay-upload openshift-deploy openshift-delete deploy-itsm delete-itsm

local-build:
	$(PODMAN) build -t $(AGENT_IMAGE) -f components/agent/Containerfile components/agent
	$(PODMAN) build -t $(CHAT_IMAGE) -f components/chat/Containerfile components/chat
	$(PODMAN) build -t $(TOOLS_IMAGE) -f components/tools/Containerfile components/tools

local-run:
	@test -f $(ENV_FILE) || (echo "Missing $(ENV_FILE). Copy .env.example to .env and fill in your values." && exit 1)
	@$(PODMAN) network exists $(NETWORK) || $(PODMAN) network create $(NETWORK)
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(AGENT_CONTAINER) $(TOOLS_CONTAINER)
	$(PODMAN) run -d --name $(TOOLS_CONTAINER) --network $(NETWORK) \
		-p $(TOOLS_PORT):9000 -p 9001:9001 \
		$(TOOLS_IMAGE)
	$(PODMAN) run -d --name $(AGENT_CONTAINER) --network $(NETWORK) \
		-p $(AGENT_PORT):8080 --env-file $(ENV_FILE) \
		-e MCP_URL=http://$(TOOLS_CONTAINER):9001 \
		$(AGENT_IMAGE)
	$(PODMAN) run -d --name $(CHAT_CONTAINER) --network $(NETWORK) \
		-p $(CHAT_PORT):5000 \
		-e AGENT_URL=http://$(AGENT_CONTAINER):8080 \
		-e AGENT_TIMEOUT=$(AGENT_TIMEOUT) $(CHAT_IMAGE)
	@echo "Chat:  http://localhost:$(CHAT_PORT)"
	@echo "Agent: http://localhost:$(AGENT_PORT)/health"
	@echo "Tools: http://localhost:$(TOOLS_PORT)"

local-clean:
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(AGENT_CONTAINER) $(TOOLS_CONTAINER)
	-$(PODMAN) rmi $(AGENT_IMAGE) $(CHAT_IMAGE) $(TOOLS_IMAGE)
	-$(PODMAN) network rm $(NETWORK)

quay-login:
	@if [ -n "$$QUAY_TOKEN" ]; then \
		echo "$$QUAY_TOKEN" | $(PODMAN) login $(QUAY_REGISTRY) -u $(QUAY_USER) --password-stdin; \
	else \
		$(PODMAN) login $(QUAY_REGISTRY) -u $(QUAY_USER); \
	fi

quay-upload: local-build quay-login
	$(PODMAN) tag $(AGENT_IMAGE) $(AGENT_QUAY_IMAGE)
	$(PODMAN) tag $(CHAT_IMAGE) $(CHAT_QUAY_IMAGE)
	$(PODMAN) tag $(TOOLS_IMAGE) $(TOOLS_QUAY_IMAGE)
	$(PODMAN) push $(AGENT_QUAY_IMAGE)
	$(PODMAN) push $(CHAT_QUAY_IMAGE)
	$(PODMAN) push $(TOOLS_QUAY_IMAGE)
	@echo ""
	@echo "Agent: $(AGENT_QUAY_IMAGE)"
	@echo "Chat:  $(CHAT_QUAY_IMAGE)"
	@echo "Tools: $(TOOLS_QUAY_IMAGE)"

OC ?= oc
KUSTOMIZE_OVERLAY := deploy/overlays/openshift
ITSM_KUSTOMIZE := deploy/itsm

openshift-deploy:
	$(OC) apply -k $(KUSTOMIZE_OVERLAY)
	@echo ""
	@echo "Demo label: demo=gen-ai-k8s-playground"
	@echo "  $(OC) get all,route -l demo=gen-ai-k8s-playground -n gen-ai-playground"
	@echo ""
	@echo "Routes:"
	@$(OC) get route -n gen-ai-playground chat tools -o custom-columns=NAME:.metadata.name,URL:.spec.host --no-headers 2>/dev/null || true

openshift-delete:
	$(OC) delete -k $(KUSTOMIZE_OVERLAY) --ignore-not-found

deploy-itsm:
	$(OC) apply -k $(ITSM_KUSTOMIZE)
	@echo ""
	@echo "ITSM in namespace: gen-ai-playground"
	@$(OC) get route -n gen-ai-playground itsm-app -o custom-columns=NAME:.metadata.name,URL:.spec.host --no-headers 2>/dev/null || true

delete-itsm:
	$(OC) delete -k $(ITSM_KUSTOMIZE) --ignore-not-found
