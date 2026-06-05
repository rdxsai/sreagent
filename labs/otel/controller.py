"""Scenario control entrypoints for the OpenTelemetry Demo lab."""

from __future__ import annotations

import argparse

from pathlib import Path

from labs.otel.flagd import FlagdClient
from labs.otel.smoke import default_targets, smoke_check
from labs.otel.source import start_command, stop_command, verify_source_checkout


def record_scenario(config_path: Path, output_dir: Path) -> None:
    """Record one scenario from a scenario config file.

    Live control is intentionally not wired until the OpenTelemetry Demo SHA,
    flagd API, workload command, and telemetry backends are verified.
    """

    raise NotImplementedError("live OpenTelemetry Demo control is not wired yet")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenTelemetry Demo lab controller")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="verify a pinned demo checkout")
    doctor.add_argument("demo_dir", type=Path)

    subparsers.add_parser("start-command", help="print the verified start command")
    subparsers.add_parser("stop-command", help="print the verified stop command")

    flag_set = subparsers.add_parser("flag-set", help="set one raw flag variant")
    flag_set.add_argument("raw_flag_key")
    flag_set.add_argument("variant")
    flag_set.add_argument("--base-url", default=None)

    flag_reset = subparsers.add_parser("flag-reset", help="reset all flags to off")
    flag_reset.add_argument("--base-url", default=None)

    smoke = subparsers.add_parser("smoke", help="smoke-check a running demo")
    smoke.add_argument("--frontend-base-url", default=None)
    smoke.add_argument("--prometheus-base-url", default=None)
    smoke.add_argument("--opensearch-base-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "doctor":
        report = verify_source_checkout(args.demo_dir)
        print(f"root={report.root}")
        print(f"git_sha={report.git_sha}")
        print(f"missing_files={','.join(report.missing_files)}")
        return 0 if report.passed else 1
    if args.command == "start-command":
        print(" ".join(start_command()))
        return 0
    if args.command == "stop-command":
        print(" ".join(stop_command()))
        return 0
    if args.command == "flag-set":
        client = FlagdClient(base_url=args.base_url) if args.base_url else FlagdClient()
        client.set_flag_variant(args.raw_flag_key, args.variant)
        return 0
    if args.command == "flag-reset":
        client = FlagdClient(base_url=args.base_url) if args.base_url else FlagdClient()
        client.reset_all_flags()
        return 0
    if args.command == "smoke":
        targets = default_targets(
            frontend_base_url=args.frontend_base_url,
            prometheus_url=args.prometheus_base_url,
            opensearch_url=args.opensearch_base_url,
        )
        results = smoke_check(targets)
        for result in results:
            status = result.status_code if result.status_code is not None else "ERR"
            print(f"{result.name} {status} {result.url}")
        return 0 if all(result.ok for result in results) else 1
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
