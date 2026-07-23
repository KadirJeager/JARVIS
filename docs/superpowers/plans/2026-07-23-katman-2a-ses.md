# Katman 2a — Ses Geçidi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gemini Live tabanlı ses geçidi: tarayıcı mikrofonundan (geçiş istemcisi PWA) WebSocket üzerinden canlı sesli konuşma, canlı transkript, aynı politika katmanı ve hafıza. "Bitti" ölçütü: PWA'daki mikrofon butonuyla Jarvis'le sesli konuşuyorum, transkript sohbete düşüyor, araç çağrıları yine audit'leniyor.

**Architecture:** Aynı kod tabanı (`brain/`), İKİNCİ Cloud Run servisi `jarvis-voice` (North Star §4.2: ses geçidi ayrı servis). FastAPI'ye `/ws/voice` WebSocket endpoint'i eklenir: istemciden ilk mesaj auth (ID token), sonrası binary PCM16@16kHz ses; sunucudan binary PCM16@24kHz ses + JSON olaylar (transkript, turn_complete). Köprü: WebSocket ↔ ADK `LiveRequestQueue`/`Runner.run_live` (deneysel API — her imza kurulu kaynaktan doğrulanır). Ajan, araçlar ve politika katmanı Katman 1'dekiyle AYNI nesnelerdir — sesli oturumda da her araç çağrısı matristen geçer.

**Tech Stack:** Mevcut brain paketi + `google-adk` run_live, `google-genai` types. Live model: `gemini-2.5-flash-native-audio-latest` (env `JARVIS_LIVE_MODEL`; -latest kuralı). PWA tarafı: `getUserMedia` + `AudioWorklet` (PCM16 downsample) — kütüphanesiz.

## Global Constraints

- Katman 1 Global Constraints aynen geçerli (proje/bölge/dil kuralları, trailer, sırlar, İlke 1: istemcide sıfır zeka).
- **WebSocket sözleşmesi Android'in (2b) kalıcı sözleşmesidir** — PWA geçici, sözleşme kalıcı. Sözleşme bu planın Task 1'inde tek dosyada tanımlanır; sonraki hiçbir görev onu plan dışı değiştiremez.
- Ses formatları sabit: giriş PCM16 mono 16kHz, çıkış PCM16 mono 24kHz (Live API yerlileri).
- `run_live` DENEYSEL: her ADK/genai imzası implementasyon anında `.venv` içindeki kurulu kaynaktan doğrulanır (tahmin yasak); sapma rapor edilir.
- Sesli oturumlar da politika + audit'ten geçer; ses geçidi ayrı bir güvenlik rejimi AÇMAZ.
- PWA sesli modu asgaridir: tek buton, transkript satırları, cila yok (geçiş istemcisi).

---

### Task 1: WebSocket ses sözleşmesi (kalıcı) + Live konfig

**Files:**
- Create: `brain/app/voice_protocol.py`
- Modify: `brain/app/config.py` (LIVE_MODEL ekle)
- Test: `brain/tests/test_voice_protocol.py`

**Interfaces:**
- Produces: `LIVE_MODEL` (config, env `JARVIS_LIVE_MODEL`, default `gemini-2.5-flash-native-audio-latest`); `voice_protocol.py` sabitleri: `AUDIO_IN_RATE=16000`, `AUDIO_OUT_RATE=24000`, `AUDIO_MIME_IN="audio/pcm;rate=16000"`; JSON olay kurucuları `evt_transcript(role: str, text: str) -> dict` (`{"type":"transcript","role":role,"text":text}`), `evt_turn_complete() -> dict`, `evt_error(message: str) -> dict`; istemci ilk mesajı çözücü `parse_hello(raw: str) -> str` (JSON `{"token": "..."}` → token; bozuksa `ValueError`).
- Consumes: yok.

- [ ] **Step 1: Failing testler** (`brain/tests/test_voice_protocol.py`)

```python
import json

import pytest

from app import config
from app.voice_protocol import (
    AUDIO_IN_RATE, AUDIO_OUT_RATE, evt_error, evt_transcript,
    evt_turn_complete, parse_hello,
)


def test_rates_fixed_by_contract():
    assert AUDIO_IN_RATE == 16000 and AUDIO_OUT_RATE == 24000


def test_live_model_default_uses_latest_alias():
    assert "latest" in config.LIVE_MODEL


def test_events_shape():
    assert evt_transcript("user", "selam") == {"type": "transcript", "role": "user", "text": "selam"}
    assert evt_turn_complete() == {"type": "turn_complete"}
    assert evt_error("x")["type"] == "error"


def test_parse_hello_roundtrip_and_reject():
    assert parse_hello(json.dumps({"token": "abc"})) == "abc"
    with pytest.raises(ValueError):
        parse_hello("not json")
    with pytest.raises(ValueError):
        parse_hello(json.dumps({"no_token": 1}))
```

- [ ] **Step 2: RED** — `.venv/bin/pytest tests/test_voice_protocol.py -q` → `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`config.py`'ye ekle:

```python
LIVE_MODEL = os.environ.get("JARVIS_LIVE_MODEL", "gemini-2.5-flash-native-audio-latest")
```

`voice_protocol.py`:

```python
"""Permanent WebSocket voice contract (Katman 2b Android reuses this verbatim).

Client -> server: first TEXT frame is a hello JSON {"token": "<google id token>"};
after auth, BINARY frames are raw PCM16 mono 16kHz microphone audio.
Server -> client: BINARY frames are raw PCM16 mono 24kHz model audio;
TEXT frames are JSON events built by the evt_* helpers below.
"""
import json

AUDIO_IN_RATE = 16000
AUDIO_OUT_RATE = 24000
AUDIO_MIME_IN = "audio/pcm;rate=16000"


def evt_transcript(role: str, text: str) -> dict:
    return {"type": "transcript", "role": role, "text": text}


def evt_turn_complete() -> dict:
    return {"type": "turn_complete"}


def evt_error(message: str) -> dict:
    return {"type": "error", "message": message}


def parse_hello(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("hello frame is not JSON") from exc
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("hello frame missing token")
    return token
```

- [ ] **Step 4: GREEN** — `.venv/bin/pytest -q` (tümü; 26 + 4 beklenir)

- [ ] **Step 5: Commit** — `feat: permanent websocket voice contract and live model config` + trailer

---

### Task 2: Ses köprüsü — WebSocket ↔ run_live

**Files:**
- Create: `brain/app/voice.py`
- Modify: `brain/app/main.py` (router include)
- Test: `brain/tests/test_voice.py`

**Interfaces:**
- Consumes: `parse_hello`/evt_* (Task 1), `auth.verify_token_email(token) -> str` — DİKKAT: auth.py'de şu an yalnız FastAPI dependency'si var; bu görevde `require_user`'ın gövdesinden token-doğrulama çekirdeği `verify_token_email(token: str) -> str` fonksiyonuna çıkarılır (401/403 mantığı HTTPException yerine `PermissionError` fırlatır; `require_user` bu çekirdeği sarar — mevcut 26 test yeşil kalmalı).
- Produces: `voice.router` (FastAPI `APIRouter`, `/ws/voice`); `voice.VoiceBridge(runner, session_service)` sınıfı — test edilebilir çekirdek: `async run(ws, user_id)`. `main.py`: `app.include_router(voice.router)` + `get_runner()` erişimi (mevcut `_init()`/`_runner` global'ini `voice`'a veren küçük bir accessor: `main.get_runner_and_sessions()`).

- [ ] **Step 1: ADK imzalarını kurulu kaynaktan doğrula (kod yazmadan)**

`.venv` içinde `google/adk/agents/live_request_queue.py` (LiveRequestQueue: `send_realtime(blob)`, `send_content`, `close()`), `google/adk/agents/run_config.py` (RunConfig: `response_modalities`, `output_audio_transcription`, `input_audio_transcription` alan adları), `google/adk/runners.py` (`run_live` imzası) ve `google/genai/types.py` (`Blob(data=bytes, mime_type=str)`, transcription event alanları) okunur. Bulunanlar rapora yazılır; aşağıdaki taslak koddan sapma varsa taslak koda değil KAYNAĞA uyulur.

- [ ] **Step 2: Failing testler** (`brain/tests/test_voice.py`) — köprü çekirdeği fake'lerle:

```python
import asyncio
import json

import pytest

from app.voice import VoiceBridge


class FakeWS:
    """Minimal duck-type of fastapi WebSocket used by VoiceBridge.run."""

    def __init__(self, incoming):
        self.incoming = list(incoming)  # list of dicts like starlette receive()
        self.sent = []

    async def receive(self):
        if not self.incoming:
            await asyncio.sleep(3600)
        return self.incoming.pop(0)

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))

    async def send_text(self, t):
        self.sent.append(("text", t))


class FakeQueue:
    def __init__(self):
        self.blobs = []
        self.closed = False

    def send_realtime(self, blob):
        self.blobs.append(blob)

    def close(self):
        self.closed = True


def _audio_event(data=b"\x01\x02"):
    """Shape mirrors the ADK live event surface VoiceBridge reads."""
    class P:
        inline_data = type("D", (), {"data": data, "mime_type": "audio/pcm"})()
        text = None
    class C:
        parts = [P()]
        role = "model"
    class E:
        content = C()
        turn_complete = False
        input_transcription = None
        output_transcription = None
    return E()


@pytest.mark.asyncio
async def test_bridge_forwards_mic_bytes_to_queue_and_audio_back():
    q = FakeQueue()

    async def fake_events():
        yield _audio_event(b"\xaa\xbb")

    ws = FakeWS([{"type": "websocket.receive", "bytes": b"\x00\x01"}])
    bridge = VoiceBridge(runner=None, session_service=None)
    await bridge._pump_events(fake_events(), ws)          # model -> client
    await bridge._pump_mic_once(ws, q)                    # client -> queue
    assert q.blobs and q.blobs[0].data == b"\x00\x01"
    assert ("bytes", b"\xaa\xbb") in ws.sent
```

(İkinci test: `turn_complete=True` olayı `{"type":"turn_complete"}` TEXT frame'i üretmeli; üçüncü test: transcription taşıyan olay `evt_transcript` üretmeli — Step 1'de doğrulanan gerçek alan adlarına göre yazılır.)

- [ ] **Step 3: RED**, sonra **Step 4: Implement** — taslak (Step 1 doğrulamasıyla düzeltilecek):

```python
"""Voice gateway: bridges a WebSocket to an ADK live session (North Star §4.2).

Contract: see voice_protocol.py. The same agent/policy/audit as text chat.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig
from google.genai import types

from . import voice_protocol as vp
from .auth import verify_token_email

router = APIRouter()


class VoiceBridge:
    def __init__(self, runner, session_service):
        self.runner = runner
        self.session_service = session_service

    async def _pump_mic_once(self, ws, queue) -> bool:
        msg = await ws.receive()
        if msg.get("type") == "websocket.disconnect":
            queue.close()
            return False
        if data := msg.get("bytes"):
            queue.send_realtime(types.Blob(data=data, mime_type=vp.AUDIO_MIME_IN))
        return True

    async def _pump_events(self, events, ws) -> None:
        async for event in events:
            # exact field names verified in Task 2 Step 1 against installed ADK
            if getattr(event, "turn_complete", False):
                await ws.send_text(json.dumps(vp.evt_turn_complete()))
            for tr_attr, role in (("input_transcription", "user"), ("output_transcription", "jarvis")):
                tr = getattr(event, tr_attr, None)
                if tr and getattr(tr, "text", None):
                    await ws.send_text(json.dumps(vp.evt_transcript(role, tr.text)))
            content = getattr(event, "content", None)
            for part in (getattr(content, "parts", None) or []):
                blob = getattr(part, "inline_data", None)
                if blob and blob.data:
                    await ws.send_bytes(blob.data)

    async def run(self, ws: WebSocket, user_id: str) -> None:
        session_id = f"voice-{user_id}"
        session = await self.session_service.get_session(
            app_name="jarvis", user_id=user_id, session_id=session_id
        ) or await self.session_service.create_session(
            app_name="jarvis", user_id=user_id, session_id=session_id
        )
        queue = LiveRequestQueue()
        run_config = RunConfig(response_modalities=["AUDIO"])  # + transcription alanları Step 1'e göre
        events = self.runner.run_live(
            user_id=user_id, session_id=session_id,
            live_request_queue=queue, run_config=run_config,
        )
        pump_out = asyncio.create_task(self._pump_events(events, ws))
        try:
            while await self._pump_mic_once(ws, queue):
                pass
        finally:
            pump_out.cancel()


@router.websocket("/ws/voice")
async def ws_voice(ws: WebSocket) -> None:
    await ws.accept()
    try:
        hello = await ws.receive_text()
        email = verify_token_email(vp.parse_hello(hello))
    except (ValueError, PermissionError):
        await ws.send_text(json.dumps(vp.evt_error("Giriş doğrulanamadı")))
        await ws.close(code=4401)
        return
    from . import main
    runner, sessions = main.get_runner_and_sessions()
    try:
        await VoiceBridge(runner, sessions).run(ws, user_id=email)
    except Exception:
        logging.exception("voice bridge failed for %s", email)
        await ws.send_text(json.dumps(vp.evt_error("Sesli oturum düştü, tekrar bağlan")))
        await ws.close(code=1011)
```

- [ ] **Step 5: GREEN** — tüm suite (26+4+~3). auth refactor sonrası eski testler de yeşil.
- [ ] **Step 6: Commit** — `feat: voice gateway bridging websocket to adk live session` + trailer

---

### Task 3: PWA sesli mod (geçiş istemcisi, asgari)

**Files:**
- Modify: `brain/web/index.html` (mik butonu + ws URL global), `brain/web/app.js`
- Create: `brain/web/audio-worklet.js`

**Interfaces:**
- Consumes: Task 1 sözleşmesi. `window.JARVIS_VOICE_URL` global'i index.html'de tanımlanır (Task 5'te gerçek URL yazılır; yerelde `ws://localhost:8080/ws/voice`).

- [ ] **Step 1:** `audio-worklet.js` — AudioWorkletProcessor: 48kHz float32 girişini 16kHz PCM16'ya indirger, 320-sample'lık chunk'ları `port.postMessage` ile yollar (tam kod planda değil kısa olduğu için: lineer decimation 3:1, Int16 dönüşümü `Math.max(-1,Math.min(1,s))*0x7FFF`).
- [ ] **Step 2:** `app.js`'e sesli mod: 🎤 butonu → `getUserMedia({audio:{sampleRate:48000,channelCount:1,echoCancellation:true}})` → worklet → WS'e binary; WS'ten binary geldiğinde 24kHz `AudioContext`'te kuyruklu oynatma (basit `AudioBufferSourceNode` zinciri); TEXT frame'lerde `transcript` olayları sohbet log'una `me`/`jarvis` balonu olarak düşer, `error` olayı balon + mod kapanışı. Hello frame: mevcut `idToken` ile `{"token": idToken}`. Buton toggle: açikken kırmızı, kapatınca track'ler stop + ws close.
- [ ] **Step 3:** Yerel smoke (mikrofonlu): `uvicorn` + tarayıcıdan konuşma — Kadir'le birlikte (HITL). Scriptli smoke Task 4'te.
- [ ] **Step 4: Commit** — `feat: minimal voice mode in transitional PWA client` + trailer

---

### Task 4: Scriptli canlı smoke + transkript kalıcılığı

**Files:**
- Modify: `brain/app/voice.py` (oturum sonu snapshot)
- Create: `scratchpad'te smoke scripti (repoya girmez)`

**Interfaces:**
- Consumes: `Memory.snapshot_session` (Katman 1).

- [ ] **Step 1:** `VoiceBridge.run` finally bloğuna: birikmiş transkript satırlarını (`self.transcript: list[dict]`, `_pump_events` doldurur) `snapshot_session(f"voice-{user_id}", user_id, {"transcript": self.transcript[-50:]})` ile yaz. Birim test: fake events ile transcript birikimi + snapshot çağrısı (fake Memory).
- [ ] **Step 2:** Scriptli canlı smoke (scratchpad, `websockets` pip'iyle): ws bağlan → hello (gcloud'dan gerçek ID token ÜRETİLEMEZ; smoke için `JARVIS_ALLOWED_EMAILS`'e ek olarak env `JARVIS_DEV_BEARER` kısa-devre... HAYIR — auth'a arka kapı AÇILMAZ. Bunun yerine smoke, `VoiceBridge`'i doğrudan Python'dan sürer: gerçek `Runner.run_live` + gerçek Live API'ye `LiveRequestQueue.send_content(text)` ile "merhaba de" gönderir, dönen olaylarda ses byte'ı VE transkript beklenir). DATA-log: gönderilen içerik, olay sayısı, ilk ses chunk boyutu, transkript metni.
- [ ] **Step 3:** Tüm suite + commit — `feat: voice session transcript snapshots` + trailer

---

### Task 5: `jarvis-voice` servisi deploy + prod smoke

- [ ] **Step 1:** Aynı imajdan ikinci servis:

```bash
cd brain && gcloud run deploy jarvis-voice --source . --region europe-west1 --project your-gcp-project \
  --allow-unauthenticated --min-instances 0 --memory 512Mi --timeout 3600 \
  --set-secrets "GOOGLE_API_KEY=gemini-api-key:latest" \
  --set-env-vars "JARVIS_OAUTH_CLIENT_ID=000000000000-tmu4im1mba53dmqj1gbhgba55v3i6hmb.apps.googleusercontent.com"
```
(`--timeout 3600`: WebSocket oturumları için istek zaman aşımı 1 saat; Cloud Run WS'i destekler, IAM rolleri Katman 1'den hazır.)

- [ ] **Step 2:** `index.html`'de `window.JARVIS_VOICE_URL = "wss://<jarvis-voice-url>/ws/voice"` yazılır; `jarvis-brain` yeniden deploy edilir (PWA güncellensin).
- [ ] **Step 3 (HITL — Kadir):** Telefonda/masaüstünde 🎤 ile canlı konuşma; transkript balonları + Firestore `sessions`'ta `voice-*` snapshot kontrolü.
- [ ] **Step 4:** Commit + ledger.

## Kapsam Dışı (bilinçli)

Android/Wear (2b/2c), "Devral" akışı ve arama kartları (telefon Katman 3'le gelir), barge-in inceliklerinin cilası, kalıcı oturum çerezi (2b'de), ses geçidinde ayrı hafıza (aynı beyin nesneleri kullanılır).
