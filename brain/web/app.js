let idToken = null;
const sessionId = "web-" + new Date().toISOString().slice(0, 10);

window.onload = () => {
  google.accounts.id.initialize({
    client_id: window.JARVIS_CLIENT_ID,
    callback: (resp) => {
      idToken = resp.credential;
      document.getElementById("signin").hidden = true;
      document.getElementById("log").hidden = false;
      document.getElementById("f").hidden = false;
    },
  });
  google.accounts.id.renderButton(document.getElementById("gbtn"), { theme: "filled_black" });
};

function addMsg(text, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.textContent = text;
  const log = document.getElementById("log");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const inp = document.getElementById("inp");
  const message = inp.value.trim();
  if (!message) return;
  inp.value = "";
  addMsg(message, "me");
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + idToken },
      body: JSON.stringify({ session_id: sessionId, message }),
    });
    if (!r.ok) throw new Error("HTTP " + r.status);
    addMsg((await r.json()).reply, "jarvis");
  } catch (err) {
    addMsg("Hata: " + err.message + " (oturum süresi dolduysa sayfayı yenile)", "jarvis");
  }
});

// --- Voice mode (Katman 2a Task 3, minimal transitional client) ---
// Wire contract: brain/app/voice_protocol.py. Mic -> 16kHz PCM16 binary frames
// via audio-worklet.js; server -> 24kHz PCM16 binary frames + JSON text events.
const micBtn = document.getElementById("mic");
let voiceActive = false;
let voiceStarting = false;
let voiceWs = null;
let micStream = null;
let micCtx = null;
let micWorklet = null;
let playCtx = null;
let playNextStartTime = 0;

micBtn.addEventListener("click", () => {
  if (voiceActive || voiceStarting) {
    stopVoice();
  } else {
    startVoice();
  }
});

async function startVoice() {
  voiceStarting = true;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
  } catch (err) {
    voiceStarting = false;
    addMsg("Mikrofon erişimi alınamadı: " + err.message, "jarvis");
    return;
  }

  try {
    micCtx = new AudioContext();
    await micCtx.audioWorklet.addModule("/audio-worklet.js");
    const source = micCtx.createMediaStreamSource(micStream);
    micWorklet = new AudioWorkletNode(micCtx, "pcm-downsampler");
    source.connect(micWorklet);

    playCtx = new AudioContext({ sampleRate: 24000 });
    playNextStartTime = 0;

    const voiceUrl = window.JARVIS_VOICE_URL;
    voiceWs = new WebSocket(voiceUrl);
    voiceWs.binaryType = "arraybuffer";
    voiceWs.onopen = onVoiceOpen;
    voiceWs.onmessage = onVoiceMessage;
    voiceWs.onclose = onVoiceDrop;
    voiceWs.onerror = onVoiceDrop;
  } catch (err) {
    voiceStarting = false;
    cleanupVoice();
    addMsg("Sesli mod başlatılamadı: " + err.message, "jarvis");
  }
}

function onVoiceOpen() {
  voiceStarting = false;
  voiceActive = true;
  micBtn.classList.add("active");
  voiceWs.send(JSON.stringify({ token: idToken }));
  micWorklet.port.onmessage = (e) => {
    if (voiceWs && voiceWs.readyState === WebSocket.OPEN) voiceWs.send(e.data);
  };
}

function onVoiceMessage(e) {
  if (e.data instanceof ArrayBuffer) {
    playVoiceChunk(e.data);
    return;
  }
  let evt;
  try {
    evt = JSON.parse(e.data);
  } catch {
    return; // malformed text frame, ignore
  }
  if (evt.type === "transcript") {
    addMsg(evt.text, evt.role === "user" ? "me" : "jarvis");
  } else if (evt.type === "error") {
    addMsg("Hata: " + evt.message, "jarvis");
    stopVoice();
  }
  // turn_complete: no UI action needed in this minimal client.
}

function playVoiceChunk(buffer) {
  const int16 = new Int16Array(buffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;
  const audioBuffer = playCtx.createBuffer(1, float32.length, 24000);
  audioBuffer.copyToChannel(float32, 0);
  const src = playCtx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(playCtx.destination);
  playNextStartTime = Math.max(playCtx.currentTime, playNextStartTime);
  src.start(playNextStartTime);
  playNextStartTime += audioBuffer.duration;
}

function onVoiceDrop() {
  const wasActive = voiceActive;
  cleanupVoice();
  if (wasActive) addMsg("Sesli oturum kapandı", "jarvis");
}

function stopVoice() {
  cleanupVoice();
}

function cleanupVoice() {
  voiceActive = false;
  voiceStarting = false;
  micBtn.classList.remove("active");
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  if (micWorklet) {
    micWorklet.port.onmessage = null;
    micWorklet.disconnect();
    micWorklet = null;
  }
  if (micCtx) {
    micCtx.close().catch(() => {});
    micCtx = null;
  }
  if (playCtx) {
    playCtx.close().catch(() => {});
    playCtx = null;
  }
  if (voiceWs) {
    voiceWs.onopen = null;
    voiceWs.onmessage = null;
    voiceWs.onclose = null;
    voiceWs.onerror = null;
    if (voiceWs.readyState === WebSocket.OPEN || voiceWs.readyState === WebSocket.CONNECTING) {
      voiceWs.close();
    }
    voiceWs = null;
  }
}
