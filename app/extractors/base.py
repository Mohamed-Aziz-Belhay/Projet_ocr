"""
app/extractors/base.py
Abstract base for specialized extractors (Phase-B fallback layer).

Architecture decision (honest):
  Generic template extractor (generic_template_extractor.py) handles ~90% of cases.
  Specialized extractors exist ONLY as fallback for edge cases that YAML templates
  cannot handle — e.g. complex multi-page logic, cross-field calculations,
  or highly irregular legacy layouts.
"""
from __future__ import annotations
import abc
from typing import Any, Dict, List, Optional


class FieldOutput:
    """Normalized output from any extractor."""
    __slots__ = ("name", "value", "confidence", "validated", "raw_text", "error")

    def __init__(
        self,
        name: str,
        value: Optional[Any],
        confidence: float = 0.0,
        validated: bool = False,
        raw_text: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.name = name
        self.value = value
        self.confidence = confidence
        self.validated = validated
        self.raw_text = raw_text
        self.error = error

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "confidence": self.confidence,
            "validated": self.validated,
            "raw_text": self.raw_text,
            "error": self.error,
        }


class BaseExtractor(abc.ABC):
    """
    All specialized extractors inherit from this class.
    They accept raw OCR text and return a list of FieldOutput objects.
    """

    # Subclasses declare which doc_family + variant_id they handle
    doc_family: str = "generic"
    variant_id: str = "generic"

    @abc.abstractmethod
    def extract(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[FieldOutput]:
        """
        Extract fields from OCR text.
        metadata: optional extra context (page count, image dims, etc.)
        """

    def can_handle(self, doc_family: str, variant_id: Optional[str] = None) -> bool:
        """True if this extractor handles the given family/variant."""
        if self.doc_family != doc_family:
            return False
        if variant_id and self.variant_id != "generic" and self.variant_id != variant_id:
            return False
        return True

    def _make_field(
        self,
        name: str,
        value: Optional[Any],
        confidence: float,
        raw_text: Optional[str] = None,
        validated: bool = True,
        error: Optional[str] = None,
    ) -> FieldOutput:
        return FieldOutput(
            name=name,
            value=value,
            confidence=confidence,
            validated=validated,
            raw_text=raw_text,
            error=error,
        )
