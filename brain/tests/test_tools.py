from app import tools
from app.agent import build_agent
from app.memory import Memory
from tests.fakes import FakeDB


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def test_tools_delegate_to_memory():
    tools.init(Memory(FakeDB()))
    tools.remember_fact("test gerçeği")
    assert tools.search_memory("gerçeği")[0]["text"].startswith("test")
    assert tools.get_user_profile() == {}


def test_agent_wires_tools_and_policy():
    agent = build_agent(Memory(FakeDB()), FakeAudit())
    tool_names = {getattr(t, "__name__", getattr(t, "name", "")) for t in agent.tools}
    assert "get_user_profile" in tool_names
    assert agent.before_tool_callback is not None
    assert "Jarvis" in agent.instruction


def test_agent_defaults_to_config_model_name():
    from app import config

    agent = build_agent(Memory(FakeDB()), FakeAudit())
    assert agent.model == config.MODEL_NAME


def test_agent_uses_explicit_model_override():
    agent = build_agent(Memory(FakeDB()), FakeAudit(), model="gemini-3.1-flash-live-preview")
    assert agent.model == "gemini-3.1-flash-live-preview"
