"""Task-1 gate: confirm speechbrain ECAPA loads, embeds, and report dims.
Run with the torch-capable interpreter (.venv-speaker)."""
import struct, math, sys

def _sine_pcm16(seconds=2.0, freq=220.0, rate=16000):
    n = int(seconds * rate)
    return b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
        for i in range(n)
    )

def main():
    import torch
    from speechbrain.inference.speaker import EncoderClassifier
    model = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="/tmp/spkrec-ecapa",
        run_opts={"device": "cpu"},
    )
    pcm = _sine_pcm16()
    wav = (torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0).unsqueeze(0)
    emb = model.encode_batch(wav)
    print("EMBED_SHAPE", tuple(emb.shape))
    print("EMBED_DIM", emb.squeeze().numel())

if __name__ == "__main__":
    sys.exit(main())
