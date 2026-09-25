"""Manager receives one task envelope, without a hard-coded tool catalog."""

import pytest

from app.harness_prompt import render_manager_prompt


def test_renders_goal_context_authority_and_evidence_rule():
    prompt = render_manager_prompt({
        "task_id": "h1_123",
        "envelope": {
            "goal": "Depodaki hatayı düzelt",
            "context": {"repo": "JARVIS"},
            "authority": "Yalnız çalışma alanındaki dosyaları değiştir",
            "success_criteria": ["İlgili test geçsin"],
        },
    })
    assert "h1_123" in prompt
    assert "Depodaki hatayı düzelt" in prompt
    assert "İlgili test geçsin" in prompt
    assert "terminalden kendin keşfet" in prompt
    assert "AI Studio/Gemini API anahtarı" in prompt


@pytest.mark.parametrize("envelope", [
    {},
    {"goal": "x", "authority": "y"},
    {"goal": "x", "success_criteria": ["z"]},
])
def test_rejects_incomplete_envelope(envelope):
    with pytest.raises(ValueError):
        render_manager_prompt({"task_id": "h1_123", "envelope": envelope})
