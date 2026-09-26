# Optional CLIProxyAPI sidecar for installations that reach their model
# through a subscription OAuth session instead of an API key.
#
# Serves the account's quota (OAuth, via the antigravity token) as a
# Gemini-compatible /v1beta endpoint on localhost:8317, so the core container
# reaches it over localhost -- no ingress, no IAM, no public surface. The core
# authenticates with the proxy's own api-key, referenced from the model
# settings as a Secret Manager version.
#
# The OAuth token arrives from Secret Manager as a READ-ONLY mount at
# /secrets/auth/; the entrypoint copies it into a WRITABLE auth-dir because
# the proxy rewrites the token file on every access-token refresh. A Cloud
# Run instance restart re-seeds from the secret -- if Google ever rotates the
# REFRESH token, the secret must be re-seeded from a fresh local login.
FROM debian:12-slim AS download
# Pinned release and its SHA256 from the GitHub release metadata (verified
# 2026-09-26). Bump both deliberately, never "latest": the proxy is this
# installation's model path, so an unreviewed upgrade is an outage vector.
ARG CLIPROXY_VERSION=7.2.111
ARG CLIPROXY_SHA256=d6d8c35c10067fcd700e43fdd8dea7bb6152b3a8cc02806b812764ef63009980
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /tmp/cliproxy.tar.gz "https://github.com/router-for-me/CLIProxyAPI/releases/download/v${CLIPROXY_VERSION}/CLIProxyAPI_${CLIPROXY_VERSION}_linux_amd64_no-plugin.tar.gz" \
    && echo "${CLIPROXY_SHA256}  /tmp/cliproxy.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/cliproxy.tar.gz -C /usr/local/bin cli-proxy-api \
    && chmod +x /usr/local/bin/cli-proxy-api

FROM debian:12-slim
# ca-certificates in the FINAL image, not just the download stage: without
# them every upstream TLS call fails x509 (seen live in Cloud Run logs:
# "certificate signed by unknown authority" on token refresh/model refresh).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home appuser
COPY --from=download /usr/local/bin/cli-proxy-api /usr/local/bin/cli-proxy-api
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER appuser
ENTRYPOINT ["/entrypoint.sh"]
