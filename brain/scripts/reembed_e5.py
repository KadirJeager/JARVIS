"""ONE-OFF MIGRATION: re-embed every `facts` and `lessons` document with the
local intfloat/multilingual-e5-base model, replacing the gemini-embedding-001
vectors written before the e5 switch.

WARNING: the old vector space DIES here. gemini-embedding-001 and e5 vectors
are not comparable -- a mixed collection silently corrupts every COSINE
nearest-neighbour query until ALL docs carry e5 vectors. Run this once,
right after deploying the e5-based brain, and never again. The re-embedded
text must mirror the write-time logic in app/memory.py EXACTLY (facts ->
record["text"]; lessons -> the four joined fields), or stored vectors stop
matching what the write path would produce.

Dry-run first (counts and embeds, writes nothing):
    .venv-speaker/bin/python scripts/reembed_e5.py --dry-run
Real run (Kadir-approved, post-deploy):
    .venv-speaker/bin/python scripts/reembed_e5.py

Needs the torch-capable interpreter (.venv-speaker) and Firestore
credentials (gcloud ADC or GOOGLE_APPLICATION_CREDENTIALS)."""
import argparse
import logging
import math
import sys

from google.cloud import firestore
from google.cloud.firestore_v1.vector import Vector

from app.memory import make_e5_embedders

COLLECTIONS = ("facts", "lessons")


def _source_text(collection_name: str, data: dict) -> str:
    """Rebuild the exact string the write path embedded (app/memory.py):
    remember_fact() embeds the fact text itself; add_lesson() embeds the
    four lesson fields space-joined, in field order."""
    if collection_name == "facts":
        return data["text"]
    return " ".join(
        data[k] for k in ("context", "tried", "went_wrong", "correct")
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="embed and log everything but write nothing to Firestore",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("reembed_e5")

    embedders = make_e5_embedders()
    db = firestore.Client()

    total = written = failed = 0
    for name in COLLECTIONS:
        for snap in db.collection(name).stream():
            total += 1
            doc_id = f"{name}/{snap.id}"
            try:
                vec = embedders.embed_passage(_source_text(name, snap.to_dict()))
                # DATA-level: the norm must be ~1.0 per doc -- it is the
                # per-item proof that normalize_embeddings=True held, so one
                # bad run localizes here instead of corrupting recall silently.
                norm = math.sqrt(sum(x * x for x in vec))
                if args.dry_run:
                    log.info("%s dim=%d norm=%.4f dry-run (write skipped)", doc_id, len(vec), norm)
                    written += 1
                    continue
                snap.reference.update({"embedding": Vector(vec)})
                written += 1
                log.info("%s dim=%d norm=%.4f written", doc_id, len(vec), norm)
            except Exception:
                failed += 1
                log.exception("%s FAILED", doc_id)
    log.info(
        "summary: total=%d %s=%d failed=%d",
        total, "would-write" if args.dry_run else "written", written, failed,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
