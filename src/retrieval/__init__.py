"""Query-time evidence retrieval with explicit, testable ranking semantics."""

from .ugc import ExplainableUGCRetriever, UGCRetrievalHit, expand_query

__all__ = ["ExplainableUGCRetriever", "UGCRetrievalHit", "expand_query"]
