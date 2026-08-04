"""Ses kimliği yüzeyi TAZE GOOGLE oturumu ister (Wear W0 dal-review kuyruğu).

Neden ayrı bir kural: cihaz token'ı (`jdt_`, Wear W0) saatte 30 gün kayar
süreyle yaşar ve saatte ses kimliği yönetimi diye bir ekran YOKTUR — ne W1'de
ne W2'de. Buna karşılık bu uçlar galeriyi SİLER (`DELETE /api/voice/profile`),
ZEHİRLER (`POST /api/voice/enroll`, `PATCH /api/voice/sample`) veya uyarlama
kararını değiştirir (`history/confirm|reject`). Çalınmış bir saat Kadir'in ses
kimliğini yok edebilmemeli.

Kural tek cümle: **ses kimliği yüzeyinin TAMAMI `require_google_user`.** İki ucu
seçip diğer dördünü bırakmak, sonradan bakan için ezberlenmesi gereken bir
istisna listesi üretirdi ([[kadir-cihaz-tercihleri]] 3. ilke).

Telefon etkilenmez: telefonun her isteği zaten taze Google ID token'ı taşır.
"""
import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
import app.main as main_mod
from app import device_tokens
from app.memory import Memory
from tests.fakes import FakeDB

USER = "kadir@example.com"

# (method, path) — ses kimliği yüzeyinin TAMAMI.
VOICE_IDENTITY_ROUTES = [
    ("post", "/api/voice/enroll"),
    ("get", "/api/voice/profile"),
    ("delete", "/api/voice/profile"),
    ("patch", "/api/voice/sample/s1"),
    ("delete", "/api/voice/sample/s1"),
    ("post", "/api/voice/history/e1/confirm"),
    ("post", "/api/voice/history/e1/reject"),
]


@pytest.fixture
def client(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(main_mod, "_init", lambda: None)
    monkeypatch.setattr(main_mod, "_memory", Memory(db))
    monkeypatch.setattr(main_mod, "_enroll_db", lambda: db, raising=False)
    monkeypatch.setattr(main_mod, "_speaker_service", None)
    auth_mod.init(lambda: db)
    with TestClient(main_mod.app) as c:
        yield c, db
    main_mod.app.dependency_overrides.clear()
    auth_mod.init(None)


def _call(c, method, path, token):
    """`get`/`delete` gövde kabul etmez (TestClient imzası) — gövde YALNIZ
    gövdeli metotlara verilir."""
    headers = {"Authorization": f"Bearer {token}"}
    if method in ("post", "patch", "put"):
        body = {"clips": ["AAA="]} if path.endswith("/enroll") else {}
        return getattr(c, method)(path, headers=headers, json=body)
    return getattr(c, method)(path, headers=headers)


@pytest.mark.parametrize("method,path", VOICE_IDENTITY_ROUTES)
def test_a_device_token_cannot_touch_the_voice_identity_surface(client, method, path):
    """TAŞIYICI PİM: çalınmış saat, ses kimliğini ne siler ne zehirler.

    Bu pim düşerse 30 gün kayar süreli bir bearer, Kadir'in ses galerisini tek
    istekle silebiliyor demektir."""
    c, db = client
    minted = device_tokens.mint(db, email=USER, device="watch-ultra")

    r = _call(c, method, path, minted["token"])

    assert r.status_code == 403, f"{method.upper()} {path} cihaz token'ını kabul etti"
    assert "Google" in r.json()["detail"]


@pytest.mark.parametrize("method,path", VOICE_IDENTITY_ROUTES)
def test_the_same_routes_still_answer_a_google_session(client, method, path, monkeypatch):
    """KONTROL GRUBU: yukarıdaki 403'ler "uç kapalı" demek DEĞİL — Google
    oturumuyla aynı yollar hâlâ çalışıyor (negatif gözlem tek başına kanıt
    değildir; 3 Ağu dersi)."""
    c, _ = client
    monkeypatch.setattr(auth_mod, "verify_token_email", lambda token: USER)

    r = _call(c, method, path, "google-jwt")

    assert r.status_code != 403, f"{method.upper()} {path} Google oturumunu reddetti"
