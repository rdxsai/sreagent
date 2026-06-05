# OpenTelemetry Demo Lab

This lab records curated incident fixtures from OpenTelemetry Demo.

## Pinned Source

The v1 lab is verified against:

```text
repository: https://github.com/open-telemetry/opentelemetry-demo
git_sha: b6f5c1ac7328fa57f2681e9c74574f1698847abb
deployment_mode: docker-compose
compose_files:
  - compose.yaml
  - compose.full.yaml
  - compose.observability.yaml
  - compose.extras.yaml
```

The source verification pass checked these files at the pinned SHA:

- `Makefile`
- `compose.yaml`
- `compose.full.yaml`
- `compose.observability.yaml`
- `compose.extras.yaml`
- `.env`
- `src/flagd/demo.flagd.json`
- `src/flagd-ui/lib/flagd_ui_web/router.ex`
- `src/flagd-ui/lib/flagd_ui_web/controllers/feature_controller.ex`
- `src/frontend-proxy/envoy.tmpl.yaml`
- `src/otel-collector/otelcol-config-observability.yml`

## Verified Control Surface

At the pinned SHA:

- `make start` starts full demo plus observability using the compose files above.
- `make stop` tears the stack down with volumes.
- frontend-proxy routes `/feature` to flagd-ui.
- flagd-ui exposes `GET /api/read` and `POST /api/write`.
- through frontend-proxy, the lab control base URL is `http://localhost:8080/feature`.

The lab must only use raw flag keys inside controller and eval-only files.
Public fixtures must never expose raw flag keys or `feature_flag.*` attributes.

## Verified Telemetry Export Surface

At the pinned SHA:

- the collector exports metrics to Prometheus through `otlp_http/prometheus`
- the collector exports traces to Jaeger through `otlp_grpc/jaeger`
- the collector exports logs to OpenSearch through the `opensearch` exporter
- Prometheus listens on `localhost:9090`
- the Jaeger UI is available through `http://localhost:8080/jaeger/ui`

Still pending live smoke tests before recorder wiring:

- exact Jaeger query API path exposed through the proxy
- stable host access path for OpenSearch log queries
- final metric names selected for normalized fixture rows

Do not wire these pending surfaces from guesses.
