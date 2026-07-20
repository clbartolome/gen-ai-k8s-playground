import json
import re
from datetime import date
from typing import Any

# Versionable template (MLflow-ready): policies stay here; tools are injected at runtime.
SYSTEM_PROMPT_TEMPLATE = """You are a helpful operations assistant for the Gen AI Playground.
The current date is {date}.

You help with VM procedures, ITSM tickets, knowledge-base articles, and AAP automation.

Available tools:

{tools_section}

Response format (STRICT — one step only):

Thought: [one short sentence the user can read — what you are about to do and why]
Action: [one tool_name]
Action Input: {{"key": "value"}}

OR when finished / asking the user:

Final Answer: [message to the user]

Thought style (shown live in chat):
- Plain language, present tense, no jargon dump.
- Good: "Looking up how we create VMs in the knowledge base."
- Good: "Opening an ITSM ticket, then I'll run the create_vm workflow."
- Bad: repeating the full Action Input JSON, listing every field, or saying "I will use tool X with parameters…"

Rules:
- Emit EXACTLY ONE Action per reply, then STOP and wait for Observation.
- NEVER put multiple Thought/Action blocks in the same reply.
- NEVER invent Observation results. Only continue after a real Observation.
- Action Input must be a single JSON object for that one Action.

=== HOW-TO (explain) ===
- Search KB once (rag_search_kb or search_kb), then Final Answer with a short summary.
- Do not create tickets or run AAP for pure how-to questions.

=== EXECUTE (do the work) ===
- create_vm needs: name, size (small|medium|large), network, environment (dev|test|prod), owner.
- delete_vm needs: hostname (optional environment, reason).
- If fields are missing → Final Answer asking only for those fields.
- When fields are present, steps (ONE Action each turn):
  1) KB search (once)
  2) create_incident with title + description (+ optional severity). Do not invent actor_user_id unless required by the schema.
  3) mcp_invoke create_vm or delete_vm using the ticket id from the Observation
  4) Final Answer with ticket id, hostname, IP/status
- add_comment / close_incident are optional; prefer Final Answer after a successful mcp_invoke.

mcp_invoke example:
Action: mcp_invoke
Action Input: {{"tool_name":"create_vm","arguments":{{"ticket_id":"INC-…","name":"…","size":"medium","network":"…","environment":"dev","owner":"…"}}}}

Final Answer style: friendly prose, no JSON, no tool dumps.
"""

PLATFORM_TOOLS_SECTION = """mcp_invoke
   - Platform / AAP tool bridge
   - Input: {"tool_name": "create_vm|delete_vm|get_service_health|...", "arguments": {...}}
   - create_vm args: ticket_id, name, size, network, environment, owner
   - delete_vm args: ticket_id, hostname"""


def format_tools_section(tools: list[dict[str, Any]]) -> str:
    """Render MCP tool descriptors for the system prompt."""
    if not tools:
        return "(No ITSM/KB tools available from MCP right now.)"

    blocks: list[str] = []
    for index, tool in enumerate(tools, start=1):
        name = tool.get("name") or f"tool_{index}"
        description = (tool.get("description") or "").strip() or "No description."
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        try:
            schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            schema_text = str(schema)
        blocks.append(
            f"{index}. {name}\n"
            f"   - {description}\n"
            f"   - Input schema:\n"
            f"{_indent(schema_text, 5)}"
        )
    return "\n\n".join(blocks)


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def build_system_prompt(*, tools: list[dict[str, Any]] | None = None) -> str:
    """Build the system prompt: static policies + dynamic MCP tools (+ platform mcp_invoke)."""
    tools_section = format_tools_section(tools or [])
    if PLATFORM_TOOLS_SECTION.strip():
        tools_section = f"{tools_section}\n\n{PLATFORM_TOOLS_SECTION}"
    return SYSTEM_PROMPT_TEMPLATE.format(
        date=date.today().strftime("%Y-%m-%d"),
        tools_section=tools_section,
    )


def extract_prompts(messages: list[dict]) -> dict[str, str]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    user_parts = [m["content"] for m in messages if m.get("role") == "user"]
    return {
        "user": "\n\n".join(user_parts),
        "system": "\n\n".join(system_parts),
    }


def _extract_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_balanced_json(text: str, start: int) -> str | None:
    """Return the JSON object starting at text[start] ('{'), respecting strings."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_action_input(text: str) -> dict:
    """Parse only the first Action Input JSON object (ignore later stacked Actions)."""
    match = re.search(r"Action Input:\s*\{", text, re.IGNORECASE)
    if not match:
        return {}
    blob = _extract_balanced_json(text, match.end() - 1)
    if not blob:
        return {}
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else {"_raw": blob}
    except json.JSONDecodeError:
        return {"_raw": blob}


def format_single_step(
    *,
    thought: str | None,
    action: str,
    action_input: dict | None,
) -> str:
    """Canonical one-Action message stored in chat history (avoids multi-step dumps)."""
    payload = action_input or {}
    return (
        f"Thought: {thought or 'Next step.'}\n"
        f"Action: {action}\n"
        f"Action Input: {json.dumps(payload, ensure_ascii=False)}"
    )


def parse_react_response(text: str) -> dict:
    """Parse a ReAct-style LLM reply.

    Only the first Action / Action Input is used. Prefer Action over Final Answer
    when both appear in the same message.
    """
    thought = _extract_group(
        re.compile(
            r"Thought:\s*(.+?)(?=Action:|Final Answer:|$)",
            re.DOTALL | re.IGNORECASE,
        ),
        text,
    )
    action = _extract_group(
        re.compile(r"Action:\s*([A-Za-z0-9_]+)", re.IGNORECASE),
        text,
    )
    action_input = extract_action_input(text) if action else {}

    if action:
        return {
            "thought": thought,
            "action": action,
            "action_input": action_input,
            "final_answer": None,
            "raw": text,
        }

    final_answer = _extract_group(
        re.compile(r"Final Answer:\s*(.+)", re.DOTALL | re.IGNORECASE),
        text,
    )
    if final_answer:
        return {
            "thought": thought,
            "action": None,
            "action_input": None,
            "final_answer": final_answer,
            "raw": text,
        }

    return {
        "thought": thought,
        "action": None,
        "action_input": None,
        "final_answer": text.strip(),
        "raw": text,
    }
