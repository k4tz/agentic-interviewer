import ast
import re
from importlib.util import resolve_name
from pathlib import Path

import pytest

from agentic_interviewer.domain.models import ExecutionControls, SpeechRequest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "agentic_interviewer"
NETWORK_CLIENTS = {"openai", "anthropic", "httpx", "requests", "websockets", "aiohttp", "boto3"}
CORE_LAYERS = ("domain", "workflow", "services", "operations", "security", "policy")


def imported_modules(source: str, *, package: str) -> set[str]:
    """Resolve relative imports and from-import aliases, not just top-level names."""
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def python_files(layer: str):
    path = PACKAGE_ROOT / layer
    return path.rglob("*.py") if path.is_dir() else (path.with_suffix(".py"),)


def imports_for(path: Path) -> set[str]:
    package = ".".join(path.parent.relative_to(PACKAGE_ROOT.parent).parts)
    return imported_modules(path.read_text(encoding="utf-8"), package=package)


@pytest.mark.parametrize(
    "source",
    [
        "import agentic_interviewer.adapters.speaches",
        "from agentic_interviewer.adapters import speaches",
        "from ..adapters import speaches",
        "from agentic_interviewer import adapters",
    ],
)
def test_boundary_check_resolves_provider_import_forms(source):
    modules = imported_modules(source, package="agentic_interviewer.services")
    assert any(module.startswith("agentic_interviewer.adapters") for module in modules)


def test_core_layers_do_not_depend_on_provider_implementations_or_transport():
    forbidden_layers = ("adapters", "composition", "api", "config")
    forbidden_internal = tuple(f"agentic_interviewer.{layer}" for layer in forbidden_layers)
    violations = []
    for layer in CORE_LAYERS:
        for path in python_files(layer):
            for module in imports_for(path):
                if module.split(".")[0] in NETWORK_CLIENTS or any(
                    module == prefix or module.startswith(prefix + ".")
                    for prefix in forbidden_internal
                ):
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)}: {module}")
    assert violations == []


def test_api_uses_composition_and_ports_not_provider_clients():
    violations = []
    for path in python_files("api"):
        for module in imports_for(path):
            if module.split(".")[0] in NETWORK_CLIENTS or module.startswith(
                "agentic_interviewer.adapters"
            ):
                violations.append(f"{path.name}: {module}")
    assert violations == []


def test_provider_branding_and_defaults_stay_out_of_core_and_candidate_ui():
    provider_tokens = re.compile(r"\b(speaches|kokoro|af_heart|vllm|qwen|mistral)\b", re.I)
    paths = [path for layer in (*CORE_LAYERS, "api") for path in python_files(layer)]
    paths.extend((PACKAGE_ROOT / "web").glob("*.js"))
    paths.extend((PACKAGE_ROOT / "web").glob("*.html"))
    violations = [
        str(path.relative_to(PACKAGE_ROOT))
        for path in paths
        if provider_tokens.search(path.read_text(encoding="utf-8"))
    ]
    assert violations == []


def test_generic_compatible_adapter_does_not_depend_on_a_named_provider():
    path = PACKAGE_ROOT / "adapters" / "openai_compatible.py"
    assert not re.search(
        r"\b(speaches|kokoro|af_heart|vllm|qwen|mistral)\b",
        path.read_text(encoding="utf-8"),
        re.I,
    )


def test_speech_request_leaves_provider_voice_selection_to_adapter():
    request = SpeechRequest(text="Next question.", controls=ExecutionControls(idempotency_key="1"))
    assert request.voice is None
    assert request.model_copy(update={"voice": "configured-voice"}).voice == "configured-voice"
