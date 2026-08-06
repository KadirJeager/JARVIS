"""Anti-Spoofing / Deepfake Voice Detection (CM - Countermeasure) module for JARVIS.

Uses nii-yamagishilab/mms-300m-anti-deepfake model (fairseq -> HF Wav2Vec2Model key remapping).
Loads lazily via double-checked locking singleton on CPU.
Calculates cm_fake_prob (softmax fake class probability).
cm_fake_prob < config.CM_REJECT_THRESHOLD -> bonafide (True)
cm_fake_prob >= config.CM_REJECT_THRESHOLD -> spoof (False)

Torch and heavy ML libraries are imported lazily inside functions so this module
can be imported in lightweight / torch-less test environments.
"""
import json
import os
import threading
from typing import Callable

from . import config

_model = None
_model_lock = threading.Lock()
_score_fn: Callable[[bytes], float | tuple[bool, float]] | None = None


def _load_model():
    """Load model with key remapping fairseq -> HF Wav2Vec2Model.
    Evaluated and verified in brain/scripts/cm_benchmark.py.
    """
    import huggingface_hub
    import torch
    import torch.nn as nn
    from safetensors.torch import load_file
    from transformers import Wav2Vec2Config, Wav2Vec2Model

    model_id = "nii-yamagishilab/mms-300m-anti-deepfake"
    cm_dir = os.environ.get("CM_MODEL_DIR") or getattr(config, "CM_MODEL_DIR", "/opt/antispoof")

    if cm_dir and os.path.exists(cm_dir):
        path = cm_dir
    else:
        path = huggingface_hub.snapshot_download(model_id)

    weights_path = os.path.join(path, "model.safetensors")
    weights = load_file(weights_path)

    config_path = os.path.join(path, "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        cfg_dict = json.load(f)

    # Disable spec augment and time/feature masking for deterministic inference
    cfg_dict["mask_time_prob"] = 0.0
    cfg_dict["mask_feature_prob"] = 0.0
    cfg_dict["apply_spec_augment"] = False
    w2v_config = Wav2Vec2Config(**cfg_dict)

    w2v_model = Wav2Vec2Model(w2v_config)
    hf_state = w2v_model.state_dict()

    mapped_state = {}
    for src_k, tensor in weights.items():
        dst_k = None
        if src_k.startswith("m_ssl.model."):
            k = src_k[len("m_ssl.model.") :]
            if k.startswith("feature_extractor.conv_layers."):
                parts = k.split(".")
                layer_idx = parts[2]
                sub_idx = parts[3]
                if sub_idx == "0":
                    dst_k = f"feature_extractor.conv_layers.{layer_idx}.conv." + ".".join(parts[4:])
                elif sub_idx == "2" and parts[4] == "1":
                    dst_k = f"feature_extractor.conv_layers.{layer_idx}.layer_norm." + ".".join(parts[5:])
            elif k.startswith("layer_norm."):
                dst_k = "feature_projection." + k
            elif k.startswith("post_extract_proj."):
                dst_k = "feature_projection.projection." + k[len("post_extract_proj.") :]
            elif k.startswith("encoder.pos_conv.0."):
                param = k[len("encoder.pos_conv.0.") :]
                if param == "bias":
                    dst_k = "encoder.pos_conv_embed.conv.bias"
                elif param == "weight_g":
                    dst_k = "encoder.pos_conv_embed.conv.parametrizations.weight.original0"
                elif param == "weight_v":
                    dst_k = "encoder.pos_conv_embed.conv.parametrizations.weight.original1"
            elif k.startswith("encoder.layer_norm."):
                dst_k = k
            elif k.startswith("encoder.layers."):
                parts = k.split(".")
                layer_idx = parts[2]
                rest = ".".join(parts[3:])
                if rest.startswith("self_attn."):
                    attn_param = rest[len("self_attn.") :]
                    dst_k = f"encoder.layers.{layer_idx}.attention.{attn_param}"
                elif rest.startswith("self_attn_layer_norm."):
                    norm_param = rest[len("self_attn_layer_norm.") :]
                    dst_k = f"encoder.layers.{layer_idx}.layer_norm.{norm_param}"
                elif rest.startswith("fc1."):
                    fc1_param = rest[len("fc1.") :]
                    dst_k = f"encoder.layers.{layer_idx}.feed_forward.intermediate_dense.{fc1_param}"
                elif rest.startswith("fc2."):
                    fc2_param = rest[len("fc2.") :]
                    dst_k = f"encoder.layers.{layer_idx}.feed_forward.output_dense.{fc2_param}"
                elif rest.startswith("final_layer_norm."):
                    norm_param = rest[len("final_layer_norm.") :]
                    dst_k = f"encoder.layers.{layer_idx}.final_layer_norm.{norm_param}"

        if dst_k and dst_k in hf_state and hf_state[dst_k].shape == tensor.shape:
            mapped_state[dst_k] = tensor

    w2v_model.load_state_dict(mapped_state, strict=True)
    w2v_model.eval()

    proj_fc = nn.Linear(1024, 2)
    proj_fc.weight.data = weights["proj_fc.weight"]
    proj_fc.bias.data = weights["proj_fc.bias"]
    proj_fc.eval()

    return w2v_model, proj_fc


def _get_model():
    """Lazy singleton anti-spoof model tuple (w2v_model, proj_fc).
    Loaded once per process on CPU with double-checked locking.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            _model = _load_model()
    return _model


def pcm16_to_tensor(pcm: bytes):
    """Raw PCM16 mono 16kHz bytes -> float32 tensor [samples] in [-1, 1].
    Matches speaker.pcm16_to_tensor pattern.
    """
    import torch

    ints = torch.frombuffer(bytearray(pcm), dtype=torch.int16)
    return ints.float() / 32768.0


def is_bonafide(pcm: bytes) -> tuple[bool, float]:
    """Evaluates PCM16 16kHz mono audio bytes for anti-spoofing / deepfake detection.

    Returns:
        tuple[bool, float]: (is_bonafide, cm_fake_prob)
        - is_bonafide: True if cm_fake_prob < config.CM_REJECT_THRESHOLD, else False.
        - cm_fake_prob: float in [0.0, 1.0] representing fake class probability from softmax.

    Raises:
        Exception: If model loading or forward pass fails (caller handles fail-closed/fail-safe policy).
    """
    global _score_fn
    if _score_fn is not None:
        res = _score_fn(pcm)
        if isinstance(res, tuple):
            return res
        cm_fake_prob = float(res)
        threshold = getattr(config, "CM_REJECT_THRESHOLD", 0.85)
        return (cm_fake_prob < threshold, cm_fake_prob)

    import torch

    w2v_model, proj_fc = _get_model()

    wav_tensor = pcm16_to_tensor(pcm)
    wav = torch.nn.functional.layer_norm(wav_tensor, wav_tensor.shape)
    if wav.dim() == 1:
        wav = wav.unsqueeze(0)

    with torch.no_grad():
        out = w2v_model(wav)
        hidden = out.last_hidden_state  # (1, T_out, 1024)
        pooled = hidden.mean(dim=1)  # (1, 1024)
        logits = proj_fc(pooled)
        probs = torch.softmax(logits, dim=-1).squeeze(0)

    cm_fake_prob = float(probs[0].item())
    threshold = getattr(config, "CM_REJECT_THRESHOLD", 0.85)
    cm_is_bonafide = cm_fake_prob < threshold

    return cm_is_bonafide, cm_fake_prob
