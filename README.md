# Gen AI K8s Playground

A demo stack for IT operations assistance powered by an LLM. The agent routes user requests to domain specialists and calls external MCP servers for OpenShift, Ansible Automation Platform (AAP), and ITSM — including knowledge-base search and procedure execution.

## Components

| Component | Role |
|-----------|------|
| **Agent** | Core service. Classifies intent, runs ReAct loops, and invokes MCP tools. Persists execution traces to SQLite. |
| **Chat** | Simple web UI to send messages and read agent replies. |
| **Monitor** | React dashboard that visualizes agent traces — routing, tool calls, and step-by-step flow per thread. |

All three run as containers. Chat and Monitor talk to the Agent over HTTP.

## Configuration

Configuration is done with environment variables. For local development, put them in a `.env` file at the repo root — `make local-run` loads it into the agent container.

### Agent

The agent needs an LLM endpoint and one or more MCP servers. Only set the MCPs you plan to use.

**LLM** (required)

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_URL` | Chat completions API URL | — |
| `LLM_API_KEY` | API key | — |
| `LLM_MODEL` | Model name | — |
| `LLM_TIMEOUT` | Request timeout (seconds) | `120` |
| `LLM_MAX_TOKENS` | Max tokens per completion | `1024` |

**MCP servers** (set URL + token for each server you enable)

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENSHIFT_MCP_URL` | OpenShift MCP endpoint | — |
| `AAP_MCP_URL` | Ansible Automation Platform MCP endpoint | — |
| `AAP_MCP_TOKEN` | Bearer token for AAP MCP | — |
| `AAP_MCP_TOOLS` | Comma-separated tool allowlist | built-in list |
| `ITSM_MCP_URL` | ITSM / knowledge-base MCP endpoint | `http://itsm-app:8000/mcp/` |
| `ITSM_MCP_TOKEN` | Token for ITSM MCP | `change-me-mcp-token` |
| `ITSM_MCP_TOOLS` | Comma-separated tool allowlist | built-in list |
| `TOOLS_TIMEOUT` | MCP request timeout (seconds) | `30` |

ITSM MCP also powers **RAG** (knowledge-base search and procedure execution).

**Other**

| Variable | Description | Default |
|----------|-------------|---------|
| `SSL_VERIFY` | Verify TLS certificates for HTTPS (`true` / `false`) | `false` |
| `LOG_LEVEL` | Log verbosity (`INFO`, `DEBUG`, …) | `INFO` |
| `TRACE_DB_PATH` | SQLite path for execution traces | `/tmp/agent-traces.db` |
| `PORT` | HTTP listen port | `8080` |

## Local deployment

Requires [Podman](https://podman.io/).

1. Create a `.env` file at the repo root:

```bash
# LLM (required)
LLM_URL=https://your-llm.example.com/v1/chat/completions
LLM_API_KEY=sk-your-api-key
LLM_MODEL=your-model
LLM_TIMEOUT=120

# MCP servers — comment out or remove the ones you don't use
OPENSHIFT_MCP_URL=https://openshift-mcp.example.com/mcp/
AAP_MCP_URL=https://aap-mcp.example.com
AAP_MCP_TOKEN=your-aap-token
ITSM_MCP_URL=https://itsm-app.example.com/mcp/
ITSM_MCP_TOKEN=your-itsm-token

# Optional
SSL_VERIFY=false
LOG_LEVEL=INFO
```

2. Build and start the stack:

```bash
make local-build
make local-run
```

Open **Chat** at http://localhost:5000 and **Monitor** at http://localhost:5100.

## Chat API

The chat service exposes HTTP endpoints to push messages into the UI from external systems (monitoring, ITSM, automation, etc.). The browser polls these endpoints and renders the messages in the Slack-style channel.

Base URL: `http://localhost:5000` (or the chat route in OpenShift).

### Info messages

Push an informational message into an **existing thread**. Info messages are displayed in the thread as **Info** (they are **not** sent to the agent).

**POST** `/threads/<thread_id>/info`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | yes | Text shown in the thread |

`thread_id` is the agent thread ID (`agentThreadId` in the chat UI), assigned after the first agent reply in that thread.

**Example**

```bash
curl -X POST http://localhost:5000/threads/<thread_id>/info \
  -H "Content-Type: application/json" \
  -d '{"message": "El despliegue ha finalizado correctamente."}'
```

**Response** `201`

```json
{
  "id": "uuid",
  "thread_id": "uuid",
  "message": "El despliegue ha finalizado correctamente.",
  "created_at": 1756800000000
}
```

The UI polls `GET /threads/info?ids=<thread_id>,...` and dequeues pending info messages for the open threads.

### Incident alerts

Push an **incident alert** into the channel. Incidents appear as a new channel message (with an **INCIDENT** badge and severity), are **automatically sent to the agent** for remediation (same RAG / autoremediation flow as a documented procedure), and support unread indicators like any other thread reply.

**POST** `/incidents`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | one of `title` / `message` | Short incident title (shown as the message author in the UI) |
| `message` | string | one of `title` / `message` | Incident description / details |
| `severity` | string | no | Severity level (see below). Default: `unknown` |

**Severity values**

| Value | Typical use |
|-------|-------------|
| `critical` | Service down, major outage, immediate action |
| `high` | Serious degradation, urgent remediation |
| `medium` | Notable issue, should be addressed soon |
| `low` | Minor issue, low urgency |
| `warning` | Early warning / threshold crossed (alias of medium in the UI) |
| `info` | Informational alert, no immediate action |
| `unknown` | Severity not specified (default) |

**Example**

```bash
curl -X POST http://localhost:5000/incidents \
  -H "Content-Type: application/json" \
  -d '{
    "title": "High CPU on web-server-prod",
    "message": "CPU utilization above 95% for 10 minutes on node worker-03.",
    "severity": "critical"
  }'
```

**Response** `201`

```json
{
  "id": "uuid",
  "title": "High CPU on web-server-prod",
  "message": "CPU utilization above 95% for 10 minutes on node worker-03.",
  "severity": "critical",
  "created_at": 1756800000000
}
```

The UI polls `GET /incidents`, ingests each alert, and starts agent processing with `category: RAG` (knowledge-base remediation lookup and autoremediation offer).

## OpenShift deployment

### Build and push images

Log in and push with:

```bash
make quay-upload
```

This builds all three images locally, tags them, and pushes to:

```
quay.io/calopezb/gen-ai-k8s-playground-agent:latest
quay.io/calopezb/gen-ai-k8s-playground-chat:latest
quay.io/calopezb/gen-ai-k8s-playground-monitor:latest
```

Override defaults if needed:

```bash
QUAY_USER=myuser QUAY_TAG=v0.1.0 make quay-upload
```

If you change `QUAY_USER` or `QUAY_TAG`, update `deploy/overlays/openshift/kustomization.yaml` to match.

### Deploy to the cluster

Namespace and configuration are **not** managed by Kustomize. Create them manually first:

**1. Namespace**

```bash
oc create namespace gen-ai-playground
```

**2. Secret** — copy `deploy/config.secret.env.example` to `deploy/config.secret.env`, fill in your values, then:

```bash
oc create secret generic gen-ai-playground-config \
  --from-env-file=deploy/config.secret.env \
  -n gen-ai-playground
```

All three components (agent, chat, monitor) load this secret via `envFrom`.

**3. Apply manifests**

```bash
make openshift-deploy
```

Check routes:

```bash
oc get route -n gen-ai-playground
```

To remove the stack (namespace and secret are kept):

```bash
make openshift-delete
```

