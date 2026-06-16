"""
app/services/engine_selector.py
Engine strategy builder for generic and CIN-specialized pipelines.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from app.core.settings import get_settings
from app.engines.engine_factory import BaseOCREngine, available_engines, get_engine

settings = get_settings()

_PREFERENCE_ORDER = ["paddle", "tesseract", "easyocr", "surya"]
_engines_loaded = False


@dataclass
class EnginePlan:
    primary: str
    secondary: Optional[str] = None
    experimental: List[str] = field(default_factory=list)
    numeric_fallback: Optional[str] = None
    strategy: str = "single_engine"
    compare_all_engines: bool = False
    notes: List[str] = field(default_factory=list)


def _ensure_engines() -> None:
    global _engines_loaded

    if _engines_loaded:
        return

    try:
        import app.engines.paddle_engine     # noqa: F401
        import app.engines.tesseract_engine  # noqa: F401
        import app.engines.easyocr_engine    # noqa: F401
        import app.engines.surya_engine      # noqa: F401
    finally:
        _engines_loaded = True


def get_engine_instance(name: str) -> BaseOCREngine:
    _ensure_engines()
    return get_engine(name)


def _is_enabled(name: str) -> bool:
    return {
        "paddle": settings.ENABLE_PADDLE,
        "tesseract": settings.ENABLE_TESSERACT,
        "easyocr": settings.ENABLE_EASYOCR,
        "surya": settings.ENABLE_SURYA_EXPERIMENTAL,
    }.get(name, False)


def select_engine(
    preferred: Optional[str] = None,
    fast_mode: bool = False,
    language: Optional[str] = None,
) -> BaseOCREngine:
    _ensure_engines()

    avail = available_engines()
    requested = preferred if preferred not in (None, "", "auto") else settings.DEFAULT_ENGINE

    candidates = []
    if requested:
        candidates.append(requested)

    candidates.extend(x for x in _PREFERENCE_ORDER if x not in candidates)

    if fast_mode and "easyocr" in candidates:
        candidates.remove("easyocr")
        candidates.append("easyocr")

    for name in candidates:
        if _is_enabled(name) and avail.get(name):
            return get_engine_instance(name)

    for name, ok in avail.items():
        if ok:
            return get_engine_instance(name)

    raise RuntimeError(f"Aucun moteur OCR disponible. Moteurs détectés: {avail}")


def build_engine_plan(
    template=None,
    forced_engine: Optional[str] = None,
    fast_mode: bool = False,
    language: Optional[str] = None,
    doc_family: Optional[str] = None,
) -> EnginePlan:
    _ensure_engines()

    avail = available_engines()
    tpl_engines = getattr(template, "engines", {}) or {}
    pipeline = getattr(template, "pipeline", "generic_template_v1") if template else "generic_template_v1"

    if forced_engine and forced_engine not in ("", "auto"):
        return EnginePlan(
            primary=forced_engine,
            strategy="forced_engine",
            compare_all_engines=False,
        )

    is_cin_pipeline = (
        pipeline.startswith("cin_specialized")
        or getattr(template, "id", None) == "cin_tn"
        or doc_family == "id_document"
    )

    if is_cin_pipeline:
        primary = tpl_engines.get("primary") or settings.CIN_PRIMARY_ENGINE
        secondary = tpl_engines.get("secondary") or settings.CIN_SECONDARY_ENGINE
        numeric = tpl_engines.get("numeric_fallback") or settings.CIN_NUMERIC_ENGINE
        experimental = list(tpl_engines.get("experimental") or [])

        if settings.ENABLE_SURYA_EXPERIMENTAL and settings.CIN_EXPERIMENTAL_ENGINE not in experimental:
            experimental.append(settings.CIN_EXPERIMENTAL_ENGINE)

        if not (_is_enabled(primary) and avail.get(primary)):
            primary = "paddle" if avail.get("paddle") else settings.DEFAULT_ENGINE

        if fast_mode:
            secondary = None
            experimental = []

        if secondary and not (_is_enabled(secondary) and avail.get(secondary)):
            secondary = None

        experimental = [
            name
            for name in experimental
            if _is_enabled(name)
            and avail.get(name)
            and name not in {primary, secondary, numeric}
        ]

        if numeric and not (_is_enabled(numeric) and avail.get(numeric)):
            numeric = None

        compare_all = bool(settings.COMPARE_ALL_ENGINES_FOR_CIN and not fast_mode)

        return EnginePlan(
            primary=primary,
            secondary=secondary,
            experimental=experimental,
            numeric_fallback=numeric,
            strategy="fieldwise_cin_compare_all" if compare_all else "fieldwise_cin",
            compare_all_engines=compare_all,
            notes=[
                "engines exécutés séparément puis meilleur candidat choisi par champ"
                if compare_all
                else "zones fixes + scoring par champ + fallback conditionnel"
            ],
        )

    primary = tpl_engines.get("primary") if tpl_engines else None

    if not primary:
        primary = select_engine(
            preferred=getattr(template, "preferred_engine", None),
            fast_mode=fast_mode,
            language=language,
        ).name

    return EnginePlan(
        primary=primary,
        strategy="single_engine",
        compare_all_engines=False,
    )


def should_run_fallback(
    field_name: str,
    best_score: float,
    is_missing: bool,
    required: bool = False,
) -> bool:
    if is_missing:
        return True

    if field_name == "id_number":
        return best_score < 0.96

    if field_name in {"last_name", "first_name"}:
        return best_score < 0.88

    if field_name == "birth_date":
        return best_score < 0.90

    if field_name == "birth_place":
        return best_score < 0.76

    return best_score < 0.80


def get_best_engine_for_doc(
    doc_family: Optional[str] = None,
    language: Optional[str] = None,
    fast_mode: bool = False,
) -> BaseOCREngine:
    plan = build_engine_plan(
        template=None,
        forced_engine=None,
        fast_mode=fast_mode,
        language=language,
        doc_family=doc_family,
    )
    return get_engine_instance(plan.primary)