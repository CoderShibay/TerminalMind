"""Generate sentence embeddings for semantic search. Cached in message_embeddings table."""

MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE  = 128
MIN_LEN     = 15   # skip very short messages
MAX_CHARS   = 512  # truncate long messages before embedding

_model      = None
_np         = None   # numpy — loaded lazily, may not be available
_available  = None   # None = not yet checked, True/False after first attempt


def _check_available() -> bool:
    """Return True if numpy + sentence-transformers are both importable."""
    global _available, _np
    if _available is not None:
        return _available
    try:
        import numpy as np
        import sentence_transformers  # noqa: F401
        _np = np
        _available = True
    except ImportError:
        _available = False
    return _available


def _get_model():
    global _model
    if not _check_available():
        return None
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_query(text: str):
    """Embed a single search query. Returns L2-normalized float32 ndarray, or None."""
    model = _get_model()
    if model is None:
        return None
    vec = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
    return vec.astype(_np.float32)


def run(conn, verbose: bool = False) -> int:
    """Embed all messages that don't have embeddings yet. Returns count of new embeddings."""
    if not _check_available():
        if verbose:
            print("  Embeddings skipped — sentence-transformers not installed "
                  "(semantic search unavailable, keyword search still works)")
        return 0

    rows = conn.execute(
        """SELECT m.id, m.content_text
           FROM claude_messages m
           LEFT JOIN message_embeddings e ON e.message_id = m.id
           WHERE e.message_id IS NULL
             AND m.content_text IS NOT NULL
             AND length(m.content_text) >= ?
             AND m.role IN ('user', 'assistant')""",
        (MIN_LEN,)
    ).fetchall()

    if not rows:
        return 0

    model = _get_model()
    ids   = [r["id"] for r in rows]
    texts = [r["content_text"][:MAX_CHARS] for r in rows]

    if verbose:
        print(f"  Embedding {len(texts)} messages with {MODEL_NAME}…", flush=True)

    embeddings = model.encode(
        texts,
        batch_size=BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=verbose,
    )

    conn.executemany(
        "INSERT OR IGNORE INTO message_embeddings (message_id, embedding, model) VALUES (?,?,?)",
        [
            (ids[i], embeddings[i].astype(_np.float32).tobytes(), MODEL_NAME)
            for i in range(len(ids))
        ]
    )
    conn.commit()
    return len(rows)


def load_matrix(conn):
    """Load all embeddings into a matrix for similarity search.
    Returns (ndarray, list[int]) or (None, None) if unavailable."""
    if not _check_available():
        return None, None

    rows = conn.execute(
        "SELECT message_id, embedding FROM message_embeddings"
    ).fetchall()

    if not rows:
        return None, None

    ids    = [r["message_id"] for r in rows]
    matrix = _np.vstack([
        _np.frombuffer(r["embedding"], dtype=_np.float32) for r in rows
    ])
    return matrix, ids
