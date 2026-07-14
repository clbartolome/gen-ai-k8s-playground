PROJECT := gen-ai-k8s-playground
PODMAN ?= podman

AGENT_IMAGE := $(PROJECT)/agent:local
CHAT_IMAGE := $(PROJECT)/chat:local
NETWORK := $(PROJECT)-net
AGENT_CONTAINER := $(PROJECT)-agent
CHAT_CONTAINER := $(PROJECT)-chat

QUAY_REGISTRY ?= quay.io
QUAY_USER ?= calopezb
QUAY_TAG ?= latest
AGENT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-agent:$(QUAY_TAG)
CHAT_QUAY_IMAGE := $(QUAY_REGISTRY)/$(QUAY_USER)/$(PROJECT)-chat:$(QUAY_TAG)

AGENT_PORT ?= 8080
CHAT_PORT ?= 5000
DELAY_SECONDS ?= 5
AGENT_TIMEOUT ?= 120

.PHONY: local-build local-run local-clean quay-login quay-upload openshift-deploy openshift-delete

local-build:
	$(PODMAN) build -t $(AGENT_IMAGE) -f components/agent/Containerfile components/agent
	$(PODMAN) build -t $(CHAT_IMAGE) -f components/chat/Containerfile components/chat

local-run:
	@$(PODMAN) network exists $(NETWORK) || $(PODMAN) network create $(NETWORK)
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(AGENT_CONTAINER)
	$(PODMAN) run -d --name $(AGENT_CONTAINER) --network $(NETWORK) \
		-p $(AGENT_PORT):8080 -e DELAY_SECONDS=$(DELAY_SECONDS) $(AGENT_IMAGE)
	$(PODMAN) run -d --name $(CHAT_CONTAINER) --network $(NETWORK) \
		-p $(CHAT_PORT):5000 \
		-e AGENT_URL=http://$(AGENT_CONTAINER):8080 \
		-e AGENT_TIMEOUT=$(AGENT_TIMEOUT) $(CHAT_IMAGE)
	@echo "Chat:  http://localhost:$(CHAT_PORT)"
	@echo "Agent: http://localhost:$(AGENT_PORT)/debug"

local-clean:
	-$(PODMAN) rm -f $(CHAT_CONTAINER) $(AGENT_CONTAINER)
	-$(PODMAN) rmi $(AGENT_IMAGE) $(CHAT_IMAGE)
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
	$(PODMAN) push $(AGENT_QUAY_IMAGE)
	$(PODMAN) push $(CHAT_QUAY_IMAGE)
	@echo ""
	@echo "Agent: $(AGENT_QUAY_IMAGE)"
	@echo "Chat:  $(CHAT_QUAY_IMAGE)"

OC ?= oc
KUSTOMIZE_OVERLAY := deploy/overlays/openshift

openshift-deploy:
	$(OC) apply -k $(KUSTOMIZE_OVERLAY)
	@echo ""
	@echo "Demo label: demo=gen-ai-k8s-playground"
	@echo "  $(OC) get all,route -l demo=gen-ai-k8s-playground -n gen-ai-playground"
	@echo ""
	@echo "Routes:"
	@$(OC) get route -n gen-ai-playground chat agent -o custom-columns=NAME:.metadata.name,URL:.spec.host --no-headers 2>/dev/null || true

openshift-delete:
	$(OC) delete -k $(KUSTOMIZE_OVERLAY) --ignore-not-found
