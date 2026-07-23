import ast
from pathlib import Path


def test_application_layer_has_no_pyqt_imports():
    project = Path(__file__).parents[1] / "pet_harness"
    roots = (project / "app", project / "engine", project / "character", project / "skills", project / "xp", project / "models")
    violations = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                module = getattr(node, "module", "") or ""
                names = [alias.name for alias in getattr(node, "names", ())]
                if module.startswith("PyQt5") or any(name.startswith("PyQt5") for name in names):
                    violations.append(f"{path}:{node.lineno}")
    assert not violations, "Application layer imports PyQt: " + ", ".join(violations)


def test_ui_does_not_reach_through_adapter_to_router():
    ui_root = Path(__file__).parents[1] / "ui"
    violations = [str(path) for path in ui_root.rglob("*.py") if "adapter.router" in path.read_text(encoding="utf-8")]
    assert not violations, "UI reaches through adapter.router: " + ", ".join(violations)
