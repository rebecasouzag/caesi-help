-- Schema do caesi-help. Pode ser reaplicado sem perder dados.
-- A dimensão de vector(...) deve bater com EMBEDDING_DIM.

create extension if not exists vector;

create table if not exists documents (
  id text primary key,            -- nome do arquivo
  title text not null,
  status text not null check (status in ('vigente','revogada','desatualizado')),
  topics text[] not null default '{}',
  updated_at date
);

create table if not exists chunks (
  id bigserial primary key,
  document_id text references documents(id) on delete cascade,
  content text not null,
  locator text,                   -- ex.: "Art. 270" ou "p. 12"
  embedding vector(384)           -- = EMBEDDING_DIM
);

create table if not exists faq (
  id text primary key,
  category text not null,
  question text not null,
  answer text not null,
  sources jsonb not null default '[]',
  curriculum text not null default 'todos',
  status text not null default 'rascunho',
  origin text not null default 'yaml' check (origin in ('yaml','escalonamento')),  -- a ingestão só altera 'yaml'
  embedding vector(384)          -- embedding da pergunta canônica
);

create table if not exists faq_variations (
  id bigserial primary key,
  faq_id text references faq(id) on delete cascade,
  text text not null,
  embedding vector(384)
);

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  channel text not null,          -- cli | web | telegram | whatsapp
  user_hash text,                 -- hash do identificador, nunca o número em texto
  question text not null,
  answer text,
  route text,                     -- faq | docs | eureca | contatos | escalated | out_of_scope
  confidence real,
  sources jsonb,
  created_at timestamptz default now()
);

create table if not exists escalations (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid references conversations(id),
  status text not null default 'aberta',  -- aberta | respondida | virou_faq
  human_answer text,
  answered_by text,
  created_at timestamptz default now()
);

-- Texto revisado de cada currículo nas entradas com curriculum = 'depende'.
alter table faq add column if not exists answer_by_curriculum jsonb not null default '{}';

-- Procedência da resposta e validade: entradas vencidas deixam de ser respondidas.
alter table faq add column if not exists provenance text not null default 'oficial' check (provenance in ('oficial','ca','comunidade'));
alter table faq add column if not exists valid_until date;

-- Sem políticas públicas: só o backend, com a chave service_role, acessa as tabelas.
alter table documents enable row level security;
alter table chunks enable row level security;
alter table faq enable row level security;
alter table faq_variations enable row level security;
alter table conversations enable row level security;
alter table escalations enable row level security;

-- Compara a pergunta com as perguntas canônicas e as variações; devolve o melhor resultado de cada entrada.
drop function if exists match_faq(vector, int);
create function match_faq(query_embedding vector, k int default 5)
returns table (
  id text,
  category text,
  question text,
  answer text,
  answer_by_curriculum jsonb,
  sources jsonb,
  curriculum text,
  status text,
  provenance text,
  valid_until date,
  matched_text text,
  similarity double precision
)
language sql stable
as $$
  with candidates as (
    select f.id as faq_id, f.question as matched_text, 1 - (f.embedding <=> query_embedding) as similarity
    from faq f
    where f.embedding is not null
    union all
    select v.faq_id, v.text, 1 - (v.embedding <=> query_embedding)
    from faq_variations v
    where v.embedding is not null
  ),
  best as (
    select distinct on (c.faq_id) c.faq_id, c.matched_text, c.similarity
    from candidates c
    order by c.faq_id, c.similarity desc
  )
  select f.id, f.category, f.question, f.answer, f.answer_by_curriculum, f.sources, f.curriculum, f.status, f.provenance, f.valid_until, b.matched_text, b.similarity
  from best b
  join faq f on f.id = b.faq_id
  order by b.similarity desc
  limit k;
$$;

-- Documentos revogados nunca são devolvidos; status volta para o agente aplicar a regra de 'desatualizado'.
create or replace function match_chunks(query_embedding vector, k int default 5, topics text[] default null)
returns table (
  id bigint,
  document_id text,
  title text,
  status text,
  content text,
  locator text,
  similarity double precision
)
language sql stable
as $$
  select c.id, c.document_id, d.title, d.status, c.content, c.locator,
         1 - (c.embedding <=> query_embedding) as similarity
  from chunks c
  join documents d on d.id = c.document_id
  where d.status <> 'revogada'
    and c.embedding is not null
    and (match_chunks.topics is null or d.topics && match_chunks.topics)
  order by c.embedding <=> query_embedding
  limit k;
$$;

-- Busca textual: search_text vem sem acentos (normalizado na ingestão), porque os alunos escrevem "matricula".
alter table chunks add column if not exists search_text text;
alter table chunks add column if not exists fts tsvector
  generated always as (to_tsvector('portuguese', coalesce(search_text, ''))) stored;
create index if not exists chunks_fts_idx on chunks using gin (fts);

-- Busca híbrida: funde os rankings vetorial e textual por Reciprocal Rank Fusion, que só usa a posição em cada lista.
-- query_text também vem normalizado. Qualquer palavra da pergunta conta (OR), para não exigir todas no mesmo trecho.
drop function if exists match_chunks_hybrid(vector, text, int, int, int, text[]);
create function match_chunks_hybrid(
  query_embedding vector, query_text text, k int default 8, candidates int default 40, rrf_k int default 60, topics text[] default null
)
returns table (
  id bigint,
  document_id text,
  title text,
  status text,
  content text,
  locator text,
  similarity double precision,
  rrf_score double precision
)
language sql stable
as $$
  with allowed as (
    select c.id, c.embedding, c.fts
    from chunks c
    join documents d on d.id = c.document_id
    where d.status <> 'revogada'
      and c.embedding is not null
      and (match_chunks_hybrid.topics is null or d.topics && match_chunks_hybrid.topics)
  ),
  query as (
    select replace(plainto_tsquery('portuguese', query_text)::text, '&', '|')::tsquery as tsq
  ),
  vector_ranked as (
    select a.id, row_number() over (order by a.embedding <=> query_embedding) as position
    from allowed a
    order by a.embedding <=> query_embedding
    limit candidates
  ),
  text_ranked as (
    select a.id, row_number() over (order by ts_rank(a.fts, q.tsq, 1) desc, a.id) as position
    from allowed a, query q
    where a.fts @@ q.tsq
    order by position
    limit candidates
  ),
  fused as (
    select ranked.id, sum(1.0 / (rrf_k + ranked.position)) as score
    from (select * from vector_ranked union all select * from text_ranked) ranked
    group by ranked.id
  )
  select c.id, c.document_id, d.title, d.status, c.content, c.locator,
         1 - (c.embedding <=> query_embedding) as similarity, f.score::double precision as rrf_score
  from fused f
  join chunks c on c.id = f.id
  join documents d on d.id = c.document_id
  order by f.score desc, c.id
  limit k;
$$;

