"""Neural-embedding-based novelty judge.

Drop-in alternative to the AST-based NoveltyJudge. Loads a HuggingFace
model lazily on first use (sentence-transformers when supported, raw
transformers as fallback for models like CodeT5+ that emit pre-pooled
outputs), embeds code as a dense vector, and rejects proposals whose
cosine similarity to existing island programs exceeds the threshold.

Embedding storage format:
    list[float]  -- the normalized vector (so dot product == cosine).

Incompatible with embeddings stored by the AST judge (those are dicts
keyed by 'tfidf'/'minhash'); mixing methods within a single run causes
prior embeddings to be ignored during similarity comparison.
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from claude_evolve.database.models import Program

logger = logging.getLogger(__name__)


class EmbeddingJudge:
    """Novelty judge backed by a neural code-embedding model.

    Uses sentence-transformers when the model conforms to that interface;
    falls back to raw HuggingFace transformers (with mean-pool of last
    hidden state, or the model's pre-pooled output) for code-specific
    models like CodeT5+ that don't fit sentence-transformers' assumptions.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.80,
        max_attempts: int = 3,
        model_name: str = "Salesforce/codet5p-110m-embedding",
        device: Optional[str] = None,
        max_tokens: int = 8192,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_attempts = max_attempts
        self.model_name = model_name
        self.device = device
        self.max_tokens = max_tokens
        self._backend: Optional[str] = None  # "st" or "hf"
        self._st_model = None
        self._hf_tokenizer = None
        self._hf_model = None

    def _ensure_model(self):
        if self._backend is not None:
            return
        # Try sentence-transformers first.
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "EmbeddingJudge requires sentence-transformers + transformers + torch. "
                "Install with: pip install 'claude-evolve[embedding]'"
            ) from exc

        st_kwargs = {"trust_remote_code": True}
        if self.device:
            st_kwargs["device"] = self.device

        try:
            logger.info(
                "EmbeddingJudge: loading %s via sentence-transformers", self.model_name
            )
            model = SentenceTransformer(self.model_name, **st_kwargs)
            # Probe encode -- some models load fine but break at encode time
            # (e.g., CodeT5+ outputs a 2-D tensor sentence-transformers can't pool).
            _ = model.encode(
                "def _probe(): pass",
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            if self.max_tokens:
                try:
                    model.max_seq_length = self.max_tokens
                except Exception:
                    pass
            self._st_model = model
            self._backend = "st"
            return
        except Exception as exc:
            logger.info(
                "EmbeddingJudge: sentence-transformers path failed (%s); "
                "falling back to raw transformers",
                type(exc).__name__,
            )

        # Fall back to raw transformers.
        try:
            from transformers import AutoModel, AutoTokenizer  # type: ignore
            import torch  # type: ignore  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "EmbeddingJudge fallback requires transformers + torch. "
                "Install with: pip install 'claude-evolve[embedding]'"
            ) from exc

        logger.info(
            "EmbeddingJudge: loading %s via raw transformers", self.model_name
        )
        self._hf_tokenizer = AutoTokenizer.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        self._hf_model = AutoModel.from_pretrained(
            self.model_name, trust_remote_code=True
        )
        self._hf_model.eval()
        if self.device:
            self._hf_model = self._hf_model.to(self.device)
        self._backend = "hf"

    def embed_code(self, code: str) -> list[float]:
        """Encode a code string into a normalized embedding vector."""
        self._ensure_model()
        if self._backend == "st":
            vec = self._st_model.encode(
                code,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [float(x) for x in vec.tolist()]

        # Raw transformers path.
        import torch
        max_len = min(self.max_tokens, getattr(self._hf_tokenizer, "model_max_length", 512))
        if max_len <= 0 or max_len > 100000:
            max_len = 512
        inputs = self._hf_tokenizer(
            code,
            return_tensors="pt",
            truncation=True,
            max_length=max_len,
        )
        if self.device:
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with torch.no_grad():
            out = self._hf_model(**inputs)
        # Models with built-in pooling (CodeT5+) return a 2-D tensor as out[0].
        # Fall through to mean-pool last_hidden_state if shape suggests sequence.
        candidate = out[0] if isinstance(out, tuple) else out.last_hidden_state if hasattr(out, "last_hidden_state") else out
        if hasattr(candidate, "shape") and candidate.dim() == 2 and candidate.shape[0] == inputs["input_ids"].shape[0]:
            vec = candidate
        else:
            attn = inputs.get("attention_mask")
            if attn is not None:
                mask = attn.unsqueeze(-1).float()
                summed = (candidate * mask).sum(dim=1)
                counts = mask.sum(dim=1).clamp(min=1)
                vec = summed / counts
            else:
                vec = candidate.mean(dim=1)
        # Normalize so dot product == cosine similarity.
        norm = vec.norm(dim=-1, keepdim=True).clamp(min=1e-12)
        vec = vec / norm
        return [float(x) for x in vec.squeeze(0).cpu().tolist()]

    def should_check_novelty(
        self,
        code_embedding: Optional[list[float]],
        generation: int,
        parent_program: Optional[Program],
    ) -> bool:
        if not code_embedding or generation == 0 or not parent_program:
            return False
        return True

    def assess_novelty(
        self,
        code: str,
        code_embedding: list[float],
        island_idx: int,
        db,
    ) -> tuple[bool, dict]:
        metadata = {
            "max_similarity": 0.0,
            "num_compared": 0,
            "threshold": self.similarity_threshold,
            "method": "embedding",
        }

        island_programs = db.get_island_programs(island_idx)
        comparable = [
            p
            for p in island_programs
            if isinstance(p.embedding, list) and p.embedding
        ]

        if not comparable:
            logger.info(
                "NOVELTY[embedding]: Accepting -- no comparable embeddings in island"
            )
            return True, metadata

        metadata["num_compared"] = len(comparable)

        similarities = [_cosine(code_embedding, p.embedding) for p in comparable]
        max_sim = max(similarities) if similarities else 0.0
        metadata["max_similarity"] = max_sim

        top_5 = sorted(similarities, reverse=True)[:5]
        logger.info(
            "NOVELTY[embedding]: Top-5 similarities: %s",
            [f"{s:.3f}" for s in top_5],
        )

        if max_sim <= self.similarity_threshold:
            logger.info(
                "NOVELTY[embedding]: Accepting -- max similarity %.3f <= threshold %.3f",
                max_sim,
                self.similarity_threshold,
            )
            return True, metadata

        logger.info(
            "NOVELTY[embedding]: Rejecting -- max similarity %.3f > threshold %.3f",
            max_sim,
            self.similarity_threshold,
        )
        return False, metadata

    def assess_with_rejection_sampling(
        self,
        code: str,
        island_idx: int,
        db,
    ) -> tuple[bool, dict]:
        embedding = self.embed_code(code)
        return self.assess_novelty(code, embedding, island_idx, db)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))
