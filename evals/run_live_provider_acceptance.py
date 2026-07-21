"""Run one explicitly authorized DeepSeek acceptance through CSSwitch config."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for item in (ROOT, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from agents.llm_client import DpskClient  # noqa: E402
from application import PlanningService  # noqa: E402
from evals.live_model.observation import build_live_model_observation  # noqa: E402
from evals.live_model.quality import build_live_plan_quality_artifact  # noqa: E402
from evals.live_model.scenarios import DEFAULT_SCENARIO_ID, SCENARIOS, get_scenario  # noqa: E402
from evals.live_provider.acceptance import build_live_provider_acceptance  # noqa: E402
from evals.live_provider.credential_source import (  # noqa: E402
    CsswitchCredential,
    load_csswitch_credential,
)


DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_OUTPUT_TOKENS = 4096
_BUDGET_ENV = {
    "BJ_PAL_MAX_LLM_CALLS": "2",
    "BJ_PAL_MAX_DATA_PROVIDER_BATCHES": "1",
    "BJ_PAL_MAX_TOOL_CALLS": "8",
    "BJ_PAL_MAX_TRANSPORT_ATTEMPTS_PER_LLM_CALL": "2",
    "BJ_PAL_MAX_REPORTED_TOKENS": "16384",
    "BJ_PAL_MAX_EXECUTION_MS": "90000",
}


@contextmanager
def _bounded_execution_environment() -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in _BUDGET_ENV}
    try:
        os.environ.update(_BUDGET_ENV)
        yield
    finally:
        for key in _BUDGET_ENV:
            os.environ.pop(key, None)
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value


def _limits(client: DpskClient, service: PlanningService) -> dict[str, int]:
    config = client.config()
    policy = service.execution_budget_policy
    return {
        "max_output_tokens": config.max_tokens,
        "max_llm_calls": policy.max_llm_calls,
        "max_data_provider_batches": policy.max_data_provider_batches,
        "max_tool_calls": policy.max_tool_calls,
        "max_transport_attempts_per_llm_call": (
            policy.max_transport_attempts_per_llm_call
        ),
        "max_reported_tokens": policy.max_reported_tokens,
        "max_wall_clock_ms": policy.max_wall_clock_ms,
    }


def _write_mode_0600(path: Path, payload: dict) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # The incomplete, non-overwritable file is intentionally retained as
        # visible failure evidence; callers choose whether to remove it.
        raise


def run(
    output_dir: Path,
    *,
    credential: CsswitchCredential,
    model: str = DEFAULT_MODEL,
    scenario_id: str = DEFAULT_SCENARIO_ID,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict:
    """Execute and publish one three-file, non-overwriting acceptance bundle."""
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"acceptance output directory already exists: {output_dir}")
    scenario = get_scenario(scenario_id)
    started = time.perf_counter()
    with credential.provider_environment(
        model=model,
        max_output_tokens=max_output_tokens,
    ):
        with _bounded_execution_environment():
            client = DpskClient()
            service = PlanningService()
            limits = _limits(client, service)
            result = service.execute(scenario.request())
            config = client.config()
    elapsed_ms = (time.perf_counter() - started) * 1000
    snapshot = result.final_plan.model_output_context
    if not isinstance(snapshot, dict):
        raise RuntimeError("accepted live plan has no model-output contract snapshot")
    outcome = str(snapshot.get("status"))
    if outcome not in {"accepted", "accepted_after_repair"}:
        raise RuntimeError("live provider result did not satisfy the model-output contract")
    if not result.execution.verify_integrity():
        raise RuntimeError("live execution observation failed integrity verification")

    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    observation = build_live_model_observation(
        observed_at=observed_at,
        scenario_id=scenario.scenario_id,
        provider="dpsk",
        model=config.model,
        endpoint_base_url=config.base_url,
        execution_limits=limits,
        outcome=outcome,
        elapsed_ms=elapsed_ms,
        model_output_contract=snapshot,
        recording_method="csswitch_handoff_runner",
    )
    quality = build_live_plan_quality_artifact(
        observed_at=observed_at,
        scenario=scenario,
        provider=observation["provider"],
        linked_observation_sha256=observation["artifact_sha256"],
        result=result,
    )
    receipt = build_live_provider_acceptance(
        observation=observation,
        quality=quality,
        execution=result.execution.to_dict(),
        credential_metadata=credential.safe_metadata(),
        credential_value=credential.api_key,
        explicit_cost_ack=True,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    os.mkdir(output_dir, mode=0o700)
    _write_mode_0600(output_dir / "observation.json", observation)
    _write_mode_0600(output_dir / "quality.json", quality)
    _write_mode_0600(output_dir / "acceptance.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run one bounded DeepSeek acceptance using an explicitly selected "
            "local CSSwitch credential profile."
        )
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--credential-source", choices=["csswitch"], required=True)
    parser.add_argument("--csswitch-config", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--scenario-id", choices=sorted(SCENARIOS), default=DEFAULT_SCENARIO_ID
    )
    parser.add_argument(
        "--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS
    )
    parser.add_argument("--ack-provider-cost", action="store_true")
    args = parser.parse_args()
    if not args.ack_provider_cost:
        parser.error("--ack-provider-cost is required before any external model call")
    credential = load_csswitch_credential(args.csswitch_config)
    receipt = run(
        args.output_dir,
        credential=credential,
        model=args.model,
        scenario_id=args.scenario_id,
        max_output_tokens=args.max_output_tokens,
    )
    usage = receipt["execution_evidence"]["provider_reported_usage"]
    print(
        "live provider acceptance written: "
        f"gate_pass={receipt['acceptance']['gate_pass']} "
        f"scenario={receipt['scenario_id']} "
        f"reported_total_tokens={usage['total_tokens']}"
    )
    return 0 if receipt["acceptance"]["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
