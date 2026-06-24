import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import rewoo_loop

_PLANNER_MSG = [{"role": "user", "content": "You are an SRE investigation planner for the Vroom ride-hailing platform on Kubernetes.\n\nAlert: HighErrorRate\nService: ride-service"}]
_SOLVER_MSG  = [{"role": "user", "content": "You are an SRE diagnosis expert for the Vroom ride-hailing platform.\n\nAlert: HighErrorRate"}]


def test_mock_planner_scale_to_zero():
    result = rewoo_loop._mock_llm(_PLANNER_MSG, "HighErrorRate", "ride-service", "vroom-dev", "scale_to_zero")
    assert '#E1 = get_pods' in result
    assert '#E2 = get_events' in result
    assert 'vroom-dev' in result
    assert 'ride-service' in result


def test_mock_planner_crashloop():
    result = rewoo_loop._mock_llm(_PLANNER_MSG, "HighErrorRate", "ride-service", "vroom-dev", "crashloop")
    assert '#E1 = get_pods' in result
    assert '#E2 = get_logs' in result


def test_mock_solver_scale_to_zero():
    result = rewoo_loop._mock_llm(_SOLVER_MSG, "HighErrorRate", "ride-service", "vroom-dev", "scale_to_zero")
    parsed = json.loads(result)
    assert parsed["remediation_tool"] == "scale_deployment"
    assert parsed["confidence"] == "HIGH"
    assert parsed["remediation_args"]["deployment"] == "ride-service"
    assert parsed["remediation_args"]["namespace"] == "vroom-dev"


def test_mock_solver_crashloop():
    result = rewoo_loop._mock_llm(_SOLVER_MSG, "HighErrorRate", "ride-service", "vroom-dev", "crashloop")
    parsed = json.loads(result)
    assert parsed["remediation_tool"] == "restart_deployment"
    assert parsed["confidence"] == "HIGH"


def test_mock_solver_unknown_scenario_falls_back_to_scale():
    result = rewoo_loop._mock_llm(_SOLVER_MSG, "HighErrorRate", "ride-service", "vroom-dev", "unknown_scenario")
    parsed = json.loads(result)
    assert parsed["remediation_tool"] == "scale_deployment"
