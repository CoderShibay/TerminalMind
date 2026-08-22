"""Generate sentence embeddings for semantic search. Cached in message_embeddings table."""
import numpy as np

MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE  = 128
MIN_LEN     = 15   # skip very short messages
MAX_CHARS   = 512  # truncate long messages before embedding

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_query(text: str) -> np.ndarray:
    """Embed a single search query. Returns L2-normalized float32 vector."""
    model = _get_model()
    vec = model.encode([text], convert_to_numpy=True, normalize_embeddings=True)[0]
    return vec.astype(np.float32)


def run(conn, verbose: bool = False) -> int:
    """Embed all messages that don't have embeddings yet. Returns count of new embeddings."""
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
        normalize_embeddings=True,  # dot product == cosine similarity
        show_progress_bar=verbose,
    )

    conn.executemany(
        "INSERT OR IGNORE INTO message_embeddings (message_id, embedding, model) VALUES (?,?,?)",
        [
            (ids[i], embeddings[i].astype(np.float32).tobytes(), MODEL_NAME)
            for i in range(len(ids))
        ]
    )
    conn.commit()
    return len(rows)


def load_matrix(conn) -> tuple[np.ndarray, list[int]] | tuple[None, None]:
    """Load all embeddings into memory as a matrix for fast similarity search.
    Returns (matrix, message_ids) or (None, None) if no embeddings exist."""
    rows = conn.execute(
        "SELECT message_id, embedding FROM message_embeddings"
    ).fetchall()

    if not rows:
        return None, None

    ids    = [r["message_id"] for r in rows]
    matrix = np.vstack([
        np.frombuffer(r["embedding"], dtype=np.float32) for r in rows
    ])
    return matrix, ids
