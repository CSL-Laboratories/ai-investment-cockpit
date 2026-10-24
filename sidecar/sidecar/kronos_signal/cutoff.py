"""Where Kronos's pretraining ends, and where this sidecar is allowed to score it.

The paper states: "The pre-training data for Kronos extends up to June 2024.
Consequently, our test period for all tasks begins in July 2024 to ensure a
strict temporal separation between training and evaluation." (arXiv 2508.02739,
appendix on experimental setup). This module follows that sentence.

A later repository comment (shiyu-coder/Kronos#117, 2025-09-27) splits the same
corpus more finely: training through February 2024 inclusive, validation from
March through May 2024, out-of-sample from June 2024. The NeurIPS 2025 slides
say train/validation through about June 2024 and test from July 2024 through
June 2025. July 2024 is the start that sits after every one of those windows.
Scored labels before that date are refused. Context bars may be older: they are
the history available on the decision date, not labels.
"""

from __future__ import annotations

from datetime import date

PRETRAINING_THROUGH = date(2024, 6, 30)
EVALUATION_START = date(2024, 7, 1)
PAPER_CITATION = "arXiv:2508.02739"
AUTHOR_COMMENT = "https://github.com/shiyu-coder/Kronos/issues/117"
