"""
openHome RAG Memory
───────────────────
Gives the AI long-term knowledge about the household:
  - User preferences ("I like the living room at 70°F")
  - Routines ("we go to bed around 11pm")
  - People ("my dog's name is Max, ignore motion below 1ft")
  - Facts the AI should remember across reboots

Uses Ollama's embedding model (nomic-embed-text) for semantic search.
Falls back to keyword matching if embeddings unavailable.

Stored via storage.py so it persists. Each entry:
  { "id", "text", "category", "user_id", "embedding", "created" }
"""

import json
import math
import httpx
from datetime import datetime

import storage

OLLAMA_BASE   = "http://localhost:11434"
EMBED_MODEL   = "nomic-embed-text"   # tiny, fast, runs on Pi
EMBED_URL     = f"{OLLAMA_BASE}/api/embeddings"

# How many memories to retrieve for a given query
TOP_K = 5
# Minimum similarity to include a memory (0-1)
SIM_THRESHOLD = 0.4


# ── ADD MEMORY ────────────────────────────────────────────
async def add_memory(text: str, category: str = "general", user_id: str = None) -> dict:
    """Store a new piece of knowledge. Embeds it for later retrieval."""
    embedding = await _embed(text)

    entry = {
        "id":        f"mem_{int(datetime.now().timestamp() * 1000)}",
        "text":      text,
        "category":  category,   # preference | routine | person | fact | general
        "user_id":   user_id,
        "embedding": embedding,
        "created":   datetime.now().isoformat()
    }

    storage.append("ai_memory", entry)
    print(f"[RAG] Stored memory [{category}]: {text[:60]}")
    return {"id": entry["id"], "text": text, "category": category}


# ── RETRIEVE ──────────────────────────────────────────────
async def retrieve(query: str, top_k: int = TOP_K, user_id: str = None) -> list[dict]:
    """Find the most relevant memories for a query."""
    memories = storage.get("ai_memory")
    if not memories:
        return []

    # Filter by user if specified (None = global memories + this user's)
    if user_id:
        memories = [m for m in memories if m.get("user_id") in (None, user_id)]

    query_emb = await _embed(query)

    if query_emb:
        # Semantic search via cosine similarity
        scored = []
        for m in memories:
            emb = m.get("embedding")
            if emb:
                sim = _cosine(query_emb, emb)
                if sim >= SIM_THRESHOLD:
                    scored.append((sim, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"text": m["text"], "category": m["category"], "score": round(s, 3)}
                for s, m in scored[:top_k]]
    else:
        # Fallback: keyword matching
        q_words = set(query.lower().split())
        scored = []
        for m in memories:
            m_words = set(m["text"].lower().split())
            overlap = len(q_words & m_words)
            if overlap > 0:
                scored.append((overlap, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"text": m["text"], "category": m["category"]}
                for _, m in scored[:top_k]]


async def get_context_block(query: str, user_id: str = None) -> str:
    """Returns a formatted string of relevant memories to inject into a prompt."""
    memories = await retrieve(query, user_id=user_id)
    if not memories:
        return ""
    lines = [f"- {m['text']}" for m in memories]
    return "KNOWN HOUSEHOLD CONTEXT:\n" + "\n".join(lines)


# ── MANAGEMENT ────────────────────────────────────────────
def list_memories(category: str = None, user_id: str = None) -> list[dict]:
    """List stored memories (without embeddings, for display)."""
    memories = storage.get("ai_memory")
    out = []
    for m in memories:
        if category and m.get("category") != category:
            continue
        if user_id and m.get("user_id") not in (None, user_id):
            continue
        out.append({
            "id":       m["id"],
            "text":     m["text"],
            "category": m["category"],
            "user_id":  m.get("user_id"),
            "created":  m["created"]
        })
    return out


def delete_memory(memory_id: str) -> bool:
    memories = storage.get("ai_memory")
    filtered = [m for m in memories if m["id"] != memory_id]
    if len(filtered) != len(memories):
        storage.set_collection("ai_memory", filtered)
        return True
    return False


# ── EMBEDDINGS ────────────────────────────────────────────
async def _embed(text: str):
    """Get embedding vector from Ollama. Returns None on failure (triggers keyword fallback)."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text})
            resp.raise_for_status()
            return resp.json().get("embedding")
    except Exception as e:
        print(f"[RAG] Embedding unavailable ({type(e).__name__}) — using keyword fallback")
        return None


def _cosine(a: list, b: list) -> float:
    """Cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot  = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
