import json
import re
from datetime import date


def build_system_prompt() -> str:
    today = date.today().strftime("%Y-%m-%d")
    return f"""You are a helpful operations assistant for the Gen AI Playground.
The current date is {today}

You help users with VM procedures, ITSM tickets, and automation workflows.

Available tools:

1. rag_search
   - Searches knowledge base runbooks (VM create/delete procedures, etc.)
   - Input format: {{"query": "your search query", "max_results": 3}}
   - Use when the user asks how to do something or needs documented procedures

2. itsm_list_tickets
   - Lists ITSM tickets
   - Input format: {{"component": "chat|agent|tools", "status": "open|in_progress|resolved"}}
   - Use when the user asks about open incidents or ticket status

3. itsm_create_ticket
   - Creates an ITSM ticket
   - Input format: {{"title": "short title", "description": "details", "component": "chat", "severity": "low|medium|high"}}

4. mcp_invoke
   - Invokes an MCP tool (platform state or AAP automation workflows)
   - Input format: {{"tool_name": "get_service_health|create_vm|delete_vm|...", "arguments": {{...}}}}
   - Use get_service_health for live component status
   - Use create_vm / delete_vm when executing AAP workflows referenced in runbooks

Use this format for your responses:

Thought: [Explain your reasoning about what to do next]
Action: [tool_name]
Action Input: {{"key": "value"}}

After receiving an observation, you can either:
1. Continue with another Thought/Action/Action Input if you need more information
2. Provide a final answer with: Final Answer: [your answer to the user]

Important (internal steps — Thought / Action / Action Input):
- Always explain your thinking in the Thought section
- Choose the most appropriate tool for the task
- For procedure / how-to questions (VM create/delete, runbooks, documented workflows): NEVER give Final Answer on the first turn — ALWAYS call rag_search first and wait for the observation before answering
- For ticket questions, use itsm_list_tickets or itsm_create_ticket
- For automation execution, use mcp_invoke with the correct tool_name
- When you have enough information (including after rag_search observations), respond with Final Answer only (no Action, no Thought)

Final Answer rules (this is what the user sees in chat):
- Write for a human operator, not for a developer. Use clear, friendly prose.
- Summarize the procedure in your own words — do NOT copy-paste the runbook verbatim.
- Do NOT include JSON, Action Input, tool names (rag_search, mcp_invoke, etc.), or code blocks.
- Do NOT say "use these tools" or show example API payloads in the Final Answer.
- You may mention ITSM tickets and AAP workflow names naturally (e.g. "open an ITSM ticket", "run the delete_vm workflow").
- Prefer a short intro sentence, then numbered steps if helpful. Keep it concise.

Example Final Answer (good — only after rag_search returned runbooks):
"To delete a VM, you'll need owner approval and an ITSM ticket for audit. Then run the delete_vm workflow in AAP with the ticket ID and VM hostname, confirm the VM is gone, and close the ticket with the outcome."

Example Final Answer (bad — never do this):
"Use itsm_create_ticket: {{...}} and mcp_invoke: {{...}}" or pasting the full runbook text.
"""


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
    final_answer = _extract_group(
        re.compile(r"Final Answer:\s*(.+)", re.DOTALL | re.IGNORECASE),
        text,
    )
    if final_answer:
        thought = _extract_group(
            re.compile(r"Thought:\s*(.+?)(?=Final Answer:|$)", re.DOTALL | re.IGNORECASE),
            text,
        )
        return {
            "thought": thought,
            "action": None,
            "action_input": None,
            "final_answer": final_answer,
            "raw": text,
        }

    thought = _extract_group(
        re.compile(r"Thought:\s*(.+?)(?=Action:|$)", re.DOTALL | re.IGNORECASE),
        text,
    )
    action = _extract_group(re.compile(r"Action:\s*([A-Za-z0-9_]+)", re.IGNORECASE), text)

    action_input: dict = {}
    input_match = re.search(
        r"Action Input:\s*(\{.*\})", text, re.DOTALL | re.IGNORECASE
    )
    if input_match:
        try:
            action_input = json.loads(input_match.group(1))
        except json.JSONDecodeError:
            action_input = {"_raw": input_match.group(1).strip()}

    if not action and not final_answer:
        return {
            "thought": thought,
            "action": None,
            "action_input": None,
            "final_answer": text.strip(),
            "raw": text,
        }

    return {
        "thought": thought,
        "action": action,
        "action_input": action_input,
        "final_answer": None,
        "raw": text,
    }
