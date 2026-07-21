"""Golden-set evaluation for query-specific UGC retrieval."""

from .evaluate import evaluate_retrievers, load_golden_set

__all__ = ["evaluate_retrievers", "load_golden_set"]
