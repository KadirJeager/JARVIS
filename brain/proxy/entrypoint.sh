#!/bin/sh
# CLIProxyAPI sidecar entrypoint. Builds a minimal config from env and
# re-seeds the writable auth-dir from the read-only secret mount on every
# start (Cloud Run instances are ephemeral; the secret is the source of
# truth, the instance's refreshed copy is disposable).
set -eu

AUTH_SRC=/secrets/auth
AUTH_DIR=/tmp/cli-proxy-auth
mkdir -p "$AUTH_DIR"
# cat+redirect, not cp: the first two deploys showed cp from the secret
# volume failing SILENTLY (its stderr was swallowed by `|| true`) while ls
# could read the same file. Never swallow this error again -- a copy
# failure means "0 clients" and a brain that 502s every chat turn.
for src in "$AUTH_SRC"/*.json; do
    [ -e "$src" ] || continue
    base=$(basename "$src")
    if cat "$src" > "$AUTH_DIR/$base"; then
        echo "entrypoint: copied $base ($(wc -c < "$AUTH_DIR/$base") bytes)"
    else
        echo "entrypoint: COPY FAILED for $src" >&2
    fi
done
# Secret mounts are 0444; plain cp preserves that, and the proxy SKIPS auth
# files it cannot rewrite (it persists refreshed tokens back into the file).
# Observed live: "full client load complete - 0 clients" with the file
# present but read-only. Make the instance copy writable.
chmod u+w "$AUTH_DIR"/*.json 2>/dev/null || true
# The proxy's loader derives provider/account from the FILENAME pattern
# "<type>-<email>.json" (observed live: a bare "antigravity.json" mount was
# counted as "0 auth files"; the same content named
# "antigravity-<email>.json" loads fine locally). Cloud Run rejects '@' in
# secret item paths, so the mount arrives as "<type>.json" and is renamed
# here from the file's own type/email fields.
for f in "$AUTH_DIR"/*.json; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in
        *-*@*.json) continue ;;   # already in <type>-<email>.json form
    esac
    ftype=$(sed -n 's/.*"type": *"\([^"]*\)".*/\1/p' "$f" | head -1)
    femail=$(sed -n 's/.*"email": *"\([^"]*\)".*/\1/p' "$f" | head -1)
    if [ -n "$ftype" ] && [ -n "$femail" ]; then
        mv "$f" "$AUTH_DIR/${ftype}-${femail}.json"
        echo "entrypoint: renamed $(basename "$f") -> ${ftype}-${femail}.json"
    fi
done
# DATA-level diagnostics for the "0 auth files" failure mode: one failing
# run must localize the fault without a rerun.
echo "entrypoint: secret mount contents:"; ls -la "$AUTH_SRC" 2>&1 || true
echo "entrypoint: auth dir after copy:"; ls -la "$AUTH_DIR" 2>&1 || true

cat > /tmp/cliproxy.yaml <<EOF
port: 8317
# 0.0.0.0, not 127.0.0.1: Cloud Run's TCP startup probe does not reach a
# loopback-only listener in a sidecar (seen live: "API server started
# successfully on 127.0.0.1:8317" + probe DEADLINE_EXCEEDED x12). The
# network namespace is shared with the brain container but NOTHING outside
# the instance can reach this -- only the ingress container's port is
# published, so binding all interfaces exposes nothing.
host: 0.0.0.0
auth-dir: $AUTH_DIR
api-keys:
  - ${CLIPROXY_API_KEY}
debug: false
logging-to-file: false
EOF

exec /usr/local/bin/cli-proxy-api -config /tmp/cliproxy.yaml
