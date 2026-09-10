"""
Tests for Hybrid RAG (BM25 + Dense Semantic Vector Search) and Knowledge Graph Traversal.
"""
import unittest
from mini_platform.knowledge.hybrid_rag import GLOBAL_HYBRID_RAG, BM25Ranker, DenseSemanticRanker
from mini_platform.knowledge.knowledge_graph import GLOBAL_KNOWLEDGE_GRAPH
from mini_platform.knowledge.corpus import KNOWLEDGE_DOCUMENTS


class TestKnowledgeAndRAG(unittest.TestCase):
    def test_bm25_lexical_search(self):
        bm25 = BM25Ranker(KNOWLEDGE_DOCUMENTS)
        results = bm25.search("PaymentService memory leak OutOfMemoryError heap", top_k=3)
        self.assertGreater(len(results), 0)
        top_doc = results[0]["doc"]
        self.assertEqual(top_doc["service"], "payment-service")

    def test_dense_semantic_search(self):
        dense = DenseSemanticRanker(KNOWLEDGE_DOCUMENTS)
        results = dense.search("redis connection pool exhaustion timeout", top_k=3)
        self.assertGreater(len(results), 0)
        top_doc = results[0]["doc"]
        self.assertEqual(top_doc["service"], "auth-service")

    def test_hybrid_rag_fusion_and_citation(self):
        rag = GLOBAL_HYBRID_RAG
        hits = rag.search(
            query="payment-service memory saturation rolling restart",
            service_filter="payment-service",
            top_k=2
        )
        self.assertGreater(len(hits), 0)
        self.assertIn("rrf_score", hits[0])
        self.assertIn("bm25_score", hits[0])
        self.assertIn("dense_score", hits[0])
        self.assertIn("citation_snippet", hits[0])
        self.assertEqual(hits[0]["doc_id"], "DOC-RB-PAY-001")

    def test_hybrid_rag_version_filter(self):
        rag = GLOBAL_HYBRID_RAG
        hits = rag.search(
            query="payment-service memory saturation rolling restart",
            service_filter="payment-service",
            version_filter="1.0.0",
            top_k=5,
        )
        # Only the postmortem is versioned 1.0.0; the runbook and architecture
        # spec are both 2.4.0, so they must be excluded by the filter.
        self.assertTrue(hits)
        self.assertTrue(all(h["version"] == "1.0.0" for h in hits))
        self.assertIn("DOC-PM-2025-11", [h["doc_id"] for h in hits])
        self.assertNotIn("DOC-RB-PAY-001", [h["doc_id"] for h in hits])

    def test_knowledge_graph_blast_radius_traversal(self):
        kg = GLOBAL_KNOWLEDGE_GRAPH
        blast = kg.calculate_blast_radius("payment-service")
        self.assertTrue(blast["found"])
        self.assertEqual(blast["service_tier"], "tier-1")
        self.assertIn("order-service", blast["direct_dependents"])
        self.assertIn("mobile-api-gateway", blast["direct_dependents"])
        # Transitive dependents of order-service
        self.assertIn("web-frontend", blast["transitive_dependents"])
        self.assertEqual(blast["total_affected_services"], 4)

    def test_knowledge_graph_tier_0_impact(self):
        kg = GLOBAL_KNOWLEDGE_GRAPH
        blast = kg.calculate_blast_radius("auth-service")
        self.assertTrue(blast["tier_0_impacted"])
        self.assertEqual(blast["service_tier"], "tier-0")
        self.assertEqual(blast["risk_rating"], "CRITICAL")


if __name__ == "__main__":
    unittest.main()
