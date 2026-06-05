# Sentinel

Sentinel is a production-shaped, autonomous SRE incident-response agent.
Given an incident symptom, the agent will inspect telemetry, form and test
hypotheses, trace root cause, and emit a structured incident report.

v1 uses OpenTelemetry Demo as a recorded incident lab. A scenario controller
enables one known built-in failure flag under steady workload, records metrics,
logs, traces, topology, and change events, then writes replayable public
fixtures plus private eval truth. The agent and tools receive only public
fixtures. The eval harness is the only layer that reads private truth.

The three-layer seal is the core benchmark rule:

- scenario control records private injection metadata
- public fixtures contain only observable, redacted telemetry
- eval truth grades root-cause accuracy and is never passed to tools

See `docs/open-telemetryspec.md` for the v1 fixture-lab contract.
