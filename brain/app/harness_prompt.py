"""Render the same manager task envelope for any selected harness."""

import json
from pathlib import Path


_INSTRUCTIONS = Path(__file__).with_name("manager_instructions.md")
_MAX_PROMPT_BYTES = 64 * 1024


def render_manager_prompt(record: dict) -> str:
    """Give the manager goal, context, authority and success criteria.

    Tool discovery belongs to the manager's terminal; this function contains
    no per-CLI routing or executable names.
    """
    if not isinstance(record, dict):
        raise ValueError("task record must be a dict")
    envelope = record.get("envelope")
    if not isinstance(envelope, dict):
        raise ValueError("task envelope must be a dict")
    for field in ("goal", "authority", "success_criteria"):
        if field not in envelope or not envelope[field]:
            raise ValueError(f"task envelope requires {field}")
    if not isinstance(record.get("task_id"), str) or not record["task_id"]:
        raise ValueError("task record requires task_id")
    instructions = _INSTRUCTIONS.read_text(encoding="utf-8")
    payload = {
        "task_id": record["task_id"],
        "goal": envelope["goal"],
        "context": envelope.get("context", {}),
        "authority": envelope["authority"],
        "success_criteria": envelope["success_criteria"],
    }
    prompt = instructions + "\n\n## Görev zarfı\n\n" + json.dumps(
        payload, ensure_ascii=False, indent=2,
    )
    if len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
        raise ValueError("manager prompt exceeds size limit")
    return prompt
