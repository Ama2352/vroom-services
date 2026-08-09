import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from finalization import finalize_diagnosis
from tests.test_validation import DLQ_CHAIN


def test_rejected_answer_discards_generated_action_and_placeholder_command():
    rejected = {
        "root_cause": "unsupported guess",
        "dev_action": "delete the pod",
        "kubectl_hint": "kubectl logs <dispatch-service-pod-name>",
        "acceptance_status": "rejected_after_refine",
        "_evaluation": {"hard": "failed", "semantic": "failed"},
    }

    final = finalize_diagnosis(rejected, DLQ_CHAIN, "vroom-dev", "dispatch-service")

    assert final["root_cause"].startswith("Insufficient evidence to confirm — observed: unknown event type")
    assert final["dev_action"] == "Do not run a remediation command until the diagnosis is reviewed."
    assert final["kubectl_hint"] == "kubectl get pods -n vroom-dev -l app=dispatch-service"
    assert final["diagnosis_decision"]["status"] == "rejected_after_refine"
