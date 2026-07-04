from __future__ import annotations

import re
from typing import Any, Callable, Dict, List

from app.core.logging import get_logger

log = get_logger(__name__)

_HOOKS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
_ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def register_hook(name: str):
    def decorator(fn: Callable):
        _HOOKS[name] = fn
        return fn
    return decorator


def apply_hooks(fields: Dict[str, Any], hook_names: List[str]) -> Dict[str, Any]:
    result = dict(fields)
    for name in hook_names:
        hook = _HOOKS.get(name)
        if hook is None:
            log.warning("Hook inconnu", extra={"hook": name})
            continue
        try:
            result = hook(result)
        except Exception as exc:
            log.error("Hook échoué", extra={"hook": name, "error": str(exc)})
    return result


def generic_post_process(text: str, keep_newlines: bool = False) -> str:
    if not text:
        return ""

    t = str(text).translate(_ARABIC_DIGITS_TRANS)
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\x0c", "\n")
    t = t.replace("\u00a0", " ")
    t = t.replace("|", " ")
    t = t.replace("’", "'").replace("‘", "'")
    t = t.replace("“", '"').replace("”", '"')
    t = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", t)

    if keep_newlines:
        cleaned_lines = []
        for line in t.splitlines():
            line = re.sub(r"[ \t]+", " ", line).strip()
            if line:
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    t = t.replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


@register_hook("normalize_dates")
def normalize_dates(fields: Dict[str, Any]) -> Dict[str, Any]:
    return dict(fields)