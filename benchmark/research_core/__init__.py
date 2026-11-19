"""Research Core benchmark harness.

Stage-agnostic: a case names a stage, an adapter answers it, the runner records
what happened. See ``benchmark/research-core/`` for the documents.
"""

from .model import STATUSES, STAGES, CaseError, load_cases, load_dataset_dir

__all__ = ["STATUSES", "STAGES", "CaseError", "load_cases", "load_dataset_dir"]
