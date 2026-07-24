from types import SimpleNamespace
from app import config, trust
from app.policy import check_zone, make_policy_callback


class FakeAudit:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)


def _tool(name):
    return SimpleNamespace(name=name)


def test_known_tool_zone():
    assert check_zone("get_user_profile") == config.ZONE_GREEN


def test_unknown_tool_defaults_to_red():
    assert check_zone("launch_missiles") == config.ZONE_RED


def test_green_tool_allowed_and_audited():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"a": 1}, None)
    assert result is None  # None => ADK actually executes the tool
    assert audit.entries[0]["decision"] == "allow"
    assert audit.entries[0]["zone"] == config.ZONE_GREEN


def test_red_tool_blocked_with_turkish_message():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"


def test_dry_run_intercepts_green_tool(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {"x": 2}, None)
    assert result is not None and "DRY-RUN" in result["result"]
    assert audit.entries[0]["decision"] == "dry_run"


def test_red_zone_blocked_even_in_dry_run(monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, None)
    assert result is not None and "POLİTİKA ENGELİ" in result["result"]
    assert audit.entries[0]["decision"] == "block"  # not "dry_run"


def _ctx(level):
    # mirrors ADK ToolContext: .state is a dict-like mapping backed by session.state
    return SimpleNamespace(state={config.TRUST_STATE_KEY: level})


def test_yellow_tool_blocked_when_trust_medium():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.MEDIUM))
    assert result is not None and "onay" in result["result"].lower()
    # distinct "confirm" decision (not "block"): YELLOW zone + non-HIGH trust
    # escalates to a confirmation request, RED-zone "block" stays untouched.
    assert audit.entries[0]["decision"] == "confirm"
    assert audit.entries[0]["trust"] == trust.MEDIUM


def test_yellow_tool_allowed_when_trust_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.HIGH))
    assert result is None
    assert audit.entries[0]["decision"] == "allow"


def test_missing_tool_context_defaults_high():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    # green tool, tool_context None (text path / existing tests) -> allow, HIGH
    result = cb(_tool("get_user_profile"), {}, None)
    assert result is None
    assert audit.entries[0]["trust"] == trust.HIGH


def test_yellow_tool_blocked_when_trust_low():
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("update_user_profile"), {}, _ctx(trust.LOW))
    assert result is not None
    assert audit.entries[0]["decision"] == "confirm"
    assert audit.entries[0]["trust"] == trust.LOW


def test_read_trust_empty_state_defaults_high():
    """tool_context.state present but empty (falsy dict) -- the `if not
    state` branch -- must default HIGH just like tool_context=None does."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, SimpleNamespace(state={}))
    assert result is None
    assert audit.entries[0]["trust"] == trust.HIGH


def test_read_trust_state_get_raising_defaults_high():
    """A truthy .state whose .get() raises (e.g. a malformed ADK State) must
    be swallowed by _read_trust's except-Exception branch and default HIGH,
    not propagate and break tool execution."""
    class RaisingState:
        def get(self, key, default=None):
            raise RuntimeError("boom")

    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, SimpleNamespace(state=RaisingState()))
    assert result is None
    assert audit.entries[0]["trust"] == trust.HIGH


def test_green_zone_medium_trust_is_allowed():
    """(GREEN, MEDIUM) matrix cell: not RED, not HIGH, not YELLOW -> allow."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("get_user_profile"), {}, _ctx(trust.MEDIUM))
    assert result is None
    assert audit.entries[0]["decision"] == "allow"


def test_red_zone_medium_trust_still_blocks():
    """(RED, MEDIUM) matrix cell: RED must block regardless of trust level,
    unlike YELLOW which only blocks below HIGH."""
    audit = FakeAudit()
    cb = make_policy_callback(audit)
    result = cb(_tool("unknown_danger"), {}, _ctx(trust.MEDIUM))
    assert result is not None and "onay" in result["result"].lower()
    assert audit.entries[0]["decision"] == "block"
