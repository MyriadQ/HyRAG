-- Adding vector extension into postgre
create extension if not exists vector;

create table if not exists baseline_chunks(
    chunk_id text primary key,
    file text not null,
    heading text not null,
    level integer not null,
    page_start integer not null,
    page_end integer not null,
    citation text not null,
    occurrences jsonb not null,
    tokens integer not null,
    content text not null,
    model text not null,
    embedding vector(1024) not null
);

-- deliberately no index on `embedding`.
--
-- at 369 rows postgres scans the column exactly, in about a millisecond, and returns the
-- true nearest neighbours. hnsw and ivfflat are *approximate*: they can drop the evidence
-- target out of the top-k and fail this node's own gate for a reason that has nothing to do
-- with the method being measured. adding one changes the recorded numbers — so it is a
-- decision, not an optimisation, and it belongs in nodes/baseline.md if it is ever taken.
--
-- if one is added it must match the operator the query uses (`<=>`), or postgres falls back
-- to a sequential scan without reporting an error:
--
--     create index on baseline_chunks using hnsw (embedding vector_cosine_ops);
--     set local hnsw.ef_search = 200;   -- wider candidate list, better recall