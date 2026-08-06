"""Voice challenge-response mechanism for enrollment liveness verification (Phase C).

Firestore collection: voice_challenges/{user_id}
Document structure:
    {
        "code": "4831",
        "status": "pending" | "granted",
        "created_at": ISO string,
        "expires_at": ISO string,
        "granted_at": ISO string (optional)
    }

TTL Rules:
- Challenge TTL: 2 minutes from creation.
- Grant TTL: 5 minutes from verification.
- Single active challenge: create_challenge overwrites existing document.
"""
from datetime import datetime, timedelta, timezone
import logging
import random
from typing import Any, Callable

COLLECTION_NAME = "voice_challenges"
CHALLENGE_TTL_SECONDS = 120  # 2 minutes
GRANT_TTL_SECONDS = 300      # 5 minutes

DIGIT_WORDS = {
    "0": "sıfır",
    "1": "bir",
    "2": "iki",
    "3": "üç",
    "4": "dört",
    "5": "beş",
    "6": "altı",
    "7": "yedi",
    "8": "sekiz",
    "9": "dokuz",
}

WORD_DIGITS = {v: k for k, v in DIGIT_WORDS.items()}


def _now_dt(now_fn: Callable[[], Any] | None = None) -> datetime:
    """Resolve current time as timezone-aware UTC datetime."""
    if now_fn is None:
        return datetime.now(timezone.utc)
    val = now_fn()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, str):
        dt = datetime.fromisoformat(val)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val, tz=timezone.utc)
    raise TypeError(f"Unrecognized time format from now_fn: {type(val)}")


def digit_to_words(code: str) -> str:
    """Convert numeric string (e.g. '4831') to Turkish words ('dört sekiz üç bir')."""
    return " ".join(DIGIT_WORDS.get(ch, ch) for ch in code if ch in DIGIT_WORDS)


def extract_digits_from_text(text: str) -> str:
    """Convert Turkish digit words in text to numbers and extract all digits."""
    lower_text = text.lower()
    for word, digit in WORD_DIGITS.items():
        lower_text = lower_text.replace(word, digit)
    return "".join(ch for ch in lower_text if ch.isdigit())


def create_challenge(db: Any, user_id: str, now_fn: Callable[[], Any] | None = None) -> tuple[str, dict]:
    """Create a new 4-digit voice challenge for user_id in Firestore (overwrites existing)."""
    now = _now_dt(now_fn)
    code = f"{random.randint(0, 9999):04d}"
    expires_at = now + timedelta(seconds=CHALLENGE_TTL_SECONDS)

    doc_data = {
        "code": code,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    db.collection(COLLECTION_NAME).document(user_id).set(doc_data)
    logging.info("voice_challenge: created challenge for user=%s code=%s expires_at=%s", user_id, code, doc_data["expires_at"])
    return code, doc_data


def verify_and_grant(db: Any, user_id: str, code: str, now_fn: Callable[[], Any] | None = None) -> bool:
    """Verify challenge code for user_id. If valid and matching, grant enrollment access for 5 minutes."""
    now = _now_dt(now_fn)
    try:
        doc_ref = db.collection(COLLECTION_NAME).document(user_id)
        snap = doc_ref.get()
        if not snap.exists:
            logging.warning("voice_challenge: no challenge found for user=%s", user_id)
            return False

        data = snap.to_dict() or {}
        if data.get("status") != "pending":
            logging.warning("voice_challenge: challenge for user=%s not pending (status=%s)", user_id, data.get("status"))
            return False

        expires_at_dt = datetime.fromisoformat(data["expires_at"])
        if now > expires_at_dt:
            logging.warning("voice_challenge: challenge expired for user=%s (now=%s expires=%s)", user_id, now, expires_at_dt)
            return False

        input_code = code.strip()
        expected_code = str(data.get("code", "")).strip()
        if input_code != expected_code:
            logging.warning("voice_challenge: code mismatch for user=%s input=%s expected=%s", user_id, input_code, expected_code)
            return False

        grant_expires_at = now + timedelta(seconds=GRANT_TTL_SECONDS)
        grant_data = {
            "status": "granted",
            "granted_at": now.isoformat(),
            "expires_at": grant_expires_at.isoformat(),
        }
        doc_ref.set(grant_data, merge=True)
        logging.info("voice_challenge: granted enrollment for user=%s until %s", user_id, grant_data["expires_at"])
        return True
    except Exception:
        logging.exception("voice_challenge: verify_and_grant failed for user=%s", user_id)
        return False


def has_valid_grant(db: Any, user_id: str, now_fn: Callable[[], Any] | None = None) -> bool:
    """Check if user_id currently holds a valid (unexpired) enrollment grant (fail-closed)."""
    now = _now_dt(now_fn)
    try:
        snap = db.collection(COLLECTION_NAME).document(user_id).get()
        if not snap.exists:
            return False

        data = snap.to_dict() or {}
        if data.get("status") != "granted":
            return False

        expires_at_raw = data.get("expires_at")
        if not expires_at_raw:
            return False

        expires_at_dt = datetime.fromisoformat(expires_at_raw)
        if now > expires_at_dt:
            logging.info("voice_challenge: grant expired for user=%s", user_id)
            return False

        return True
    except Exception:
        logging.exception("voice_challenge: has_valid_grant exception for user=%s (fail-closed)", user_id)
        return False


def get_pending_challenge(db: Any, user_id: str, now_fn: Callable[[], Any] | None = None) -> dict | None:
    """Retrieve active pending challenge dict for user_id if valid and unexpired."""
    now = _now_dt(now_fn)
    try:
        snap = db.collection(COLLECTION_NAME).document(user_id).get()
        if not snap.exists:
            return None

        data = snap.to_dict() or {}
        if data.get("status") != "pending":
            return None

        expires_at_raw = data.get("expires_at")
        if not expires_at_raw:
            return None

        expires_at_dt = datetime.fromisoformat(expires_at_raw)
        if now > expires_at_dt:
            return None

        return data
    except Exception:
        logging.exception("voice_challenge: get_pending_challenge exception for user=%s", user_id)
        return None
