PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
EVAL_ARTIFACT ?= evals/results/public-core.json
RETRIEVAL_ARTIFACT ?= evals/results/retrieval-core.json
PERFORMANCE_ARTIFACT ?= evals/results/http-performance.json
SOCKET_PERFORMANCE_ARTIFACT ?= evals/results/socket-http-performance.json
WEATHER_ARTIFACT ?= evals/results/weather-acceptance.json
REQUIREMENTS_ARTIFACT ?= evals/results/requirements-core.json
CONSTRAINTS_ARTIFACT ?= evals/results/constraints-core.json
CLARIFICATIONS_ARTIFACT ?= evals/results/clarifications-core.json
OBSERVABILITY_ARTIFACT ?= evals/results/observability-core.json
JOB_DIAGNOSTICS_ARTIFACT ?= evals/results/job-diagnostics-contract.json
WORKLOAD_HEALTH_ARTIFACT ?= evals/results/workload-health-contract.json
TOOL_AUDIT_ARTIFACT ?= evals/results/tool-audit-contract.json
STATE_LAYOUT_ARTIFACT ?= evals/results/state-layout-contract.json
PREDICTION_STATE_ARTIFACT ?= evals/results/prediction-state-contract.json
USER_MEMORY_STATE_ARTIFACT ?= evals/results/user-memory-state-contract.json
LEGACY_RETIREMENT_ARTIFACT ?= evals/results/legacy-retirement-contract.json
RELEASE_CANDIDATE_MANIFEST ?= runtime/release_candidate_manifest.json
EXECUTION_BUDGET_ARTIFACT ?= evals/results/execution-budget-core.json
MODEL_OUTPUT_ARTIFACT ?= evals/results/model-output-contract.json
ORCHESTRATION_ARTIFACT ?= evals/results/orchestration-comparison.json
SCHEDULING_ARTIFACT ?= evals/results/scheduling-core.json
ACCESS_CONTROL_ARTIFACT ?= evals/results/access-control-core.json
SIDE_EFFECT_ARTIFACT ?= evals/results/side-effect-safety.json
OUTCOME_ARTIFACT ?= evals/results/human-outcome-contract.json
TRIAL_ARTIFACT ?= evals/results/trial-evidence-contract.json
LIVE_MODEL_OBSERVATION ?= evals/live_model/observations/2026-07-20-dpsk-flash.json
LIVE_MODEL_FLASH_AFTER_FIX ?= evals/live_model/observations/2026-07-20-dpsk-flash-after-prompt-fix.json
LIVE_MODEL_PRO_AFTER_FIX ?= evals/live_model/observations/2026-07-20-dpsk-pro-after-prompt-fix.json
LIVE_MODEL_PRO_FAMILY ?= evals/live_model/observations/2026-07-20-dpsk-pro-family-wudaoying.json
LIVE_MODEL_PRO_SOLO ?= evals/live_model/observations/2026-07-20-dpsk-pro-solo-798.json
LIVE_MODEL_OUTPUT ?= evals/results/live-model-observation.json
LIVE_QUALITY_OBS_FRIENDS ?= evals/live_model/observations/2026-07-20-dpsk-pro-sanlitun-quality-v2.json
LIVE_QUALITY_OBS_FAMILY ?= evals/live_model/observations/2026-07-20-dpsk-pro-family-quality-v2.json
LIVE_QUALITY_OBS_SOLO ?= evals/live_model/observations/2026-07-20-dpsk-pro-solo-quality-v2.json
LIVE_QUALITY_FRIENDS ?= evals/live_model/quality_artifacts/2026-07-20-dpsk-pro-sanlitun-quality-v2.json
LIVE_QUALITY_FAMILY ?= evals/live_model/quality_artifacts/2026-07-20-dpsk-pro-family-quality-v2.json
LIVE_QUALITY_SOLO ?= evals/live_model/quality_artifacts/2026-07-20-dpsk-pro-solo-quality-v2.json
LIVE_PLAN_QUALITY_OUTPUT ?= evals/results/live-plan-quality.json
ACK_PROVIDER_COST ?= 0
PERFORMANCE_REQUESTS ?= 20
PERFORMANCE_CONCURRENCY ?= 4
CHECK_PLAN_EVIDENCE_DB ?= $(CURDIR)/runtime/check/plan-evidence.db
CHECK_USER_MEMORY_DB ?= $(CURDIR)/runtime/check/user-memory.db
CHECK_PREDICTION_DB ?= $(CURDIR)/runtime/check/prediction-feedback.db
CHECK_TOOL_AUDIT_DB ?= $(CURDIR)/runtime/check/tool-audit.db
CHECK_CLARIFICATION_DB ?= $(CURDIR)/runtime/check/clarifications.db
CHECK_JOB_DB ?= $(CURDIR)/runtime/check/planning-jobs.db
CHECK_FEEDBACK_DB ?= $(CURDIR)/runtime/check/plan-feedback.db

.PHONY: python-check secret-scan setup bootstrap-demo test api api-smoke job-worker job-smoke operation-worker docker-build demo demo-clarification demo-trial trial-operator-help showcase audit-legacy-retirement audit-release-candidate eval-public eval-retrieval eval-requirements eval-constraints eval-clarifications eval-observability eval-job-diagnostics eval-workload-health eval-tool-audit eval-state-layout eval-prediction-state eval-user-memory-state eval-legacy-retirement eval-execution-budget eval-model-output eval-orchestration eval-scheduling eval-access-control eval-side-effects eval-outcomes eval-trials eval-weather verify-live-model-observation verify-live-plan-quality live-model-smoke weather-live-smoke benchmark-http benchmark-socket-http check-runtime-reset check

python-check:
	$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "BJ-Pal requires Python 3.11+"'

secret-scan: python-check
	$(PYTHON) scripts/check_no_secrets.py

setup: python-check
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt

bootstrap-demo: python-check
	$(PYTHON) scripts/build_mock_data.py --profile demo
	$(PYTHON) src/loader.py

test: python-check
	$(PYTHON) -m pytest

api: python-check
	BJ_PAL_LLM=mock $(PYTHON) -m uvicorn http_api.app:app --app-dir src --host 0.0.0.0 --port 8000

api-smoke: python-check
	BJ_PAL_LLM=mock $(PYTHON) scripts/smoke_http_api.py

job-worker: python-check
	BJ_PAL_LLM=mock $(PYTHON) scripts/run_job_worker.py

job-smoke: python-check
	BJ_PAL_LLM=mock $(PYTHON) scripts/smoke_job_worker.py

operation-worker: python-check
	$(PYTHON) scripts/run_operation_worker.py

docker-build:
	docker build --tag bj-pal:local .

demo: python-check
	BJ_PAL_LLM=mock $(PYTHON) src/demo_cli.py --book --approve-sandbox-booking

demo-clarification: python-check
	BJ_PAL_LLM=mock $(PYTHON) src/demo_cli.py --clarification-demo --clarification-choice text

demo-trial: python-check
	$(PYTHON) scripts/rehearse_trial.py

trial-operator-help: python-check
	$(PYTHON) scripts/manage_trial.py --help

audit-legacy-retirement: python-check
	$(PYTHON) scripts/audit_legacy_retirement.py --require-ready

audit-release-candidate: python-check
	$(PYTHON) scripts/audit_release_candidate.py --output $(RELEASE_CANDIDATE_MANIFEST) --require-ready
	$(PYTHON) scripts/verify_release_candidate_manifest.py $(RELEASE_CANDIDATE_MANIFEST)

showcase: python-check
	bash scripts/build_showcase.sh
	$(PYTHON) -m unittest tests.test_showcase_site tests.test_promo_refresh

eval-public: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_public.py --output $(EVAL_ARTIFACT)
	$(PYTHON) evals/verify_artifact.py $(EVAL_ARTIFACT)

eval-retrieval: python-check
	$(PYTHON) evals/run_retrieval.py --output $(RETRIEVAL_ARTIFACT)
	$(PYTHON) evals/verify_retrieval.py $(RETRIEVAL_ARTIFACT)

eval-requirements: python-check
	$(PYTHON) evals/run_requirements.py --output $(REQUIREMENTS_ARTIFACT)
	$(PYTHON) evals/verify_requirements.py $(REQUIREMENTS_ARTIFACT)

eval-constraints: python-check
	$(PYTHON) evals/run_constraints.py --output $(CONSTRAINTS_ARTIFACT)
	$(PYTHON) evals/verify_constraints.py $(CONSTRAINTS_ARTIFACT)

eval-clarifications: python-check
	$(PYTHON) evals/run_clarifications.py --output $(CLARIFICATIONS_ARTIFACT)
	$(PYTHON) evals/verify_clarifications.py $(CLARIFICATIONS_ARTIFACT)

eval-observability: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_observability.py --output $(OBSERVABILITY_ARTIFACT)
	$(PYTHON) evals/verify_observability.py $(OBSERVABILITY_ARTIFACT)

eval-job-diagnostics: python-check
	$(PYTHON) evals/run_job_diagnostics.py --output $(JOB_DIAGNOSTICS_ARTIFACT)
	$(PYTHON) evals/verify_job_diagnostics.py $(JOB_DIAGNOSTICS_ARTIFACT)

eval-workload-health: python-check
	$(PYTHON) evals/run_workload_health.py --output $(WORKLOAD_HEALTH_ARTIFACT)
	$(PYTHON) evals/verify_workload_health.py $(WORKLOAD_HEALTH_ARTIFACT)

eval-tool-audit: python-check
	$(PYTHON) evals/run_tool_audit.py --output $(TOOL_AUDIT_ARTIFACT)
	$(PYTHON) evals/verify_tool_audit.py $(TOOL_AUDIT_ARTIFACT)

eval-state-layout: python-check
	$(PYTHON) evals/run_state_layout.py --output $(STATE_LAYOUT_ARTIFACT)
	$(PYTHON) evals/verify_state_layout.py $(STATE_LAYOUT_ARTIFACT)

eval-prediction-state: python-check
	$(PYTHON) evals/run_prediction_state.py --output $(PREDICTION_STATE_ARTIFACT)
	$(PYTHON) evals/verify_prediction_state.py $(PREDICTION_STATE_ARTIFACT)

eval-user-memory-state: python-check
	$(PYTHON) evals/run_user_memory_state.py --output $(USER_MEMORY_STATE_ARTIFACT)
	$(PYTHON) evals/verify_user_memory_state.py $(USER_MEMORY_STATE_ARTIFACT)

eval-legacy-retirement: python-check
	$(PYTHON) evals/run_legacy_retirement.py --output $(LEGACY_RETIREMENT_ARTIFACT)
	$(PYTHON) evals/verify_legacy_retirement.py $(LEGACY_RETIREMENT_ARTIFACT)

eval-execution-budget: python-check
	$(PYTHON) evals/run_execution_budget.py --output $(EXECUTION_BUDGET_ARTIFACT)
	$(PYTHON) evals/verify_execution_budget.py $(EXECUTION_BUDGET_ARTIFACT)

eval-model-output: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_model_output.py --output $(MODEL_OUTPUT_ARTIFACT)
	$(PYTHON) evals/verify_model_output.py $(MODEL_OUTPUT_ARTIFACT)

verify-live-model-observation: python-check
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_OBSERVATION)
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_FLASH_AFTER_FIX)
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_PRO_AFTER_FIX)
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_PRO_FAMILY)
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_PRO_SOLO)
	$(PYTHON) evals/verify_live_model_comparison.py $(LIVE_MODEL_FLASH_AFTER_FIX) $(LIVE_MODEL_PRO_AFTER_FIX)
	$(PYTHON) evals/verify_live_model_suite.py $(LIVE_MODEL_PRO_AFTER_FIX) $(LIVE_MODEL_PRO_FAMILY) $(LIVE_MODEL_PRO_SOLO)

verify-live-plan-quality: python-check
	$(PYTHON) evals/verify_live_plan_quality.py $(LIVE_QUALITY_FRIENDS) $(LIVE_QUALITY_OBS_FRIENDS)
	$(PYTHON) evals/verify_live_plan_quality.py $(LIVE_QUALITY_FAMILY) $(LIVE_QUALITY_OBS_FAMILY)
	$(PYTHON) evals/verify_live_plan_quality.py $(LIVE_QUALITY_SOLO) $(LIVE_QUALITY_OBS_SOLO)
	$(PYTHON) evals/verify_live_plan_quality_suite.py $(LIVE_QUALITY_FRIENDS)=$(LIVE_QUALITY_OBS_FRIENDS) $(LIVE_QUALITY_FAMILY)=$(LIVE_QUALITY_OBS_FAMILY) $(LIVE_QUALITY_SOLO)=$(LIVE_QUALITY_OBS_SOLO)

live-model-smoke: python-check
	$(PYTHON) -c 'assert "$(ACK_PROVIDER_COST)" == "1", "set ACK_PROVIDER_COST=1 to authorize one external model smoke"'
	$(PYTHON) evals/run_live_model.py --ack-provider-cost --output $(LIVE_MODEL_OUTPUT) --quality-output $(LIVE_PLAN_QUALITY_OUTPUT)
	$(PYTHON) evals/verify_live_model.py $(LIVE_MODEL_OUTPUT)
	$(PYTHON) evals/verify_live_plan_quality.py $(LIVE_PLAN_QUALITY_OUTPUT) $(LIVE_MODEL_OUTPUT)

eval-orchestration: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_orchestration.py --output $(ORCHESTRATION_ARTIFACT)
	$(PYTHON) evals/verify_orchestration.py $(ORCHESTRATION_ARTIFACT)

eval-scheduling: python-check
	$(PYTHON) evals/run_scheduling.py --output $(SCHEDULING_ARTIFACT)
	$(PYTHON) evals/verify_scheduling.py $(SCHEDULING_ARTIFACT)

eval-access-control: python-check
	$(PYTHON) evals/run_access_control.py --output $(ACCESS_CONTROL_ARTIFACT)
	$(PYTHON) evals/verify_access_control.py $(ACCESS_CONTROL_ARTIFACT)

eval-side-effects: python-check
	$(PYTHON) evals/run_side_effects.py --output $(SIDE_EFFECT_ARTIFACT)
	$(PYTHON) evals/verify_side_effects.py $(SIDE_EFFECT_ARTIFACT)

eval-outcomes: python-check
	$(PYTHON) evals/run_outcomes.py --output $(OUTCOME_ARTIFACT)
	$(PYTHON) evals/verify_outcomes.py $(OUTCOME_ARTIFACT)

eval-trials: python-check
	$(PYTHON) evals/run_trials.py --output $(TRIAL_ARTIFACT)
	$(PYTHON) evals/verify_trials.py $(TRIAL_ARTIFACT)

eval-weather: python-check
	$(PYTHON) evals/run_weather_acceptance.py --output $(WEATHER_ARTIFACT)
	$(PYTHON) evals/verify_weather_acceptance.py $(WEATHER_ARTIFACT)

weather-live-smoke: python-check
	$(PYTHON) scripts/smoke_weather_provider.py --live

benchmark-http: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_http_benchmark.py --requests $(PERFORMANCE_REQUESTS) --concurrency $(PERFORMANCE_CONCURRENCY) --output $(PERFORMANCE_ARTIFACT)
	$(PYTHON) evals/verify_http_benchmark.py $(PERFORMANCE_ARTIFACT)

benchmark-socket-http: python-check
	BJ_PAL_LLM=mock $(PYTHON) evals/run_socket_http_benchmark.py --requests $(PERFORMANCE_REQUESTS) --concurrency $(PERFORMANCE_CONCURRENCY) --output $(SOCKET_PERFORMANCE_ARTIFACT)
	$(PYTHON) evals/verify_http_benchmark.py $(SOCKET_PERFORMANCE_ARTIFACT)

check-runtime-reset:
	$(PYTHON) -c 'from pathlib import Path; paths=[Path("$(CHECK_PLAN_EVIDENCE_DB)"), Path("$(CHECK_USER_MEMORY_DB)"), Path("$(CHECK_PREDICTION_DB)"), Path("$(CHECK_TOOL_AUDIT_DB)"), Path("$(CHECK_CLARIFICATION_DB)"), Path("$(CHECK_JOB_DB)"), Path("$(CHECK_FEEDBACK_DB)")]; [candidate.unlink(missing_ok=True) for p in paths for candidate in (p, Path(str(p)+"-journal"), Path(str(p)+"-shm"), Path(str(p)+"-wal"))]'

check: export BJ_PAL_PLAN_EVIDENCE_DB := $(CHECK_PLAN_EVIDENCE_DB)
check: export BJ_PAL_USER_MEMORY_DB := $(CHECK_USER_MEMORY_DB)
check: export BJ_PAL_PREDICTION_DB := $(CHECK_PREDICTION_DB)
check: export BJ_PAL_TOOL_AUDIT_DB := $(CHECK_TOOL_AUDIT_DB)
check: export BJ_PAL_CLARIFICATION_DB := $(CHECK_CLARIFICATION_DB)
check: export BJ_PAL_JOB_DB := $(CHECK_JOB_DB)
check: export BJ_PAL_FEEDBACK_DB := $(CHECK_FEEDBACK_DB)
check: check-runtime-reset secret-scan bootstrap-demo test api-smoke job-smoke demo demo-clarification demo-trial trial-operator-help showcase eval-public eval-retrieval eval-requirements eval-constraints eval-clarifications eval-observability eval-job-diagnostics eval-workload-health eval-tool-audit eval-state-layout eval-prediction-state eval-user-memory-state eval-legacy-retirement eval-execution-budget eval-model-output verify-live-model-observation verify-live-plan-quality eval-orchestration eval-scheduling eval-access-control eval-side-effects eval-outcomes eval-trials eval-weather benchmark-http benchmark-socket-http
	$(MAKE) check-runtime-reset PYTHON=$(PYTHON)
