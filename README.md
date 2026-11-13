# caesi-help

Assistente de dúvidas para alunos de Ciência da Computação da UFCG. Hoje essas perguntas (disciplinas, professores, regras do
curso, processos internos) chegam ao Centro Acadêmico e são respondidas à mão. Este projeto tenta responder o que tem base e
encaminhar o resto para uma pessoa, primeiro pelo terminal e, depois de validado, pelo WhatsApp.

O projeto é de estudo e de uso real ao mesmo tempo. Toda técnica adotada precisa ser medida em uma avaliação, e nenhuma entra só
para ser estudada.

## Princípios

1. **Sempre com fonte.** A resposta vem de um FAQ curado, de documentos oficiais ou de dados do Eureca, e termina citando de onde
   veio (por exemplo, "Fonte: Regulamento de Graduação, Art. 270").
2. **Sem base, não inventa.** Quando não encontra apoio, resume o caso e encaminha para um membro do CA.
3. **Melhora com o uso.** A resposta humana volta para a base, mas só depois de aprovada por uma pessoa.
4. **Regras críticas em código.** O que não pode falhar (por exemplo, nunca responder uma entrada de FAQ ainda em rascunho)
   fica em código e em testes, nunca só em prompt.

## Arquitetura

O código está organizado em camadas, e cada camada só conhece as de baixo. Um teste (`tests/test_arquitetura.py`) falha se
essa regra for violada.

```
app/canais/        terminal hoje; API e WhatsApp depois
app/agentes/       fluxo da pergunta (grafo LangGraph)
app/prompts/       um arquivo por etapa que usa LLM
app/ferramentas/   funções que os agentes chamam; o resultado vira Evidência
app/conhecimento/  manifest, catálogo, resolução de nomes, requisitos, busca
app/base/          LLM, embeddings, banco, cliente do Eureca, configuração
app/ingestao/      fora do fluxo do aluno: extrai, divide, gera embeddings, sincroniza
```

### Fluxo atual

Hoje a pergunta passa por um grafo único: classifica a intenção, busca no FAQ e nos documentos, redige a resposta citando os
trechos, verifica se o texto é sustentado por eles e aplica as políticas (fontes, avisos e encaminhamento).

### Fluxo alvo (multiagente)

O próximo passo é trocar o grafo único por agentes especializados, **um por vez, cada um medido contra o fluxo atual**:

```
mensagem + histórico → ORQUESTRADOR (reescreve, divide em tarefas, escolhe especialistas)
  ├ conversa ou fora de escopo → resposta curta
  └ em paralelo: ACADÊMICO | NORMAS E PROCESSOS | CALENDÁRIO → Evidências
       → SINTETIZADOR (redige citando as Evidências)
       → VERIFICADOR (confere cada afirmação; 1 nova tentativa, depois ENCAMINHADOR)
       → POLÍTICAS (fontes, links, avisos) → resposta e registro

Fora do fluxo do aluno: o ENCAMINHADOR resume o caso para o CA; o CURADOR propõe entradas de FAQ, e só um humano publica.
```

Decisões de projeto:

- **Evidências, não texto livre.** Os especialistas devolvem objetos `Evidencia(id, texto, fonte, url, procedencia, validade)`.
  O LLM cita ids e o código monta as fontes, então o modelo não consegue inventar uma referência.
- **Procedência.** Toda informação é `oficial`, `ca` ou `comunidade`. Em conflito, vale Eureca, depois norma oficial mais
  recente, depois CA, depois comunidade. Resposta de colega sai sempre identificada.
- **Limites por pergunta** de iterações, chamadas de LLM e tokens. Se estourar, encaminha.
- **Erro técnico não chega ao aluno.** O sistema tenta de novo e, se falhar, avisa e encaminha.
- **Nota de similaridade não é confiança.** As notas do modelo de embeddings são parecidas dentro e fora da base, então quem
  decide se há resposta são o agente e o verificador.
- **Multiagente não é melhor por definição.** Ele será comparado com o grafo atual e com um agente único, na mesma avaliação.

## Recuperação (RAG)

- **Embeddings locais** com `intfloat/multilingual-e5-small` (384 dimensões), com os prefixos `query:` e `passage:` que o modelo exige.
- **Divisão estrutural** dos documentos normativos: um trecho por artigo, com o caminho Título > Capítulo > Seção junto do texto.
- **Busca híbrida:** vetores (pgvector) e palavras exatas, fundidos por posição com Reciprocal Rank Fusion.
- **Reranking** opcional com um cross-encoder sobre os 30 melhores candidatos.
- **Manifest** que manda: cada documento tem status (`vigente`, `desatualizado`, `revogada`), e o que está revogado nunca é usado.

## Dados

| Dado | Fonte |
|---|---|
| Grade, turmas, salas, calendário | API do Eureca, apenas o curso de Ciência da Computação |
| Ementas e pré-requisitos | Projeto pedagógico do curso, mais correções revisadas pelo CA |
| Regras | Documentos oficiais e páginas do site do curso |
| Respostas prontas | FAQ curado pelo CA e relatos de alunos, estes sempre sinalizados |

Os dados do curso, o catálogo de pessoas e as perguntas de avaliação **não estão neste repositório**: contêm informações de
professores e material escrito pelo CA para a medição final. O código e os testes estão aqui, mas parte dos testes
precisa desses arquivos para rodar.

## Avaliação

O conjunto de desenvolvimento tem 132 perguntas, classificadas por categoria e com algumas marcadas como críticas, que não
podem errar. Uma segunda lista, escrita pelo CA e nunca vista pelo desenvolvimento, serve para a medição final. A nota por
palavras-chave é só um filtro: as respostas críticas e as que mudaram de resultado são lidas à mão a cada etapa.

```
python -m eval.run_eval --recuperacao   # só a busca, sem custo de LLM
python -m eval.run_eval --rapido        # subconjunto, com cache de respostas do LLM
python -m eval.run_eval                 # conjunto completo
python -m eval.run_eval --final         # perguntas reservadas
```

Metas para ir ao WhatsApp, nas perguntas reservadas:

| Métrica | Meta |
|---|---|
| Erro em pergunta crítica | 0 |
| Resposta errada dada com segurança | ≤ 3% |
| Acerto nas perguntas que a base responde | ≥ 85% |
| Encaminhamento correto do que está fora da base | ≥ 90% |
| Recusa indevida de tema do curso | ≤ 2% |
| Resposta de colega sem aviso | 0 |
| Latência p95 no terminal | ≤ 15 s |

Na recuperação, medida sem LLM com a busca híbrida, a entrada certa do FAQ aparece entre as 5 primeiras em 59 de 60
perguntas (98%). O documento citado pelo FAQ aparece entre os 8 primeiros em 89% dos casos, e o artigo exato em 72%. Esse último
número é o ponto fraco atual e é o que a etapa de RAG ainda precisa melhorar.

## Stack

Python 3.11+, LangGraph e LangChain, Pydantic para toda saída estruturada, Supabase (Postgres com pgvector), Groq como provedor
de LLM atrás de uma função única, `sentence-transformers` para embeddings e reranking, pytest. FastAPI e um painel em
Streamlit entram nas etapas finais.

## Etapas

1. ✅ Linha de base de avaliação, com revisão do CA.
2. ✅ Reorganização em camadas, sem mudar comportamento.
3. ✅ Conhecimento: catálogo, resolução de nomes, requisitos, procedência, validação cruzada das fontes.
4. ✅ Ferramentas testadas sem LLM, incluindo o decodificador de horário do SIGAA e o calendário.
5. 🔄 RAG: divisão estrutural, busca híbrida com RRF e reranking.
6. Agentes, um por vez: Acadêmico, Normas, Calendário, Orquestrador e Sintetizador, Verificador, memória. Depois, comparação.
7. Otimização: modelos por função, contexto, cache e modelos locais.
8. Ciclo com o CA no terminal: Encaminhador, atendimento e Curador.
9. Decisão sobre o WhatsApp, com base nas metas acima.

## Como rodar

```
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # preencher Supabase, chave do LLM e modelo de embeddings
python supabase/apply_schema.py # cria as tabelas e as funções de busca
python -m app.ingestao.pipeline # ingere a base (precisa dos dados do curso)
python -m app.canais.cli        # conversa pelo terminal
pytest
```
