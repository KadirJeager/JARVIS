"""Tests for app/decision_log.py (Onay Kartı 2.0, Task 4; P5/K3).

Taşıyıcı iki pim (silinirse karar kaydının garantisi kalkar):
- `test_verify_detects_a_rewritten_entry` — geçmişe dönük düzenleme algılanır;
  zincirin tüm varlık sebebi budur.
- `test_verify_accepts_an_untouched_chain_with_frozen_clock` — aynı saniyede
  yazılan halkalar leksikografik sıralamada keyfi dizilebildiği için doğrulama
  `prev_hash` gezintisine bağlıdır; dondurulmuş saatte bile dokunulmamış zincir
  temiz sayılmalıdır.
"""
from app import decision_log
from tests.fakes import FakeDB

USER = "kadir@example.com"
OTHER = "baskasi@example.com"

# Bilinçli olarak DONMUŞ saat: üç halka da aynı `at` değerini taşır. Leksikografik
# sıralama bunları ayırt edemez; test yalnızca prev_hash gezintisiyle geçer.
FROZEN_AT = "2026-08-22T12:00:00+00:00"


def _ring(db, approval_id):
    return db.collection(decision_log.COLLECTION).document(approval_id).get().to_dict()


def _rings(db):
    return db.collection(decision_log.COLLECTION)


def _append(db, approval_id, *, user_id=USER, decision="approved", at=FROZEN_AT,
            by=USER, reason=None, reversible=False):
    return decision_log.append(
        db, approval_id=approval_id, user_id=user_id, decision=decision,
        by=by, at=at, actor="orchestrator", zone="red", cause="red_zone",
        operand="Ayşe", reversible=reversible, reason=reason)


def test_first_entry_has_an_empty_prev_hash():
    db = FakeDB()
    h = _append(db, "a1")

    entry = _ring(db, "a1")
    assert entry["prev_hash"] == ""
    assert entry["hash"] == h


def test_each_entry_chains_to_the_previous():
    db = FakeDB()
    h1 = _append(db, "a1")
    h2 = _append(db, "a2")

    assert h2 != h1
    assert _ring(db, "a2")["prev_hash"] == h1


def test_verify_accepts_an_untouched_chain():
    db = FakeDB()
    for i in range(3):
        _append(db, f"a{i}")

    assert decision_log.verify(db, USER) == (True, None)


def test_verify_accepts_an_untouched_chain_with_frozen_clock():
    """Aynı `at` değerine sahip halkalar: sıralama-bağımlı bir doğrulama burada
    yanlış alarm üretirdi (bkz. modül docstring)."""
    db = FakeDB()
    for i in range(4):
        _append(db, f"a{i}", at=FROZEN_AT)

    assert decision_log.verify(db, USER) == (True, None)


def test_verify_detects_a_rewritten_entry():
    """Tüm nokta: verilmiş bir kararın sonradan düzenlenebilmesi ALGILANMALI."""
    db = FakeDB()
    _append(db, "a1", decision="rejected", reason="yanlış kişi")
    _append(db, "a2")
    db.collection(decision_log.COLLECTION).document("a1").set(
        {"decision": "approved"}, merge=True)

    ok, broken = decision_log.verify(db, USER)
    assert ok is False
    assert broken == "a1"


def test_verify_reports_the_earliest_unreachable_ring_after_a_deletion():
    db = FakeDB()
    _append(db, "a1")
    h2 = _append(db, "a2")
    _append(db, "a3")
    db.collection(decision_log.COLLECTION).document("a1").delete()

    ok, broken = decision_log.verify(db, USER)
    # a1 gitti: kök yok; rapor en eski halkayı approval_id tie-break'iyle "a2"
    # olarak seçer — deterministik, hedge'siz.
    assert ok is False
    assert broken == "a2"
    assert h2  # zincir kurulurken gerçekten bağlanmıştı


def test_verify_isolated_per_user():
    db = FakeDB()
    _append(db, "a1", user_id=USER)
    _append(db, "b1", user_id=OTHER)
    _append(db, "a2", user_id=USER)

    assert decision_log.verify(db, USER) == (True, None)
    assert decision_log.verify(db, OTHER) == (True, None)


def test_second_append_on_same_approval_writes_nothing_new():
    """doc id = approval_id: bir onayın yaşamında bir terminal geçiş vardır;
    ikinci append (yarış kaybedeni) yeni halka yazmaz ve "" döner."""
    db = FakeDB()
    first = _append(db, "a1")
    second = _append(db, "a1")

    assert first != ""
    assert second == ""
    assert len(list(_rings(db).stream())) == 1
    assert decision_log.verify(db, USER) == (True, None)


def test_reason_is_carried_only_by_rejections():
    db = FakeDB()
    _append(db, "a1", decision="rejected", reason="Yanlış kişi")
    _append(db, "a2")

    assert _ring(db, "a1")["reason"] == "Yanlış kişi"
    assert _ring(db, "a2")["reason"] is None
