import time
import google.auth
from google.cloud import texttospeech as tts

creds, _ = google.auth.default(quota_project_id="your-gcp-project")
client = tts.TextToSpeechClient(credentials=creds)
config_req = tts.StreamingSynthesizeRequest(
    streaming_config=tts.StreamingSynthesizeConfig(
        voice=tts.VoiceSelectionParams(
            name="Kore", language_code="tr-TR",
            model_name="gemini-3.1-flash-tts-preview")))
text_req = tts.StreamingSynthesizeRequest(
    input=tts.StreamingSynthesisInput(
        text="Merhaba Kadir, ben Jarvis. Artık sesim Vertex üzerinden geliyor."))

t0 = time.monotonic()
chunks = []
first = None
for resp in client.streaming_synthesize(iter([config_req, text_req])):
    if first is None:
        first = time.monotonic() - t0
    chunks.append(bytes(resp.audio_content))
total = time.monotonic() - t0
pcm = b"".join(chunks)
open("/tmp/spike_tts.pcm", "wb").write(pcm)
print(f"chunks={len(chunks)} first_chunk={first:.2f}s total={total:.2f}s bytes={len(pcm)}")
