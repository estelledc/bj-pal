from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_trial_rehearsal_is_redacted_and_explicitly_synthetic() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/rehearse_trial.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["classification"] == "synthetic_rehearsal_not_human_evidence"
    assert payload["participant_capability_count"] == 5
    assert payload["phase_participant_counts"] == {"decision": 5, "outcome": 5}
    assert payload["decision_acceptance_rate"] == 0.8
    assert payload["outcome_completion_rate"] == 0.8
    assert payload["raw_capabilities_printed"] is False
    assert "trienroll-" not in completed.stdout
    assert "tripart-" not in completed.stdout
    assert "fbcap-" not in completed.stdout
