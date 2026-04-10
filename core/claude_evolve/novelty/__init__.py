"""Novelty detection via local AST-based code embeddings."""

from claude_evolve.novelty.embeddings import CodeEmbedder
from claude_evolve.novelty.judge import NoveltyJudge

__all__ = ["CodeEmbedder", "NoveltyJudge"]
