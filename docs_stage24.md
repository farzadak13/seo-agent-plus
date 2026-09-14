# Stage 24 — Real GSC Connector

Adds the external Search Console acquisition boundary while leaving existing
ingestion and decision logic unchanged.

Unit tests inject the HTTP transport, so no network is required for the test
suite. Production wiring will supply a real HTTP function and credential
lifecycle.

Install the production HTTP/auth dependencies when wiring runtime:
    pip install "requests>=2.32,<3" "google-auth>=2.38,<3"

Never commit access tokens or service-account credentials.
