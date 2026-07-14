PROJECT := gen-ai-k8s-playground
PODMAN ?= podman

AGENT_IMAGE := $(PROJECT)/agent:local
CHAT_IMAGE := $(PROJECT)/chat:local
NETWORK := $(PROJECT)-net
AGENT_CONTAINER := $(PROJECT)-agent
CHAT_CONTAINER := $(PROJECT)-chat

AGENT_PORT ?= 8080
CHAT_PORT ?= 5000
DELAY_SECONDS ?= 5
AGENT_TIMEOUT ?= 120

.PHONY: local-build local-run local-clean

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
