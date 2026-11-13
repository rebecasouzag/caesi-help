"""Conversa pelo terminal. Uso: python -m app.canais.cli"""
from dataclasses import dataclass

from app.agentes.grafo import answer_question, detect_curriculum

EXIT_WORDS = {"sair", "exit", "quit"}


@dataclass
class Session:
    curriculum: str | None = None


def handle_message(session: Session, text: str) -> str:
    session.curriculum = detect_curriculum(text) or session.curriculum
    return answer_question(text, curriculum=session.curriculum, channel="cli")["answer"]


def main() -> None:
    print("caesi-help: tire suas dúvidas sobre o curso. Digite 'sair' para encerrar.")
    session = Session()
    while True:
        try:
            text = input("\nVocê: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in EXIT_WORDS:
            break
        if not text:
            continue
        try:
            print(f"\nBot: {handle_message(session, text)}")
        except Exception as exc:
            print(f"\nErro ao processar a pergunta ({type(exc).__name__}): {exc}")


if __name__ == "__main__":
    main()
