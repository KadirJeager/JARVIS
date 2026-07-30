# CLIProxyAPI sidecar for jarvis-brain / jarvis-voice (no-GOOGLE_API_KEY migration).
#
# Serves the Google AI Ultra SUBSCRIPTION quota (OAuth, via the antigravity
# token) as a Gemini-compatible /v1beta endpoint on 127.0.0.1:8317, so the
# brain container reaches it over localhost -- no ingress, no IAM, no public
# surface. Auth between brain and proxy is the proxy's own api-key, handed to
# the brain as GOOGLE_API_KEY (the genai SDK sends it as x-goog-api-key).
#
# The OAuth token arrives from Secret Manager as a READ-ONLY mount at
# /secrets/auth/; the entrypoint copies it into a WRITABLE auth-dir because
# the proxy rewrites the token file on every access-token refresh. A Cloud
# Run instance restart re-seeds from the secret -- if Google ever rotates the
# REFRESH token, the copy in the instance dies with it and the secret must be
# re-seeded from the local ~/.cli-proxy-api (see brain/README.md).
FROM debian:12-slim AS download
# Pinned to the exact release verified locally (7.2.109 running, /v1beta
# verified against 7.2.111 release notes). Bump deliberately, never "latest":
# the proxy IS the text path's only upstream, so an unreviewed upgrade is a
# prod outage vector.
ARG CLIPROXY_VERSION=7.2.111
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL "https://github.com/router-for-me/CLIProxyAPI/releases/download/v${CLIPROXY_VERSION}/CLIProxyAPI_${CLIPROXY_VERSION}_linux_amd64_no-plugin.tar.gz" \
       | tar -xz -C /usr/local/bin cli-proxy-api \
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
