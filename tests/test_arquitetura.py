"""Cada camada só conhece as de baixo. A ingestão fica fora do fluxo do aluno."""
import ast
import pathlib

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

ALLOWED = {
    "base": {"base"},
    "prompts": {"prompts"},
    "conhecimento": {"base", "conhecimento"},
    "ferramentas": {"base", "conhecimento", "ferramentas"},
    "agentes": {"base", "conhecimento", "ferramentas", "agentes", "prompts"},
    "canais": {"base", "conhecimento", "ferramentas", "agentes", "canais"},
    "ingestao": {"base", "conhecimento", "ingestao"},
}


def app_imports(path: pathlib.Path) -> set[str]:
    """Camadas de app/ importadas pelo arquivo."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app":
            parts = node.module.split(".")
            found |= {parts[1]} if len(parts) > 1 else {alias.name for alias in node.names}
        elif isinstance(node, ast.Import):
            found |= {a.name.split(".")[1] for a in node.names if a.name.startswith("app.")}
    return found


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_camada_so_importa_as_de_baixo(layer):
    for path in (APP_DIR / layer).glob("*.py"):
        forbidden = app_imports(path) - ALLOWED[layer]
        assert not forbidden, f"{path.name} importa {forbidden}"


def test_todo_codigo_esta_em_uma_camada():
    loose = {p.name for p in APP_DIR.glob("*.py")} - {"__init__.py"}
    folders = {p.name for p in APP_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert loose == set()
    assert folders == set(ALLOWED)
