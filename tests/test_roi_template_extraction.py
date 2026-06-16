from __future__ import annotations

import numpy as np

from app.services.roi_template_extraction_service import get_roi_template_extraction_service


def test_roi_service_no_roi_fields():
    service = get_roi_template_extraction_service()
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    fields, normalized, debug, warnings = service.extract(image=image, template={"id": "empty", "roi_fields": []}, engine_name="paddle", language_hints=["en"])
    assert fields == []
    assert normalized == {}
    assert debug["roi_extraction"] == "no_roi_fields"
    assert warnings == []
