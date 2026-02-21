import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def test_locked_step_definitions_are_synced():
    api_steps = _load_assignment(ROOT / "apps/api/app/services/run_service.py", "LOCKED_STEPS")
    orchestrator_steps = _load_assignment(
        ROOT / "services/orchestrator/orchestrator/state_machine.py", "LOCKED_STEP_ORDER"
    )
    common_steps = _load_assignment(ROOT / "packages/common/personal_stylist_common/constants.py", "LOCKED_STEPS")
    runtime_task_keys = _load_assignment(ROOT / "packages/crewai_runtime/personal_stylist_crewai/tasks.py", "TASK_KEYS")

    assert api_steps == orchestrator_steps == common_steps
    assert [step_key for step_key, _ in api_steps] == runtime_task_keys
