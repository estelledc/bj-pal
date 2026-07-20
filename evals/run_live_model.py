"""Run one explicitly authorized, fixed-scenario live model smoke test."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from agents.llm_client import DpskClient  # noqa: E402
from agents.model_output_contract import ModelOutputContractError  # noqa: E402
from application import PlanningService  # noqa: E402
from evals.live_model.observation import build_live_model_observation  # noqa: E402
from evals.live_model.quality import build_live_plan_quality_artifact  # noqa: E402
from evals.live_model.scenarios import (  # noqa: E402
    DEFAULT_SCENARIO_ID,
    SCENARIOS,
    get_scenario,
)


def _limits(client: DpskClient, service: PlanningService) -> dict[str, int]:
    config = client.config()
    policy = service.execution_budget_policy
    return {
        "max_output_tokens": config.max_tokens,
        "max_llm_calls": policy.max_llm_calls,
        "max_data_provider_batches": policy.max_data_provider_batches,
        "max_tool_calls": policy.max_tool_calls,
        "max_transport_attempts_per_llm_call": policy.max_transport_attempts_per_llm_call,
        "max_reported_tokens": policy.max_reported_tokens,
        "max_wall_clock_ms": policy.max_wall_clock_ms,
    }


def run(
    output: Path,
    *,
    scenario_id: str = DEFAULT_SCENARIO_ID,
    quality_output: Path | None = None,
) -> dict:
    backend = (os.environ.get("BJ_PAL_LLM") or "").lower()
    if backend not in {"dpsk", "deepseek"}:
        raise RuntimeError("BJ_PAL_LLM must be dpsk or deepseek for this live smoke")
    client = DpskClient()
    config = client.config()
    service = PlanningService()
    limits = _limits(client, service)
    scenario = get_scenario(scenario_id)
    request = scenario.request()
    result = None
    started = time.perf_counter()
    try:
        result = service.execute(request)
        snapshot = result.final_plan.model_output_context
        if not isinstance(snapshot, dict):
            raise RuntimeError("accepted plan did not retain a model-output snapshot")
        outcome = str(snapshot["status"])
    except ModelOutputContractError as exc:
        snapshot = exc.safe_details()
        outcome = "rejected"
    elapsed_ms = (time.perf_counter() - started) * 1000
    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    artifact = build_live_model_observation(
        observed_at=observed_at,
        scenario_id=scenario.scenario_id,
        provider="dpsk",
        model=config.model,
        endpoint_base_url=config.base_url,
        execution_limits=limits,
        outcome=outcome,
        elapsed_ms=elapsed_ms,
        model_output_contract=snapshot,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if quality_output is not None:
        if result is None:
            raise RuntimeError(
                "quality artifact requires an accepted fixed-scenario plan"
            )
        quality_artifact = build_live_plan_quality_artifact(
            observed_at=observed_at,
            scenario=scenario,
            provider=artifact["provider"],
            linked_observation_sha256=artifact["artifact_sha256"],
            result=result,
        )
        quality_output.parent.mkdir(parents=True, exist_ok=True)
        quality_output.write_text(
            json.dumps(quality_artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one paid/live DeepSeek smoke and retain only safe metadata."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--quality-output",
        type=Path,
        help=(
            "optional sanitized plan-quality artifact; fixed synthetic scenarios only"
        ),
    )
    parser.add_argument(
        "--scenario-id",
        choices=sorted(SCENARIOS),
        default=DEFAULT_SCENARIO_ID,
    )
    parser.add_argument("--ack-provider-cost", action="store_true")
    args = parser.parse_args()
    if not args.ack_provider_cost:
        parser.error("--ack-provider-cost is required before any external model call")
    artifact = run(
        args.output,
        scenario_id=args.scenario_id,
        quality_output=args.quality_output,
    )
    print(
        "live model observation written: "
        f"outcome={artifact['result']['outcome']} scenario={artifact['scenario_id']}"
    )
    # A contract rejection is a valid smoke observation: the verifier and
    # caller decide how to interpret it. Infrastructure errors still raise.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
