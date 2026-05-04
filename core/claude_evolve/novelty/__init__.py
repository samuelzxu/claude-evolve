"""Novelty detection: AST-based (default) or neural-embedding-based."""

from claude_evolve.novelty.embeddings import CodeEmbedder
from claude_evolve.novelty.embedding_judge import EmbeddingJudge
from claude_evolve.novelty.judge import NoveltyJudge

__all__ = ["CodeEmbedder", "EmbeddingJudge", "NoveltyJudge", "make_judge"]


def make_judge(novelty_config):
    """Build the judge implementation selected by ``novelty_config.method``.

    Returns an object exposing the same interface as ``NoveltyJudge``
    (``embed_code``, ``should_check_novelty``, ``assess_novelty``,
    ``assess_with_rejection_sampling``).
    """
    method = getattr(novelty_config, "method", "ast")
    if method == "embedding":
        return EmbeddingJudge(
            similarity_threshold=novelty_config.similarity_threshold,
            max_attempts=novelty_config.max_attempts,
            model_name=novelty_config.embedding_model,
            device=novelty_config.embedding_device,
            max_tokens=novelty_config.embedding_max_tokens,
        )
    if method == "ast":
        return NoveltyJudge(
            similarity_threshold=novelty_config.similarity_threshold,
            max_attempts=novelty_config.max_attempts,
        )
    raise ValueError(
        f"Unknown novelty.method={method!r}. Expected 'ast' or 'embedding'."
    )
