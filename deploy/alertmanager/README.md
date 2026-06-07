# Live Alertmanager Demo Overlay

This path is demo-only and is never used by the eval harness. Alertmanager grouping and `for:` timing are non-deterministic; eval always uses the frozen `manifest.alerts` from the recorded fixture.

## Regenerating the rules

Rebuild `prometheus_rules.yml` from the alerting module whenever the rule definitions change:

```
python -m labs.otel.alerting.prometheus_rules > deploy/alertmanager/prometheus_rules.yml
```

## Wiring the pinned demo Prometheus

The demo Prometheus config is external to this repo. Two edits are needed there, not here.

First, mount `deploy/alertmanager/prometheus_rules.yml` into the prometheus container (e.g. as `/etc/prometheus/sentinel_rules.yml`) and add it to the `rule_files:` list in the demo's `prometheus.yml`:

```yaml
rule_files:
  - /etc/prometheus/sentinel_rules.yml
```

Second, add an `alerting.alertmanagers` stanza in `prometheus.yml` pointing at the Alertmanager container:

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - sentinel-alertmanager:9093
```

These are config edits to the demo's `prometheus.yml` and compose file, not changes in this repo.

## Running the Sentinel receiver on the host

Start the FastAPI app so Alertmanager (running in Docker) can reach it via `host.docker.internal`:

```
uvicorn sentinel.api.app:app --port 8000
```

Alertmanager routes webhook POSTs to `http://host.docker.internal:8000/alert`, which resolves to port 8000 on the host.

## Running Alertmanager

```
docker compose -f deploy/alertmanager/compose.alertmanager.yml up -d
```

## Demo flow

1. Inject a fault, for example:

   ```
   python -m labs.otel.controller flag-set paymentFailure 100% --base-url <flagd-base-url>
   ```

2. Watch a symptom alert (e.g. `CheckoutFailureRate`) fire in the Prometheus UI.

3. Prometheus routes the alert to `sentinel-alertmanager:9093`, which groups it and POSTs to `/alert` on the host.

4. The `/alert` receiver maps the Alertmanager payload to `DerivedAlert` objects and logs them. Agent kickoff is a seam to be wired later.

## Webhook allow-list enforcement

The webhook mapping enforces the same allow-list as the offline path, so a misconfigured live rule that named a culprit would be rejected at `/alert`.
