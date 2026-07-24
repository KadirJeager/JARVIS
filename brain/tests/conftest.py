import pytest

from app import voice_trust


@pytest.fixture(autouse=True)
def _isolate_voice_trust():
    """voice_trust's registry is process-local by design (see its docstring), so
    without this a test that publishes signals could change the trust another
    test's policy call resolves. Cleared before AND after so a leak shows up as
    that test failing, not as a mystery failure three files later."""
    voice_trust._signals.clear()
    yield
    voice_trust._signals.clear()
