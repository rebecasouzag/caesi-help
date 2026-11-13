"""Prompts dos agentes. Cada arquivo é versionado, para comparar versões na avaliação."""
import pathlib

PROMPTS_DIR = pathlib.Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").removesuffix("\n")
