#!/usr/bin/env bash
# Deploy or update a JARVIS core installation on Google Cloud.
#
# Every step is idempotent: existing resources are reused and updated, never
# recreated. Installation-specific values come from an env file (default
# deploy/installation.env, not committed); see installation.env.example.
#
# Usage: deploy/deploy.sh [path/to/installation.env]
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CORE="$(dirname "$HERE")"
ENV_FILE="${1:-$HERE/installation.env}"
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE:?}" "${ALLOWED_EMAILS:?}" "${FIREBASE_WEB_APP_ID:?}"
CLIPROXY="${CLIPROXY:-0}"
if [[ "$CLIPROXY" == 1 ]]; then
  : "${CLIPROXY_KEY_SECRET:?}" "${CLIPROXY_OAUTH_SECRET:?}"
fi

step() { printf '\n== %s\n' "$*"; }
api() {  # api METHOD URL [JSON_BODY] -- Google REST call as the deploying user
  local method=$1 url=$2
  local args=(-fsS -X "$method" -H "Authorization: Bearer $(gcloud auth print-access-token)"
    -H "x-goog-user-project: $PROJECT_ID")
  if [[ $# -ge 3 ]]; then args+=(-H 'Content-Type: application/json' --data "$3"); fi
  curl "${args[@]}" "$url"
}

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SERVICE_URL="https://${SERVICE}-${PROJECT_NUMBER}.${REGION}.run.app"
RUNTIME_SA="${SERVICE}-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
INVOKER_SA="${SERVICE}-invoker@${PROJECT_ID}.iam.gserviceaccount.com"
QUEUE="${SERVICE}-turns"
TASKS_QUEUE="projects/${PROJECT_ID}/locations/${REGION}/queues/${QUEUE}"
REPOSITORY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${SERVICE}"
CORE_IMAGE="${REPOSITORY}/core:$(date -u +%Y%m%d%H%M%S)"
CLIPROXY_VERSION="$(sed -n 's/^ARG CLIPROXY_VERSION=//p' "$HERE/cliproxy/Dockerfile")"
CLIPROXY_IMAGE="${REPOSITORY}/cliproxy:${CLIPROXY_VERSION}"
gc() { gcloud --project "$PROJECT_ID" --quiet "$@"; }

step "APIs"
gc services enable run.googleapis.com cloudtasks.googleapis.com cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com firestore.googleapis.com \
  secretmanager.googleapis.com identitytoolkit.googleapis.com firebaserules.googleapis.com firebase.googleapis.com

step "Artifact Registry repository"
gc artifacts repositories describe "$SERVICE" --location "$REGION" >/dev/null 2>&1 \
  || gc artifacts repositories create "$SERVICE" --location "$REGION" --repository-format docker

step "Service accounts and roles"
for sa in runtime invoker; do
  gc iam service-accounts describe "${SERVICE}-${sa}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 \
    || gc iam service-accounts create "${SERVICE}-${sa}" --display-name "JARVIS ${sa}"
done
for role in roles/datastore.user roles/cloudtasks.enqueuer; do
  gc projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$RUNTIME_SA" --role "$role" \
    --condition None >/dev/null
done
# Creating tasks with OIDC tokens requires acting as the invoker account.
gc iam service-accounts add-iam-policy-binding "$INVOKER_SA" --member "serviceAccount:$RUNTIME_SA" \
  --role roles/iam.serviceAccountUser >/dev/null
for secret in ${CLIPROXY_KEY_SECRET:-} ${CLIPROXY_OAUTH_SECRET:-} ${EXTRA_SECRETS:-}; do
  gc secrets add-iam-policy-binding "$secret" --member "serviceAccount:$RUNTIME_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null
done

step "Images"
gc builds submit "$CORE" --tag "$CORE_IMAGE"
if [[ "$CLIPROXY" == 1 ]]; then
  gc artifacts docker images describe "$CLIPROXY_IMAGE" >/dev/null 2>&1 \
    || gc builds submit "$HERE/cliproxy" --tag "$CLIPROXY_IMAGE"
fi

step "Firestore indexes"
python3 - "$HERE/firestore.indexes.json" <<'PY' | while read -r args; do
import json, sys
for index in json.load(open(sys.argv[1]))['indexes']:
    fields = ' '.join(f"--field-config=field-path={f['fieldPath']},order={f['order'].lower()}" for f in index['fields'])
    print(f"--collection-group={index['collectionGroup']} {fields}")
PY
  # shellcheck disable=SC2086
  if ! out="$(gc firestore indexes composite create --async $args 2>&1)"; then
    grep -q -i 'already exists' <<<"$out" || { echo "$out" >&2; exit 1; }
  fi
done

step "Firestore security rules"
rules_json="$(python3 -c 'import json,sys; print(json.dumps({"source": {"files": [{"name": "firestore.rules", "content": open(sys.argv[1]).read()}]}}))' "$HERE/firestore.rules")"
ruleset="$(api POST "https://firebaserules.googleapis.com/v1/projects/$PROJECT_ID/rulesets" "$rules_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
release="projects/$PROJECT_ID/releases/cloud.firestore"
if api GET "https://firebaserules.googleapis.com/v1/$release" >/dev/null 2>&1; then
  api PATCH "https://firebaserules.googleapis.com/v1/$release" "{\"release\": {\"name\": \"$release\", \"rulesetName\": \"$ruleset\"}}" >/dev/null
else
  api POST "https://firebaserules.googleapis.com/v1/projects/$PROJECT_ID/releases" "{\"name\": \"$release\", \"rulesetName\": \"$ruleset\"}" >/dev/null
fi

step "Cloud Tasks queue"
gc tasks queues describe "$QUEUE" --location "$REGION" >/dev/null 2>&1 \
  || gc tasks queues create "$QUEUE" --location "$REGION"
gc tasks queues update "$QUEUE" --location "$REGION" --max-attempts 100 --min-backoff 10s \
  --max-backoff 300s --max-doublings 5 --max-retry-duration 86400s >/dev/null

step "Cloud Run service"
FIREBASE_WEB_CONFIG="$(api GET "https://firebase.googleapis.com/v1beta1/projects/$PROJECT_ID/webApps/$FIREBASE_WEB_APP_ID/config")"
export PROJECT_ID ALLOWED_EMAILS SERVICE SERVICE_URL TASKS_QUEUE INVOKER_SA RUNTIME_SA CORE_IMAGE \
  FIREBASE_WEB_CONFIG CLIPROXY CLIPROXY_IMAGE CLIPROXY_KEY_SECRET CLIPROXY_OAUTH_SECRET
spec="$(mktemp --suffix .yaml)"
trap 'rm -f "$spec"' EXIT
python3 "$HERE/render_service.py" >"$spec"
gc run services replace "$spec" --region "$REGION"
# People reach the PWA and API directly; the service authenticates every
# request itself (Firebase ID tokens, Google OIDC tokens).
gc run services add-iam-policy-binding "$SERVICE" --region "$REGION" --member allUsers \
  --role roles/run.invoker >/dev/null
actual_url="$(gc run services describe "$SERVICE" --region "$REGION" --format 'value(metadata.annotations."run.googleapis.com/urls")')"
grep -q "$SERVICE_URL" <<<"$actual_url" || { echo "service URL $SERVICE_URL is not served: $actual_url" >&2; exit 1; }

step "Repair schedule"
job="${SERVICE}-repair"
scheduler_args=(--location "$REGION" --schedule '*/5 * * * *' --uri "$SERVICE_URL/internal/repair"
  --http-method POST --oidc-service-account-email "$INVOKER_SA" --oidc-token-audience "$SERVICE_URL")
if gc scheduler jobs describe "$job" --location "$REGION" >/dev/null 2>&1; then
  gc scheduler jobs update http "$job" "${scheduler_args[@]}" >/dev/null
else
  gc scheduler jobs create http "$job" "${scheduler_args[@]}" >/dev/null
fi

step "Sign-in domain"
host="${SERVICE_URL#https://}"
if auth_config="$(api GET "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT_ID/config" 2>/dev/null)"; then
  domains="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); h=sys.argv[2]; ds=d.get("authorizedDomains",[]); print(json.dumps({"authorizedDomains": ds + ([h] if h not in ds else [])}))' "$auth_config" "$host")"
  api PATCH "https://identitytoolkit.googleapis.com/admin/v2/projects/$PROJECT_ID/config?updateMask=authorizedDomains" "$domains" >/dev/null
else
  echo "Firebase Authentication is not initialized: enable the Google provider in the Firebase console, then re-run." >&2
  exit 1
fi

step "Done"
echo "PWA and API: $SERVICE_URL"
