"""Local code embedding via AST fingerprinting + MinHash.

Replaces ShinkaEvolve's OpenAI text-embedding-3-small with a zero-dependency
local approach. No external APIs, no model downloads.

Strategy:
1. Parse code AST (Python ast module)
2. Extract structural features (node-type bigrams)
3. Build TF-IDF-like vector from feature counts
4. Cosine similarity for comparison
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
from collections import Counter
from typing import Optional


def _tokenize_code(code: str) -> list[str]:
    """Tokenize code into meaningful tokens for non-AST fallback."""
    # Remove comments and strings, split on boundaries
    code = re.sub(r'#.*$', '', code, flags=re.MULTILINE)
    code = re.sub(r'""".*?"""', '""', code, flags=re.DOTALL)
    code = re.sub(r"'''.*?'''", "''", code, flags=re.DOTALL)
    code = re.sub(r'"[^"]*"', '""', code)
    code = re.sub(r"'[^']*'", "''", code)
    tokens = re.findall(r'[a-zA-Z_]\w*|[+\-*/=<>!&|^~%]+|[\[\](){}:;,.]', code)
    return tokens


def _extract_ast_features(code: str) -> list[str]:
    """Extract structural features from Python AST.

    Returns a list of feature strings representing the code's structure.
    Falls back to token-based features if AST parsing fails.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Fallback: token bigrams
        tokens = _tokenize_code(code)
        return [f"tok:{a}_{b}" for a, b in zip(tokens, tokens[1:])]

    features = []

    # Node-type bigrams (parent -> child relationships)
    for node in ast.walk(tree):
        node_type = type(node).__name__
        for child in ast.iter_child_nodes(node):
            child_type = type(child).__name__
            features.append(f"ast:{node_type}_{child_type}")

    # Function signatures
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            features.append(f"fn:{node.name}({','.join(args)})")
            # Return annotation
            if node.returns:
                features.append(f"ret:{node.name}_{ast.dump(node.returns)}")

    # Control flow patterns
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            features.append("flow:for")
        elif isinstance(node, ast.While):
            features.append("flow:while")
        elif isinstance(node, ast.If):
            features.append("flow:if")
        elif isinstance(node, ast.Try):
            features.append("flow:try")
        elif isinstance(node, ast.With):
            features.append("flow:with")
        elif isinstance(node, ast.ListComp):
            features.append("flow:listcomp")

    # Import patterns
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                features.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            features.append(f"from:{node.module}")

    return features


def _minhash_signature(features: list[str], num_hashes: int = 128) -> list[int]:
    """Compute MinHash signature for a set of features."""
    if not features:
        return [0] * num_hashes

    signature = [float('inf')] * num_hashes
    for feature in set(features):  # Deduplicate
        for i in range(num_hashes):
            # Different hash function per slot
            h = int(hashlib.md5(
                f"{i}:{feature}".encode()
            ).hexdigest()[:8], 16)
            if h < signature[i]:
                signature[i] = h

    return [int(s) if s != float('inf') else 0 for s in signature]


def _jaccard_from_minhash(sig1: list[int], sig2: list[int]) -> float:
    """Estimate Jaccard similarity from MinHash signatures."""
    if not sig1 or not sig2 or len(sig1) != len(sig2):
        return 0.0
    matches = sum(1 for a, b in zip(sig1, sig2) if a == b)
    return matches / len(sig1)


class CodeEmbedder:
    """Embeds code into a vector space using AST structural features.

    Uses a two-signal approach:
    1. TF-IDF vector from AST feature counts (for cosine similarity)
    2. MinHash signature (for fast Jaccard similarity)
    """

    def __init__(self, num_hashes: int = 128):
        self.num_hashes = num_hashes
        # Global document frequency (grows as we see more programs)
        self._doc_freq: Counter = Counter()
        self._num_docs: int = 0

    def embed(self, code: str) -> list[float]:
        """Compute embedding vector for code.

        Returns a normalized TF-IDF vector based on AST features.
        The vector dimensions correspond to feature types seen so far.
        """
        features = _extract_ast_features(code)
        if not features:
            return []

        # Update document frequency
        self._num_docs += 1
        unique_features = set(features)
        for f in unique_features:
            self._doc_freq[f] += 1

        # TF-IDF vector (as a sparse dict, then convert to list)
        tf = Counter(features)
        total = len(features)

        tfidf = {}
        for f, count in tf.items():
            tf_val = count / total
            # IDF with smoothing
            idf = math.log(1 + self._num_docs / (1 + self._doc_freq.get(f, 0)))
            tfidf[f] = tf_val * idf

        # Normalize
        norm = math.sqrt(sum(v * v for v in tfidf.values()))
        if norm > 0:
            tfidf = {k: v / norm for k, v in tfidf.items()}

        # Return as a serializable format: list of (feature, weight) pairs
        # sorted by feature name for consistent ordering
        return list(tfidf.values()) if tfidf else []

    def embed_for_similarity(self, code: str) -> dict:
        """Compute both TF-IDF and MinHash embeddings.

        Returns a dict with 'tfidf' (sparse dict) and 'minhash' (list of ints).
        This is what gets stored in the database.
        """
        features = _extract_ast_features(code)
        if not features:
            return {"tfidf": {}, "minhash": [0] * self.num_hashes}

        # TF-IDF as sparse dict
        tf = Counter(features)
        total = len(features)
        self._num_docs += 1

        tfidf = {}
        for f, count in tf.items():
            self._doc_freq[f] += 1
            tf_val = count / total
            idf = math.log(1 + self._num_docs / (1 + self._doc_freq.get(f, 0)))
            tfidf[f] = tf_val * idf

        # Normalize
        norm = math.sqrt(sum(v * v for v in tfidf.values()))
        if norm > 0:
            tfidf = {k: v / norm for k, v in tfidf.items()}

        # MinHash
        minhash = _minhash_signature(features, self.num_hashes)

        return {"tfidf": tfidf, "minhash": minhash}

    @staticmethod
    def similarity(emb1: dict, emb2: dict) -> float:
        """Compute similarity between two embeddings.

        Uses a weighted combination:
        - 60% cosine similarity on TF-IDF vectors
        - 40% Jaccard similarity from MinHash

        Returns a float in [0, 1].
        """
        tfidf1 = emb1.get("tfidf", {})
        tfidf2 = emb2.get("tfidf", {})
        minhash1 = emb1.get("minhash", [])
        minhash2 = emb2.get("minhash", [])

        # Cosine similarity on TF-IDF
        if tfidf1 and tfidf2:
            common_keys = set(tfidf1.keys()) & set(tfidf2.keys())
            dot = sum(tfidf1[k] * tfidf2[k] for k in common_keys)
            norm1 = math.sqrt(sum(v * v for v in tfidf1.values()))
            norm2 = math.sqrt(sum(v * v for v in tfidf2.values()))
            cosine = dot / (norm1 * norm2) if norm1 > 0 and norm2 > 0 else 0.0
        else:
            cosine = 0.0

        # Jaccard from MinHash
        jaccard = _jaccard_from_minhash(minhash1, minhash2)

        # Weighted combination
        return 0.6 * cosine + 0.4 * jaccard

    @staticmethod
    def quick_similarity(code1: str, code2: str) -> float:
        """Quick similarity check between two code strings.

        Uses MinHash only for speed. Good for rejection sampling
        where we need fast approximate comparisons.
        """
        features1 = _extract_ast_features(code1)
        features2 = _extract_ast_features(code2)
        sig1 = _minhash_signature(features1)
        sig2 = _minhash_signature(features2)
        return _jaccard_from_minhash(sig1, sig2)
