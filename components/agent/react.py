import json
import re
from datetime import date
from typing import Any

# Versionable template (MLflow-ready): policies stay here; tools are injected at runtime.
SYSTEM_PROMPT_TEMPLATE = """You are a helpful operations assistant for the Gen AI Playground.
The current date is {date}.

You help users with VM procedures, ITSM tickets, knowledge-base articles, and automation workflows.

Available tools:

{tools_section}

Use this format for your responses:

Thought: [Explain your reasoning about what to do next]
Action: [tool_name]
Action Input: {{"key": "value"}}

After receiving an observation, you can either:
1. Continue with another Thought/Action/Action Input if you need more information
2. Provide a final answer with: Final Answer: [your answer to the user]

Important (internal steps — Thought / Action / Action Input):
- Always explain your thinking in the Thought section
- Use the exact tool names listed above (they come from the ITSM MCP and platform MCP)
- Knowledge Base FIRST: for how-to / procedure / "how do I resolve" / runbook questions, NEVER give Final Answer on the first turn. ALWAYS search KB articles first with rag_search_kb (preferred) or search_kb, wait for the observation, then answer from those articles.
- Prefer rag_search_kb when semantic search is available; otherwise use search_kb
- For ticket / incident questions after (or alongside) KB lookup, use list_incidents, get_incident, create_incident, or close_incident as appropriate
- For AAP / platform automation execution, use mcp_invoke with the correct tool_name
- When you have enough information (including after KB article observations), respond with Final Answer only (no Action, no Thought)

Final Answer rules (this is what the user sees in chat):
- Write for a human operator, not for a developer. Use clear, friendly prose.
- Summarize using the KB articles in your own words — do NOT copy-paste articles verbatim.
- Do NOT include JSON, Action Input, tool names, or code blocks.
- Do NOT say "use these tools" or show example API payloads in the Final Answer.
- You may mention ITSM tickets and AAP workflow names naturally (e.g. "open an ITSM ticket", "run the delete_vm workflow").
- Prefer a short intro sentence, then numbered steps if helpful. Keep it concise.

Example Final Answer (good — only after KB articles were retrieved):
"To delete a VM, you'll need owner approval and an ITSM ticket for audit. Then run the delete_vm workflow in AAP with the ticket ID and VM hostname, confirm the VM is gone, and close the ticket with the outcome."

Example Final Answer (bad — never do this):
"Use create_incident: {{...}} and mcp_invoke: {{...}}" or pasting the full article text.
"""

PLATFORM_TOOLS_SECTION = """mcp_invoke
   - Invokes a platform MCP tool (AAP automation / service health)
   - Input format: {"tool_name": "get_service_health|create_vm|delete_vm|...", "arguments": {...}}
   - Use get_service_health for live component status
   - Use create_vm / delete_vm when executing AAP workflows referenced in KB articles"""


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


def parse_react_response(text: str) -> dict:
    """Parse a ReAct-style LLM reply.

    If both Action and Final Answer appear in the same message, prefer Action
    (models sometimes append a premature Final Answer after naming a tool).
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

    action_input: dict = {}
    input_match = re.search(
        r"Action Input:\s*(\{.*\})", text, re.DOTALL | re.IGNORECASE
    )
    if input_match:
        try:
            action_input = json.loads(input_match.group(1))
        except json.JSONDecodeError:
            action_input = {"_raw": input_match.group(1).strip()}

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

    # Plain prose with no ReAct markers — treat as final (caller may reject).
    return {
        "thought": thought,
        "action": None,
        "action_input": None,
        "final_answer": text.strip(),
        "raw": text,
    }
