# JARVIS Brain

FastAPI backend for JARVIS: `/api/chat`, `/api/history`, and the voice
gateway, backed by Firestore for persistent memory and chat transcripts.

## Deploy

The `messages` collection query in `app/messages.py` (`user_id` ==, `session_id`
==, `ts` order_by) requires a Firestore composite index. The index definition
is checked in at `firestore.indexes.json`, but **that file is not auto-applied
by anything** — there is no `firebase.json` and no CI step wired to deploy it.
The index must be created manually, once per Firestore database, before the
query works:

```bash
gcloud firestore indexes composite create --project your-gcp-project --collection-group messages --query-scope COLLECTION --field-config field-path=user_id,order=ascending --field-config field-path=session_id,order=ascending --field-config field-path=ts,order=descending
gcloud firestore indexes composite list --project your-gcp-project   # verify READY before deploy
```

If the index already exists, the create command returns `ALREADY_EXISTS`,
which is not an error. Confirm the index state is `READY` (not `CREATING`)
before deploying a backend revision that depends on it — a missing or
still-building index makes `/api/history` fail with a Firestore
`FAILED_PRECONDITION` (surfaced to clients as the generic 502 infra error).
