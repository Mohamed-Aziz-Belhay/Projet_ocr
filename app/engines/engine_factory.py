# /engine_factory.py
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from importlib import import_module
from threading import RLock
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class OCRWord:
    text: str
    confidence: float = 0.0
    bbox: Optional[Sequence[float]] = None
    line_id: Optional[str] = None

    @property
    def box(self) -> Optional[Sequence[float]]:
        return self.bbox


OCRToken = OCRWord


@dataclass
class OCRZoneResult:
    zone_name: str
    text: str = ""
    confidence: float = 0.0
    words: List[OCRWord] = field(default_factory=list)
    language: Optional[str] = None
    engine: str = ""
    processing_time_ms: int = 0
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OCRResult:
    full_text: str = ""
    confidence: float = 0.0
    words: List[OCRWord] = field(default_factory=list)
    language: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    engine: Optional[str] = None
    processing_time_ms: int = 0
    zones: Dict[str, OCRZoneResult] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.full_text

    @property
    def raw_text(self) -> str:
        return self.full_text

    @property
    def full_text_score(self) -> float:
        return self.confidence


class BaseOCREngine(ABC):
    name: str = "unknown"

    def is_available(self) -> bool:
        return True

    def recognize_document(
        self,
        image: Any,
        language_hints: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> OCRResult:
        lang = None
        if language_hints:
            if isinstance(language_hints, list) and language_hints:
                lang = language_hints[0]
            elif isinstance(language_hints, str):
                lang = language_hints
        return self.run(image=image, language=lang)

    def recognize_zones(
        self,
        image: Any,
        zones: Dict[str, List[float]],
        language_hints: Optional[List[str]] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, OCRZoneResult]:
        results: Dict[str, OCRZoneResult] = {}
        lang = None
        if language_hints:
            if isinstance(language_hints, list) and language_hints:
                lang = language_hints[0]
            elif isinstance(language_hints, str):
                lang = language_hints
        for zone_name, zone in (zones or {}).items():
            crop = self._crop_zone(image, zone)
            if crop is None or getattr(crop, "size", 0) == 0:
                results[zone_name] = OCRZoneResult(zone_name=zone_name, engine=self.name)
                continue
            doc = self.run(image=crop, language=lang)
            avg_conf = 0.0
            if getattr(doc, "words", None):
                avg_conf = sum(float(getattr(w, "confidence", 0.0) or 0.0) for w in doc.words) / max(1, len(doc.words))
            results[zone_name] = OCRZoneResult(
                zone_name=zone_name,
                text=getattr(doc, "full_text", "") or "",
                confidence=round(avg_conf, 3),
                words=getattr(doc, "words", []) or [],
                language=getattr(doc, "language", None),
                engine=getattr(doc, "engine", self.name) or self.name,
                processing_time_ms=int(getattr(doc, "processing_time_ms", 0) or 0),
                meta={"bbox": zone},
            )
        return results

    def run(self, image: Any, language: Optional[str] = None) -> OCRResult:
        raise NotImplementedError

    @staticmethod
    def _crop_zone(image: Any, zone: List[float]):
        if image is None or getattr(image, "size", 0) == 0:
            return image
        h, w = image.shape[:2]
        x1, y1, x2, y2 = zone
        if max(zone) <= 1.0:
            x1, y1, x2, y2 = int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)
        else:
            x1, y1, x2, y2 = map(int, zone)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return image[0:0, 0:0]
        return image[y1:y2, x1:x2].copy()


_ENGINE_CACHE: Dict[str, BaseOCREngine] = {}
_ENGINE_CLASSES: Dict[str, Type[BaseOCREngine]] = {}
_ENGINE_LOCK = RLock()

ENGINE_MODULES: Dict[str, str] = {
    "paddle": "app.engines.paddle_engine",
    "tesseract": "app.engines.tesseract_engine",
    "easyocr": "app.engines.easyocr_engine",
    "surya": "app.engines.surya_engine",
}

ENGINE_CLASS_CANDIDATES: Dict[str, List[str]] = {
    "paddle": ["PaddleEngine", "PaddleOCREngine", "PaddleOCRAdapter", "PaddleOCRService"],
    "tesseract": ["TesseractEngine", "TesseractOCREngine", "TesseractOCRAdapter", "TesseractOCRService"],
    "easyocr": ["EasyOCREngine", "EasyOCRAdapter", "EasyOCRService"],
    "surya": ["SuryaEngine", "SuryaOCREngine", "SuryaOCRAdapter", "SuryaOCRService"],
}


def _engine_name_from_obj(obj: Any) -> str:
    name = getattr(obj, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()

    cls_name = obj.__name__ if isinstance(obj, type) else obj.__class__.__name__
    low = cls_name.lower()
    if "paddle" in low:
        return "paddle"
    if "tesseract" in low:
        return "tesseract"
    if "easyocr" in low or "easy_ocr" in low:
        return "easyocr"
    if "surya" in low:
        return "surya"
    raise ValueError(f"Impossible d'inférer le nom du moteur depuis: {cls_name}")


def _register_instance(name: str, instance: BaseOCREngine) -> BaseOCREngine:
    key = str(name).strip().lower()
    if not key:
        raise ValueError("Engine name is required")
    with _ENGINE_LOCK:
        _ENGINE_CACHE[key] = instance
    return instance


def _register_class(name: str, cls: Type[BaseOCREngine]) -> Type[BaseOCREngine]:
    key = str(name).strip().lower()
    if not key:
        raise ValueError("Engine name is required")
    with _ENGINE_LOCK:
        _ENGINE_CLASSES[key] = cls
    return cls


def register_engine(name_or_obj: Any = None, engine: Optional[Any] = None):
    if engine is not None:
        if isinstance(engine, type):
            return _register_class(str(name_or_obj), engine)
        return _register_instance(str(name_or_obj), engine)

    if callable(name_or_obj) and not isinstance(name_or_obj, str):
        obj = name_or_obj
        if isinstance(obj, type):
            key = _engine_name_from_obj(obj)
            return _register_class(key, obj)
        built = obj()
        key = _engine_name_from_obj(built)
        _register_instance(key, built)
        return obj

    if isinstance(name_or_obj, str):
        key = name_or_obj.strip().lower()

        def decorator(obj: Any):
            if isinstance(obj, type):
                return _register_class(key, obj)
            built = obj()
            _register_instance(key, built)
            return obj

        return decorator

    raise ValueError("register_engine requires a name, a class, or an engine instance")


def unregister_engine(name: str) -> None:
    key = (name or "").strip().lower()
    if not key:
        return
    with _ENGINE_LOCK:
        _ENGINE_CACHE.pop(key, None)
        _ENGINE_CLASSES.pop(key, None)


def clear_engine_cache() -> None:
    with _ENGINE_LOCK:
        _ENGINE_CACHE.clear()


def _try_module_exports(mod: Any, key: str) -> Optional[BaseOCREngine]:
    for attr_name in ("ENGINE", "engine", "ENGINE_INSTANCE", "engine_instance"):
        if hasattr(mod, attr_name):
            inst = getattr(mod, attr_name)
            if inst is not None:
                if isinstance(inst, type):
                    return inst()
                return inst

    for fn_name in ("get_engine", "build_engine", "create_engine", "make_engine"):
        if hasattr(mod, fn_name):
            fn = getattr(mod, fn_name)
            if callable(fn):
                try:
                    built = fn()
                except TypeError:
                    built = fn(key)
                if built is not None:
                    return built
    return None


def _instantiate_from_module(name: str) -> BaseOCREngine:
    key = name.lower()
    module_name = ENGINE_MODULES.get(key)
    if not module_name:
        raise KeyError(f"Engine '{name}' not declared in ENGINE_MODULES")

    mod = import_module(module_name)

    with _ENGINE_LOCK:
        if key in _ENGINE_CACHE:
            return _ENGINE_CACHE[key]
        if key in _ENGINE_CLASSES:
            instance = _ENGINE_CLASSES[key]()
            _ENGINE_CACHE[key] = instance
            return instance

    exported = _try_module_exports(mod, key)
    if exported is not None:
        with _ENGINE_LOCK:
            _ENGINE_CACHE[key] = exported
        return exported

    for cls_name in ENGINE_CLASS_CANDIDATES.get(key, []):
        if hasattr(mod, cls_name):
            cls = getattr(mod, cls_name)
            if callable(cls):
                instance = cls()
                with _ENGINE_LOCK:
                    _ENGINE_CACHE[key] = instance
                return instance

    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if not callable(obj):
            continue
        if attr_name.endswith(("Engine", "Adapter", "Service")):
            try:
                instance = obj()
                with _ENGINE_LOCK:
                    _ENGINE_CACHE[key] = instance
                return instance
            except Exception:
                continue

    raise RuntimeError(f"Impossible d'instancier le moteur '{name}' depuis {module_name}. Vérifiez la classe exportée.")


def get_engine(name: str) -> BaseOCREngine:
    key = (name or "").strip().lower()
    if not key:
        raise ValueError("Engine name is required")
    with _ENGINE_LOCK:
        cached = _ENGINE_CACHE.get(key)
    if cached is not None:
        return cached
    instance = _instantiate_from_module(key)
    with _ENGINE_LOCK:
        _ENGINE_CACHE[key] = instance
    return instance


class EngineFactory:
    def get(self, name: str) -> BaseOCREngine:
        return get_engine(name)

    def get_engine(self, name: str) -> BaseOCREngine:
        return get_engine(name)


def get_engine_factory() -> EngineFactory:
    return EngineFactory()


def available_engines() -> Dict[str, bool]:
    result: Dict[str, bool] = {}
    for name, module_name in ENGINE_MODULES.items():
        try:
            import_module(module_name)
            eng = get_engine(name)
            result[name] = bool(eng.is_available()) if hasattr(eng, "is_available") else True
        except Exception as exc:
            result[name] = False
            log.warning("Engine unavailable", extra={"engine_key": name, "error": str(exc)})
    return result
