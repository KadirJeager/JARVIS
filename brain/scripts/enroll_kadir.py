"""Enroll Kadir's voiceprint anchors from local WAV/PCM clips.
Usage: python scripts/enroll_kadir.py <id-token> <base-url> clip1.pcm clip2.pcm ...
Clips must be raw PCM16 mono 16kHz (ffmpeg -ac 1 -ar 16000 -f s16le)."""
import base64, sys, urllib.request, json

def main(argv):
    token, base_url, *paths = argv
    clips = [base64.b64encode(open(p, "rb").read()).decode() for p in paths]
    req = urllib.request.Request(
        f"{base_url}/api/voice/enroll",
        data=json.dumps({"clips": clips}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        print(r.read().decode())

if __name__ == "__main__":
    main(sys.argv[1:])
