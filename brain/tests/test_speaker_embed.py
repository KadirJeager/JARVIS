import pathlib
import pytest

pytest.importorskip("torch")            # torch yoksa (3.14 venv) atla
pytest.importorskip("speechbrain")

from app.speaker import embed
from app.memory import _cosine_similarity

FIX = pathlib.Path(__file__).parent / "fixtures"

def _pcm(name):
    return (FIX / name).read_bytes()

def test_embed_dim_is_192():
    assert len(embed(_pcm("spk_a_1.pcm"))) == 192

def test_embed_is_deterministic():
    assert embed(_pcm("spk_a_1.pcm")) == embed(_pcm("spk_a_1.pcm"))

def test_same_speaker_scores_higher_than_different():
    a1 = embed(_pcm("spk_a_1.pcm"))
    a2 = embed(_pcm("spk_a_2.pcm"))
    b1 = embed(_pcm("spk_b_1.pcm"))
    assert _cosine_similarity(a1, a2) > _cosine_similarity(a1, b1)
