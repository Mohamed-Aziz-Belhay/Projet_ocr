from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.services.llm_provider import (
    LLMProviderError,
    LLMRateLimitError,
    get_llm_provider,
)


OCR_ASSISTANT_SYSTEM_PROMPT = """
Tu es un assistant expert OCR intégré dans une plateforme FastAPI + Angular.

Mission:
- analyser les résultats OCR fournis en JSON
- expliquer les champs faibles, manquants ou incohérents
- recommander un moteur OCR ou un mode de traitement
- exploiter l'historique si présent
- aider l'utilisateur à améliorer la qualité d'extraction

Règles strictes:
- Réponds toujours en français.
- N'invente jamais un champ absent du JSON.
- Si le contexte OCR est absent, dis clairement qu'il faut lancer une extraction.
- Sois court, structuré et directement exploitable.
- Pour une facture: vérifier numéro, date, total HT, TVA, total TTC, lignes et cohérence.
- Pour CIN/passeport: vérifier numéro, nom, date naissance, nationalité, MRZ si disponible.
- Pour registre commerce: vérifier identifiant unique, raison sociale, date extrait, capital.
- Termine par 2 ou 3 actions concrètes.
"""


class AssistantService:
    async def chat(
        self,
        *,
        message: str,
        last_result: Optional[Dict[str, Any]] = None,
        latest_result_from_history: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        use_ai: bool = False,
    ) -> Dict[str, Any]:
        effective_result = self._choose_effective_result(
            last_result=last_result,
            latest_result_from_history=latest_result_from_history,
        )

        context = self._build_context(
            last_result=effective_result,
            latest_result_from_history=latest_result_from_history,
            history=history or [],
        )

        rules_reply = self._rules_reply(
            message=message,
            context=context,
        )

        llm_enabled = (
            os.getenv("OCR_ASSISTANT_USE_LLM", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if not use_ai:
            return {
                "reply": rules_reply["reply"],
                "suggestions": rules_reply["suggestions"],
                "severity": rules_reply["severity"],
                "mode": "rules",
                "debug": {
                    "use_ai": use_ai,
                    "llm_enabled": llm_enabled,
                    "reason": "frontend_use_ai_false",
                    "has_effective_result": effective_result is not None,
                },
            }

        if not llm_enabled:
            return {
                "reply": (
                    rules_reply["reply"]
                    + "\n\n⚠️ Mode IA demandé, mais OCR_ASSISTANT_USE_LLM n’est pas activé côté backend."
                ),
                "suggestions": rules_reply["suggestions"],
                "severity": "warning",
                "mode": "rules_ai_disabled",
                "debug": {
                    "use_ai": use_ai,
                    "llm_enabled": llm_enabled,
                    "reason": "backend_llm_disabled",
                    "has_effective_result": effective_result is not None,
                },
            }

        try:
            provider = get_llm_provider()
            llm_reply = await provider.chat(
                system_prompt=OCR_ASSISTANT_SYSTEM_PROMPT,
                user_prompt=message,
                context=context,
            )

            return {
                "reply": llm_reply,
                "suggestions": rules_reply["suggestions"],
                "severity": rules_reply["severity"],
                "mode": f"ai:{provider.provider}",
                "debug": {
                    "use_ai": use_ai,
                    "llm_enabled": llm_enabled,
                    "provider": provider.provider,
                    "model": provider.model,
                    "history_count": len(history or []),
                    "has_effective_result": effective_result is not None,
                    "has_latest_result_from_history": latest_result_from_history is not None,
                },
            }
        except LLMRateLimitError as exc:
                retry = ""
                if exc.retry_after_seconds:
                    retry = f" Réessaie dans environ {exc.retry_after_seconds:.0f} secondes."

                return {
                    "reply": (
                        rules_reply["reply"]
                        + "\n\n⚠️ Le mode IA est activé, mais le quota gratuit Groq est temporairement dépassé."
                        + retry
                        + "\nJ’ai donc répondu avec l’analyse locale par règles métier."
                    ),
                    "suggestions": rules_reply["suggestions"],
                    "severity": "warning",
                    "mode": "rules_quota_fallback",
                    "debug": {
                        "use_ai": use_ai,
                        "llm_enabled": llm_enabled,
                        "provider": "groq",
                        "error": str(exc),
                        "retry_after_seconds": exc.retry_after_seconds,
                        "has_effective_result": effective_result is not None,
                    },
                }
        except LLMProviderError as exc:
            return {
                "reply": (
                    rules_reply["reply"]
                    + "\n\n⚠️ Le mode IA a été demandé mais le provider IA a échoué. "
                    + "Fallback règles activé.\n"
                    + f"Détail technique: {str(exc)[:500]}"
                ),
                "suggestions": rules_reply["suggestions"],
                "severity": "warning",
                "mode": "rules_fallback",
                "debug": {
                    "use_ai": use_ai,
                    "llm_enabled": llm_enabled,
                    "error": str(exc),
                    "has_effective_result": effective_result is not None,
                },
            }

        except Exception as exc:
            return {
                "reply": (
                    rules_reply["reply"]
                    + "\n\n⚠️ Erreur inattendue IA. Fallback règles activé.\n"
                    + f"Détail technique: {str(exc)[:500]}"
                ),
                "suggestions": rules_reply["suggestions"],
                "severity": "warning",
                "mode": "rules_fallback",
                "debug": {
                    "use_ai": use_ai,
                    "llm_enabled": llm_enabled,
                    "error": str(exc),
                    "has_effective_result": effective_result is not None,
                },
            }

    def _choose_effective_result(
        self,
        *,
        last_result: Optional[Dict[str, Any]],
        latest_result_from_history: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        unwrapped_last = self._unwrap_last_result(last_result)

        if unwrapped_last:
            return unwrapped_last

        if latest_result_from_history:
            return latest_result_from_history

        return None

    def _build_context(
        self,
        *,
        last_result: Optional[Dict[str, Any]],
        latest_result_from_history: Optional[Dict[str, Any]],
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = self._unwrap_last_result(last_result)
        weak_fields = self._weak_fields(result)
        missing_fields = self._missing_fields(result)
        history_stats = self._history_stats(history)

        return {
            "last_result": result,
            "latest_result_from_history": latest_result_from_history,
            "analysis": {
                "weak_fields": weak_fields,
                "missing_fields": missing_fields,
                "recommendation": self._recommendation(result, weak_fields, missing_fields),
            },
            "history": {
                "items": history[:20],
                "stats": history_stats,
            },
        }

    def _unwrap_last_result(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not payload:
            return None

        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return payload["result"]

        return payload

    def _rules_reply(
        self,
        *,
        message: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        low = message.lower()
        result = context.get("last_result")

        if not result:
            return {
                "reply": (
                    "Je n’ai pas encore de résultat OCR complet à analyser. "
                    "Lance une extraction ou vérifie que le détail est bien sauvegardé dans extraction_results."
                ),
                "suggestions": [
                    "Quel mode OCR utiliser ?",
                    "Analyse l’historique",
                ],
                "severity": "info",
            }

        if any(k in low for k in ["dernier", "dernière", "recent", "récente", "historique"]):
            return self._reply_latest_history(context)

        if any(k in low for k in ["faible", "faibles", "manquant", "manquants", "erreur", "problème"]):
            return self._reply_weak_fields(context)

        if any(k in low for k in ["mode", "moteur", "engine", "ocr utiliser", "recommander"]):
            return self._reply_mode_recommendation(context)

        if any(k in low for k in ["succès", "global", "taux", "statistique"]):
            return self._reply_history(context)

        return self._reply_result_summary(context)

    def _reply_latest_history(self, context: Dict[str, Any]) -> Dict[str, Any]:
        result = context.get("last_result") or {}
        history_items = context.get("history", {}).get("items", [])
        latest_meta = history_items[0] if history_items else result.get("_history", {})

        text = (
            "Dernière extraction disponible :\n\n"
            f"- Fichier : {latest_meta.get('file_name') or result.get('file_name') or '—'}\n"
            f"- Type : {result.get('document_type') or latest_meta.get('document_type') or '—'}\n"
            f"- Template : {result.get('template_id') or latest_meta.get('template_id') or '—'}\n"
            f"- Statut : {result.get('status') or latest_meta.get('status') or '—'}\n"
            f"- Confiance : {self._percent(result.get('global_confidence') or latest_meta.get('global_confidence'))}\n"
            f"- Champs : {len(result.get('fields') or []) or latest_meta.get('field_count') or 0}\n"
            f"- Date : {latest_meta.get('created_at') or '—'}\n\n"
        )

        weak = context["analysis"]["weak_fields"]
        missing = context["analysis"]["missing_fields"]

        if weak:
            text += "Champs faibles détectés :\n"
            for f in weak[:6]:
                text += f"- {f.get('name', 'champ')} : {self._percent(f.get('confidence'))}\n"
        elif missing:
            text += "Des champs obligatoires semblent manquants.\n"
        else:
            text += "Aucun champ faible évident dans le détail chargé.\n"

        return {
            "reply": text,
            "suggestions": [
                "Quels champs sont faibles ?",
                "Quel mode OCR utiliser ?",
                "Analyse le résultat complet",
            ],
            "severity": "warning" if weak or missing else "info",
        }

    def _reply_result_summary(self, context: Dict[str, Any]) -> Dict[str, Any]:
        result = context["last_result"] or {}
        analysis = context["analysis"]
        weak_fields = analysis["weak_fields"]
        missing_fields = analysis["missing_fields"]

        status = result.get("status", "—")
        doc_type = result.get("document_type", "—")
        template_id = result.get("template_id", "—")
        confidence = self._percent(result.get("global_confidence"))
        engine = result.get("engine_used") or result.get("engine") or "—"
        duration = result.get("processing_time_ms", "—")

        text = (
            f"Analyse du résultat OCR :\n\n"
            f"- Statut : {status}\n"
            f"- Type document : {doc_type}\n"
            f"- Template : {template_id}\n"
            f"- Moteur utilisé : {engine}\n"
            f"- Confiance globale : {confidence}\n"
            f"- Temps de traitement : {duration} ms\n"
            f"- Champs faibles : {len(weak_fields)}\n"
            f"- Champs manquants : {len(missing_fields)}\n\n"
        )

        if weak_fields:
            text += "Champs à vérifier en priorité :\n"
            for field in weak_fields[:6]:
                text += (
                    f"- {field.get('name', 'champ')} "
                    f"avec confiance {self._percent(field.get('confidence'))}\n"
                )
        elif missing_fields:
            text += "Aucun champ faible détecté, mais certains champs obligatoires semblent manquants.\n"
        else:
            text += "Le résultat semble exploitable. Vérifie tout de même les champs critiques métier.\n"

        text += "\nRecommandation : " + analysis["recommendation"]

        return {
            "reply": text,
            "suggestions": [
                "Quels champs sont faibles ?",
                "Quel mode OCR utiliser ?",
                "Analyse l’historique",
            ],
            "severity": "warning" if weak_fields or missing_fields else "info",
        }

    def _reply_weak_fields(self, context: Dict[str, Any]) -> Dict[str, Any]:
        weak_fields = context["analysis"]["weak_fields"]
        missing_fields = context["analysis"]["missing_fields"]

        if not weak_fields and not missing_fields:
            return {
                "reply": (
                    "Je ne détecte pas de champ faible évident dans le résultat chargé. "
                    "La confiance globale et les validations semblent acceptables."
                ),
                "suggestions": [
                    "Analyse le dernier résultat OCR",
                    "Quel mode OCR utiliser ?",
                ],
                "severity": "info",
            }

        text = "Champs à vérifier :\n\n"

        if weak_fields:
            text += "Champs faibles :\n"
            for field in weak_fields[:10]:
                text += (
                    f"- {field.get('name', 'champ')} : "
                    f"valeur={field.get('value', '—')} | "
                    f"confiance={self._percent(field.get('confidence'))} | "
                    f"validé={field.get('validated', '—')}\n"
                )

        if missing_fields:
            text += "\nChamps manquants ou vides :\n"
            for field in missing_fields[:10]:
                text += f"- {field.get('name', 'champ')}\n"

        text += (
            "\nActions conseillées : utiliser une image plus nette, vérifier le template, "
            "tester le mode full/debug si le document est complexe."
        )

        return {
            "reply": text,
            "suggestions": [
                "Quel mode OCR utiliser ?",
                "Analyse le dernier résultat OCR",
            ],
            "severity": "warning",
        }

    def _reply_mode_recommendation(self, context: Dict[str, Any]) -> Dict[str, Any]:
        result = context["last_result"] or {}
        doc_type = str(result.get("document_type") or "").lower()
        recommendation = context["analysis"]["recommendation"]

        if doc_type == "invoice":
            mode = (
                "Pour une facture : utilise plutôt PaddleOCR en mode balanced ou full. "
                "Si les lignes de facture sont mal extraites, teste le mode full."
            )
        elif doc_type in {"cin_tn", "id_document"}:
            mode = (
                "Pour CIN ou document d’identité : EasyOCR en mode balanced est souvent plus robuste. "
                "Si l’arabe est important, garde language_hint=ar."
            )
        elif doc_type == "passport":
            mode = "Pour passeport : privilégie EasyOCR ou le chemin MRZ-first si disponible."
        elif doc_type == "registre_commerce":
            mode = (
                "Pour registre de commerce : PaddleOCR en mode balanced/full est recommandé, "
                "avec un template adapté au format moderne ou legacy."
            )
        else:
            mode = (
                "Pour un document inconnu : commence avec engine=auto et document_type=auto. "
                "Si le résultat est faible, teste PaddleOCR puis EasyOCR."
            )

        return {
            "reply": f"{mode}\n\nRecommandation spécifique : {recommendation}",
            "suggestions": [
                "Analyse le dernier résultat OCR",
                "Quels champs sont faibles ?",
            ],
            "severity": "info",
        }

    def _reply_history(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stats = context["history"]["stats"]
        items = context["history"]["items"]

        if not items:
            return {
                "reply": (
                    "Aucun historique n’est disponible dans le contexte actuel. "
                    "Active l’option Historique ou vérifie que l’utilisateur a le droit de le consulter."
                ),
                "suggestions": [
                    "Analyse le dernier résultat OCR",
                    "Quels champs sont faibles ?",
                ],
                "severity": "info",
            }

        text = (
            "Résumé de l’historique OCR :\n\n"
            f"- Total analysé : {stats['total']}\n"
            f"- Succès : {stats['success']}\n"
            f"- Partiels / review : {stats['partial']}\n"
            f"- Échecs : {stats['failed']}\n"
            f"- Confiance moyenne : {self._percent(stats['avg_confidence'])}\n\n"
        )

        problematic = [item for item in items if self._is_bad_history_item(item)]

        if problematic:
            text += "Documents à surveiller :\n"
            for item in problematic[:6]:
                text += (
                    f"- {item.get('file_name', 'Document')} | "
                    f"type={item.get('document_type', '—')} | "
                    f"statut={item.get('status', '—')} | "
                    f"confiance={self._percent(item.get('global_confidence'))}\n"
                )
        else:
            text += "Aucun document problématique évident dans les éléments fournis."

        return {
            "reply": text,
            "suggestions": [
                "Quels documents posent problème ?",
                "Quel mode OCR utiliser ?",
            ],
            "severity": "warning" if problematic else "info",
        }

    def _weak_fields(self, result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not result:
            return []

        fields = result.get("fields") or []

        if not isinstance(fields, list):
            return []

        weak: List[Dict[str, Any]] = []

        for field in fields:
            if not isinstance(field, dict):
                continue

            confidence = field.get("confidence")
            validated = field.get("validated")
            value = field.get("value")

            try:
                conf = float(confidence)
            except Exception:
                conf = None

            if value in (None, ""):
                continue

            if conf is not None and conf < 0.70:
                weak.append(field)
                continue

            if validated is False:
                weak.append(field)

        return weak

    def _missing_fields(self, result: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not result:
            return []

        fields = result.get("fields") or []

        if not isinstance(fields, list):
            return []

        missing: List[Dict[str, Any]] = []

        for field in fields:
            if not isinstance(field, dict):
                continue

            value = field.get("value")
            required = field.get("required")

            if required and value in (None, "", [], {}):
                missing.append(field)

        return missing

    def _recommendation(
        self,
        result: Optional[Dict[str, Any]],
        weak_fields: List[Dict[str, Any]],
        missing_fields: List[Dict[str, Any]],
    ) -> str:
        if not result:
            return "Lance une extraction avant l’analyse."

        doc_type = str(result.get("document_type") or "").lower()
        global_conf = result.get("global_confidence")

        try:
            conf = float(global_conf)
            if conf > 1:
                conf = conf / 100
        except Exception:
            conf = None

        if missing_fields:
            return "Vérifie le template et les champs obligatoires, puis relance en mode full."

        if weak_fields:
            return "Les champs faibles doivent être validés manuellement ou relus avec un mode OCR plus robuste."

        if conf is not None and conf < 0.75:
            return "La confiance globale est moyenne. Utilise une image plus nette ou teste un autre moteur OCR."

        if doc_type == "invoice":
            return "Contrôle la cohérence Total HT + TVA + timbre = Total TTC."

        if doc_type in {"passport", "id_document", "cin_tn"}:
            return "Contrôle les champs d’identité et la cohérence des dates."

        return "Résultat exploitable, avec validation métier recommandée."

    def _history_stats(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        total = len(history)
        success = 0
        partial = 0
        failed = 0
        conf_values: List[float] = []

        for item in history:
            status = str(item.get("status") or "").lower()

            if any(k in status for k in ["success", "done", "valid"]):
                success += 1
            elif any(k in status for k in ["partial", "review"]):
                partial += 1
            elif any(k in status for k in ["fail", "error"]):
                failed += 1

            try:
                c = float(item.get("global_confidence"))
                if c > 1:
                    c = c / 100
                conf_values.append(c)
            except Exception:
                pass

        avg = sum(conf_values) / len(conf_values) if conf_values else None

        return {
            "total": total,
            "success": success,
            "partial": partial,
            "failed": failed,
            "avg_confidence": avg,
        }

    def _is_bad_history_item(self, item: Dict[str, Any]) -> bool:
        status = str(item.get("status") or "").lower()

        if any(k in status for k in ["fail", "error", "partial", "review"]):
            return True

        try:
            conf = float(item.get("global_confidence"))
            if conf > 1:
                conf = conf / 100
            return conf < 0.70
        except Exception:
            return False

    def _percent(self, value: Any) -> str:
        try:
            n = float(value)
            if n <= 1:
                n *= 100
            return f"{n:.1f}%"
        except Exception:
            return "—"


_assistant_service: Optional[AssistantService] = None


def get_assistant_service() -> AssistantService:
    global _assistant_service

    if _assistant_service is None:
        _assistant_service = AssistantService()

    return _assistant_service