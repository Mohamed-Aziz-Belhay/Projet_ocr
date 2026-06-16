"""
app/db/models/template.py
Modèle ORM OcrTemplate — compatible avec TemplateSpec (schemas/template.py)
et routes_templates.py existants.
"""
from __future__ import annotations
from typing import Any

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class OcrTemplate(Base, UUIDMixin, TimestampMixin):
    """
    Table PostgreSQL : ocr_templates
    Structure alignée avec TemplateSpec (app/schemas/template.py)
    et le stockage YAML existant.
    """
    __tablename__ = "ocr_templates"

    # ── Identité métier ───────────────────────────────────────────────────────
    template_id: Mapped[str] = mapped_column(
        String(120),
        unique=True,
        nullable=False,
        index=True,
        comment="ID métier unique (ex: invoice_tn) — remplace le nom du fichier .yaml",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
    )
    version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="1.0",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # ── Classification ────────────────────────────────────────────────────────
    doc_family: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
        comment="Famille de document (ex: invoice, id_document, ...)",
    )
    document_type: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
        comment="Type précis (ex: invoice_tn, cin_tn)",
    )
    language: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        default="auto",
    )

    # ── Pipeline / moteur ─────────────────────────────────────────────────────
    preferred_engine: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="auto",
    )
    pipeline: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="generic_template_v1",
        comment="Pipeline de traitement OCR",
    )
    template_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="regex",
        comment="Mode : regex | roi | hybrid",
    )

    # ── Contenu JSON (miroir exact de TemplateSpec) ───────────────────────────
    fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="Liste FieldSpec sérialisée",
    )
    output_mapping: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    language_hints: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    anchors_required: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="Ancres obligatoires pour la détection automatique",
    )
    postprocess_hooks: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )

    # ── Champs architecture avancée (TemplateSpec) ────────────────────────────
    fixed_zones: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Zones fixes ROI (coordonnées)",
    )
    engines: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Config par moteur OCR",
    )
    field_policies: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    review_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    # ── État ──────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        index=True,
    )
    usage_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # ── Extra (champs supplémentaires non structurés) ─────────────────────────
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=True,
        default=dict,
        comment="Champs extra du YAML non couverts par le schéma",
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Sérialisation
    # ─────────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """
        Retourne un dict identique à ce que lisait yaml.safe_load()
        sur les anciens fichiers YAML → compatibilité totale avec Angular.
        """
        return {
            # Identité
            "id": self.template_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            # Classification
            "doc_family": self.doc_family,
            "document_type": self.document_type,
            "language": self.language,
            # Pipeline
            "preferred_engine": self.preferred_engine,
            "pipeline": self.pipeline,
            "template_mode": self.template_mode,
            # Contenu
            "fields": self.fields or [],
            "output_mapping": self.output_mapping or {},
            "language_hints": self.language_hints or [],
            "anchors_required": self.anchors_required or [],
            "postprocess_hooks": self.postprocess_hooks or [],
            # Architecture avancée
            "fixed_zones": self.fixed_zones or {},
            "engines": self.engines or {},
            "field_policies": self.field_policies or {},
            "review_policy": self.review_policy or {},
            # Meta
            "is_active": self.is_active,
            "usage_count": self.usage_count,
            "extra": self.extra or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_summary(self) -> dict[str, Any]:
        """
        Version allégée pour le catalogue Angular
        (remplace list_templates() de routes_templates.py).
        """
        return {
            "id": self.template_id,
            "name": self.name,
            "version": self.version,
            "document_type": self.document_type,
            "doc_family": self.doc_family,
            "language": self.language,
            "preferred_engine": self.preferred_engine,
            "pipeline": self.pipeline,
            "template_mode": self.template_mode,
            "is_active": self.is_active,
            "field_count": len(self.fields or []),
            "usage_count": self.usage_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_template_spec(cls, spec_dict: dict[str, Any]) -> "OcrTemplate":
        """
        Crée un OcrTemplate depuis un dict TemplateSpec
        (utilisé pendant la migration YAML → PostgreSQL).
        """
        # Champs connus → colonnes dédiées
        known_keys = {
            "id", "name", "version", "description", "doc_family",
            "document_type", "language", "preferred_engine", "pipeline",
            "template_mode", "fields", "output_mapping", "language_hints",
            "anchors_required", "postprocess_hooks", "fixed_zones",
            "engines", "field_policies", "review_policy",
        }

        # Tout ce qui n'est pas dans known_keys va dans extra
        extra = {k: v for k, v in spec_dict.items() if k not in known_keys}

        # Sérialise les FieldSpec si ce sont des objets Pydantic
        fields_raw = spec_dict.get("fields", [])
        fields_serialized = []
        for f in fields_raw:
            if hasattr(f, "model_dump"):
                fields_serialized.append(f.model_dump())
            elif isinstance(f, dict):
                fields_serialized.append(f)
            else:
                fields_serialized.append(dict(f))

        return cls(
            template_id=spec_dict.get("id", ""),
            name=spec_dict.get("name", ""),
            version=str(spec_dict.get("version", "1.0")),
            description=spec_dict.get("description"),
            doc_family=spec_dict.get("doc_family"),
            document_type=spec_dict.get("document_type"),
            language=spec_dict.get("language"),
            preferred_engine=spec_dict.get("preferred_engine", "auto"),
            pipeline=spec_dict.get("pipeline", "generic_template_v1"),
            template_mode=spec_dict.get("template_mode", "regex"),
            fields=fields_serialized,
            output_mapping=spec_dict.get("output_mapping") or {},
            language_hints=spec_dict.get("language_hints") or [],
            anchors_required=spec_dict.get("anchors_required") or [],
            postprocess_hooks=spec_dict.get("postprocess_hooks") or [],
            fixed_zones=spec_dict.get("fixed_zones") or {},
            engines=spec_dict.get("engines") or {},
            field_policies=spec_dict.get("field_policies") or {},
            review_policy=spec_dict.get("review_policy") or {},
            is_active=True,
            usage_count=0,
            extra=extra,
        )

    def __repr__(self) -> str:
        return (
            f"<OcrTemplate id={self.template_id!r} "
            f"name={self.name!r} "
            f"fields={len(self.fields or [])}>"
        )