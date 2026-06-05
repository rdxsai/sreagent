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
- through frontend-proxy, the default lab control base URL is `http://localhost:8080/feature`.
- if another local service owns `8080`, start the demo with an alternate
  `ENVOY_PORT` and set `SENTINEL_OTEL_FRONTEND_PROXY_BASE_URL`.

The lab must only use raw flag keys inside controller and eval-only files.
Public fixtures must never expose raw flag keys or `feature_flag.*` attributes.

## Verified Telemetry Export Surface

At the pinned SHA:

- the collector exports metrics to Prometheus through `otlp_http/prometheus`
- the collector exports traces to Jaeger through `otlp_grpc/jaeger`
- the collector exports logs to OpenSearch through the `opensearch` exporter
- Prometheus listens on `localhost:9090`
- the Jaeger UI is available through `<frontend-proxy>/jaeger/ui`
- the Jaeger JSON API is available through `<frontend-proxy>/jaeger/ui/api`
- OpenSearch publishes a dynamic host port, discover it with `docker port opensearch 9200`

Live smoke notes from the first local run:

- `8080` was already owned by another local service
- `ENVOY_PORT=18080` started the demo without collision
- `http://localhost:18080/feature/api/read` returned the flag document
- `flag-set paymentUnreachable on` succeeded through flagd-ui
- `flag-reset` succeeded and restored flags to `off`
- `http://localhost:18080/jaeger/ui/api/services` returned Jaeger service JSON
- `http://localhost:9090/-/ready` returned 200
- OpenSearch was reachable through its published Docker port

Still pending before normalized recorder wiring:

- final metric names selected for normalized fixture rows
- log index query shape for extracting normalized OpenSearch logs

Do not wire these pending surfaces from guesses.
