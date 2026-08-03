"""Saat çapası — talimat her turda TSİ saatini taşır (4 Ağu 01:19 vakası).

Vaka: model saat dilimi çapasız çalışıyordu; kayıtları UTC yazıyor (doğru) ama
Kadir'e saatleri UTC söylüyordu ("Ayağa kalk" 23:00Z = 02:00 TSİ iken "23:00'te"
dedi). Statik INSTRUCTION'a saat gömülemez — talimat imaj derlendiği anda donar.
ADK'nın InstructionProvider'ı (llm_agent.py: `instruction: Union[str,
InstructionProvider]`) her turda çağrılır; çapa oraya eklenir.

Türkiye 2016'dan beri kalıcı UTC+3 (DST yok); sabit offset kullanılır ki slim
imaja tzdata bağımlılığı girmesin.
"""
from datetime import datetime

from app import agent as agent_mod
from app.agent import build_agent
from app.memory import Memory
from tests.fakes import FakeDB


class FakeAudit:
    def write(self, entry):
        pass


def _frozen():
    return datetime(2026, 8, 4, 1, 30)


def test_provider_appends_the_current_tsi_time_to_the_instruction():
    """ÖLDÜREN MUTASYON: provider'ın `now_fn` çıktısını metne gömmemesi —
    model yine saatsiz kalır ve UTC damgalarını olduğu gibi okur."""
    text = agent_mod.instruction_with_clock(None, now_fn=_frozen)

    assert text.startswith(agent_mod.INSTRUCTION)
    assert "01:30" in text
    assert "04.08.2026" in text
    assert "TSİ" in text


def test_the_rule_tells_the_model_to_convert_utc_before_speaking():
    """Araç çıktılarındaki damgalar UTC kalmaya devam ediyor (kayıt biçimi
    değişmedi); dönüşüm kuralı talimatta olmazsa saat çapası tek başına
    yetmez — model 'şu an'ı bilir ama kayıttaki 23:00Z'yi yine aynen okur."""
    text = agent_mod.instruction_with_clock(None, now_fn=_frozen)

    assert "UTC" in text
    assert "3 saat" in text


def test_build_agent_wires_the_clock_provider():
    """Talimat artık string değil provider: ADK onu her turda çağırır, saat
    turdan tura tazelenir. String kalsaydı saat imaj başlangıcında donardı."""
    agent = build_agent(Memory(FakeDB()), FakeAudit())

    assert agent.instruction is agent_mod.instruction_with_clock


def test_provider_defaults_to_real_istanbul_time():
    """now_fn üretimde geçilmez: varsayılan gerçek TSİ saatidir (UTC+3 sabit).
    Yıl metinde görünmeli — donmuş/sahte bir varsayılan bunu geçemezdi."""
    text = agent_mod.instruction_with_clock(None)

    assert str(datetime.now(agent_mod.TZ_TR).year) in text
