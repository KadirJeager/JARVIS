"""Speaker identity management API (Katman 2b Dilim 3d, spec §6): visibility,
correction, deletion. Every endpoint is require_google_user-gated and keyed by
the authenticated user; every gallery/history touch goes through SpeakerService
(the ONE gallery lock) and runs OFF the event loop via asyncio.to_thread --
this process serves /api/chat and /ws/voice from that same loop (spec §10).

Embedding vectors NEVER leave the server (spec §6): responses are built by
explicit allowlist projection, and tests pin the absence of "vec" anywhere in
any response body. There is deliberately NO "biometric gate passed" header
anywhere in this API -- an unverifiable client assertion is not a signal
(spec §7, same class as `presence`)."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import speaker
# require_google_user, require_user DEĞİL (Wear W0 dal-review kuyruğu): cihaz
# token'ı (`jdt_`) saatte 30 gün kayar süreyle yaşar ve saatte ses kimliği
# yönetimi ekranı YOKTUR; bu uçlar ise galeriyi siler/zehirler. Çalınmış bir
# saat Kadir'in ses kimliğini yok edememeli. Kural yüzeyin TAMAMINA uygulanır --
# iki ucu seçip dördünü bırakmak ezberlenecek bir istisna listesi üretirdi.
# Telefon etkilenmez: her isteği zaten taze Google ID token'ı taşır.
# Pin: tests/test_voice_identity_scope.py
from .auth import require_google_user

router = APIRouter()

_SAMPLE_FIELDS = ("id", "source", "ts", "device_hint", "label", "note")
_HISTORY_FIELDS = ("id", "ts", "score", "verified", "device_hint", "presence",
                   "trust_level", "adapted_sample_id", "correction")

_INFRA_502 = "İşlem şu anda yapılamıyor (altyapı hatası). Az sonra tekrar dene."


def _project(row: dict, fields: tuple) -> dict:
    """Allowlist projection: `vec` (or any future private field) cannot leak
    by being FORGOTTEN -- only named fields ever cross into a response."""
    return {f: row.get(f) for f in fields}


def _service():
    from . import main   # runtime import: main mounts this module's router
    return main.get_speaker_service()


def _mean(xs: list[float]):
    return sum(xs) / len(xs) if xs else None


def quality_indicators(entries: list[dict], samples_by_id: dict) -> dict:
    """Interpreted read of the raw history (spec §6.1): rolling mean of
    verified scores, failure rate, per-device means, label-linked means (a row
    inherits the label of the gallery sample it adapted into -- history rows
    carry no label of their own, spec §4.2), and last-10 vs previous-10."""
    scores = [e["score"] for e in entries]
    verified_scores = [e["score"] for e in entries if e["verified"]]
    by_device: dict[str, list[float]] = {}
    by_label: dict[str, list[float]] = {}
    for e in entries:
        by_device.setdefault(e.get("device_hint", "unknown"), []).append(e["score"])
        label = (samples_by_id.get(e.get("adapted_sample_id") or "") or {}).get("label")
        if label:
            by_label.setdefault(label, []).append(e["score"])
    return {
        "mean_verified_score": _mean(verified_scores),
        "fail_rate": (len(entries) - len(verified_scores)) / len(entries) if entries else None,
        "by_device": {k: _mean(v) for k, v in by_device.items()},
        "by_label": {k: _mean(v) for k, v in by_label.items()},
        "trend": {"last10": _mean(scores[-10:]),
                  "previous10": _mean(scores[-20:-10])},
    }


@router.get("/api/voice/profile")
async def get_profile(email: str = Depends(require_google_user)):
    try:
        profile, history = await asyncio.to_thread(lambda: _service().overview(email))
    except Exception:
        logging.exception("voice_manage: profile read failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    samples = profile.anchors + profile.adaptive
    samples_by_id = {s["id"]: s for s in samples}
    return {
        "counts": {
            "anchors": len(profile.anchors),
            "auto": sum(1 for s in profile.adaptive if s["source"] == "auto"),
            "manual": sum(1 for s in profile.adaptive if s["source"] == "manual"),
        },
        "samples": [_project(s, _SAMPLE_FIELDS) for s in samples],
        "history": [_project(e, _HISTORY_FIELDS) for e in history],
        "quality": quality_indicators(history, samples_by_id),
    }


class SamplePatch(BaseModel):
    label: str | None = None
    note: str | None = None


@router.patch("/api/voice/sample/{sample_id}")
async def patch_sample(sample_id: str, req: SamplePatch,
                       email: str = Depends(require_google_user)):
    # model_fields_set distinguishes "absent" from an explicit null: label=None
    # must CLEAR the label, an omitted label must not touch it.
    kwargs = {}
    if "label" in req.model_fields_set:
        kwargs["label"] = req.label
    if "note" in req.model_fields_set:
        kwargs["note"] = req.note
    try:
        sample = await asyncio.to_thread(
            lambda: _service().update_sample(email, sample_id, **kwargs))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404, detail="Örnek bulunamadı")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: patch failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    return _project(sample, _SAMPLE_FIELDS)


@router.delete("/api/voice/sample/{sample_id}")
async def delete_sample(sample_id: str, email: str = Depends(require_google_user)):
    try:
        await asyncio.to_thread(lambda: _service().delete_sample(email, sample_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404, detail="Örnek bulunamadı")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: sample delete failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    return {"deleted": sample_id}


@router.post("/api/voice/history/{entry_id}/confirm")
async def confirm_history(entry_id: str, email: str = Depends(require_google_user)):
    try:
        return await asyncio.to_thread(
            lambda: _service().confirm_history(email, entry_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404,
                            detail="Geçmiş kaydı artık yok (silinmiş ya da tampondan düşmüş olabilir)")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: confirm failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)


@router.post("/api/voice/history/{entry_id}/reject")
async def reject_history(entry_id: str, email: str = Depends(require_google_user)):
    try:
        return await asyncio.to_thread(
            lambda: _service().reject_history(email, entry_id))
    except speaker.SampleNotFound:
        raise HTTPException(status_code=404,
                            detail="Geçmiş kaydı artık yok (silinmiş ya da tampondan düşmüş olabilir)")
    except speaker.RuleViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logging.exception("voice_manage: reject failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)


@router.delete("/api/voice/profile")
async def delete_profile(email: str = Depends(require_google_user)):
    try:
        await asyncio.to_thread(lambda: _service().delete_profile_and_history(email))
    except Exception:
        logging.exception("voice_manage: profile delete failed for user_id=%s", email)
        raise HTTPException(status_code=502, detail=_INFRA_502)
    logging.info("voice_manage: profile+history deleted for user_id=%s", email)
    return {"deleted": True}
