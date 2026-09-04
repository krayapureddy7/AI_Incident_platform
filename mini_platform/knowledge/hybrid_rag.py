"""
Hybrid RAG pipeline combining Lexical Search (BM25) and Dense Semantic Vector Search
with metadata filtering and Reciprocal Rank Fusion (RRF).
"""

import re
import math
import hashlib
from collections import Counter, defaultdict
from typing import List, Dict, Any, Optional, Set
from .corpus import KNOWLEDGE_DOCUMENTS

#: Version of the retrieval pipeline (BM25 + dense projection + RRF).
__version__ = "1.2.0"


STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "with",
    "is", "are", "was", "were", "it", "this", "that", "be", "by", "as", "from",
    "if", "do", "not", "have", "has", "had", "can", "will", "would", "should"
}


def tokenize(text: str) -> List[str]:
    """Tokenize and normalize text."""
    words = re.findall(r'[a-zA-Z0-9_\-]+', text.lower())
    return [w for w in words if len(w) > 1 and w not in STOPWORDS]


class BM25Ranker:
    """Okapi BM25 implementation for lexical relevance search."""

    def __init__(self, corpus: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_lengths: Dict[str, int] = {}
        self.doc_term_frequencies: Dict[str, Counter] = {}
        self.doc_frequencies: Counter = Counter()
        self.avg_doc_length: float = 0.0
        self._build_index()

    def _build_index(self):
        total_length = 0
        for doc in self.corpus:
            doc_id = doc["id"]
            text = f"{doc['title']} {doc['content']} {' '.join(doc.get('tags', []))}"
            tokens = tokenize(text)
            self.doc_lengths[doc_id] = len(tokens)
            total_length += len(tokens)
            tf = Counter(tokens)
            self.doc_term_frequencies[doc_id] = tf
            for token in tf.keys():
                self.doc_frequencies[token] += 1

        self.avg_doc_length = total_length / max(1, len(self.corpus))

    def score(self, query_tokens: List[str], doc_id: str) -> float:
        score = 0.0
        doc_len = self.doc_lengths.get(doc_id, 0)
        tf = self.doc_term_frequencies.get(doc_id, Counter())
        N = len(self.corpus)

        for token in query_tokens:
            if token not in tf:
                continue
            df = self.doc_frequencies.get(token, 0)
            # Standard Robertson-Spärck Jones IDF
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
            freq = tf[token]
            num = freq * (self.k1 + 1.0)
            den = freq + self.k1 * (1.0 - self.b + self.b * (doc_len / max(1.0, self.avg_doc_length)))
            score += idf * (num / den)

        return score

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = []
        for doc in self.corpus:
            doc_id = doc["id"]
            s = self.score(tokens, doc_id)
            scores.append((doc, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [{"doc": doc, "score": score, "rank": idx + 1} for idx, (doc, score) in enumerate(scores[:top_k])]


class DenseSemanticRanker:
    """
    Deterministic dense semantic embedding ranker using feature hashing and
    cosine similarity over normalized semantic projections.
    """

    def __init__(self, corpus: List[Dict[str, Any]], dim: int = 256):
        self.dim = dim
        self.corpus = corpus
        self.embeddings: Dict[str, List[float]] = {}
        self.doc_frequencies: Counter = Counter()
        self._calc_doc_frequencies()
        self._build_index()

    def _calc_doc_frequencies(self):
        for doc in self.corpus:
            text = f"{doc['title']} {doc['content']} {' '.join(doc.get('tags', []))}"
            tokens = set(tokenize(text))
            for t in tokens:
                self.doc_frequencies[t] += 1

    def _token_hash(self, token: str) -> int:
        digest = hashlib.md5(token.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % self.dim

    def _embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vec

        N = max(1, len(self.corpus))
        for idx, token in enumerate(tokens):
            df = self.doc_frequencies.get(token, 1)
            idf = math.log((N + 1.0) / (df + 0.5)) + 1.0
            h = self._token_hash(token)
            vec[h] += 1.0 * idf
            if idx > 0:
                bigram = f"{tokens[idx-1]}_{token}"
                h2 = self._token_hash(bigram)
                vec[h2] += 1.5 * idf

        # L2 Normalization
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        return max(0.0, min(1.0, dot))

    def _build_index(self):
        for doc in self.corpus:
            text = f"{doc['title']} {doc['content']} {' '.join(doc.get('tags', []))}"
            self.embeddings[doc["id"]] = self._embed(text)

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        query_vec = self._embed(query)
        scores = []
        for doc in self.corpus:
            doc_id = doc["id"]
            emb = self.embeddings.get(doc_id, [0.0] * self.dim)
            sim = self._cosine_similarity(query_vec, emb)
            scores.append((doc, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return [{"doc": doc, "score": score, "rank": idx + 1} for idx, (doc, score) in enumerate(scores[:top_k])]


class HybridRAG:
    """
    Hybrid RAG coordinator fusing Lexical (BM25) and Dense embeddings
    with metadata filtering and citation generation.
    """

    def __init__(self, corpus: Optional[List[Dict[str, Any]]] = None):
        self.corpus = corpus or KNOWLEDGE_DOCUMENTS
        self.bm25 = BM25Ranker(self.corpus)
        self.dense = DenseSemanticRanker(self.corpus)

    def search(
        self,
        query: str,
        service_filter: Optional[str] = None,
        env_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        top_k: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Execute hybrid search using Reciprocal Rank Fusion (RRF) with metadata constraints.
        """
        bm25_results = self.bm25.search(query, top_k=10)
        dense_results = self.dense.search(query, top_k=10)

        # RRF Scoring (k=60)
        rrf_scores: Dict[str, float] = defaultdict(float)
        docs_by_id: Dict[str, Dict[str, Any]] = {}
        bm25_scores: Dict[str, float] = {}
        dense_scores: Dict[str, float] = {}

        for item in bm25_results:
            doc = item["doc"]
            doc_id = doc["id"]
            docs_by_id[doc_id] = doc
            bm25_scores[doc_id] = item["score"]
            rrf_scores[doc_id] += 1.0 / (60 + item["rank"])

        for item in dense_results:
            doc = item["doc"]
            doc_id = doc["id"]
            docs_by_id[doc_id] = doc
            dense_scores[doc_id] = item["score"]
            rrf_scores[doc_id] += 1.0 / (60 + item["rank"])

        # Filter and construct formatted results
        filtered_results = []
        for doc_id, rrf in rrf_scores.items():
            doc = docs_by_id[doc_id]

            # Metadata filtering
            if service_filter and doc.get("service") != service_filter:
                continue
            if env_filter and doc.get("env") != env_filter:
                continue
            if type_filter and doc.get("type") != type_filter:
                continue

            # Extract highest relevance excerpt / snippet
            content = doc["content"]
            snippet = content[:320] + ("..." if len(content) > 320 else "")

            filtered_results.append({
                "doc_id": doc_id,
                "title": doc["title"],
                "service": doc.get("service"),
                "env": doc.get("env"),
                "type": doc.get("type"),
                "version": doc.get("version"),
                "rrf_score": round(rrf, 4),
                "bm25_score": round(bm25_scores.get(doc_id, 0.0), 3),
                "dense_score": round(dense_scores.get(doc_id, 0.0), 3),
                "citation_snippet": snippet,
                "full_content": content
            })

        filtered_results.sort(key=lambda x: x["rrf_score"], reverse=True)
        return filtered_results[:top_k]


# Global Hybrid RAG instance
GLOBAL_HYBRID_RAG = HybridRAG()
