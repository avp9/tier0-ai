from __future__ import annotations

import logging
from collections.abc import Sequence

from tier0.intent.types import (
    ClassifierError,
    IntentClassifier,
    IntentResult,
    IntentUnavailable,
    Turn,
)

log = logging.getLogger(__name__)


class ResilientIntentClassifier:
    """Try each classifier in order; the first valid result wins."""

    name = "resilient"

    def __init__(self, classifiers: Sequence[IntentClassifier]) -> None:
        if not classifiers:
            raise ValueError("at least one classifier is required")
        self._classifiers = list(classifiers)

    def classify(self, message: str, history: list[Turn]) -> IntentResult:
        failures: list[str] = []
        for classifier in self._classifiers:
            try:
                return classifier.classify(message, history)
            except ClassifierError as error:
                # Never log the message itself: it is employee content.
                log.warning("intent classifier %s failed: %s", classifier.name, error)
                failures.append(f"{classifier.name}: {error}")
        raise IntentUnavailable("; ".join(failures))
