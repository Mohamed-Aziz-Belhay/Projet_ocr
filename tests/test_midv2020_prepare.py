from __future__ import annotations

from pathlib import Path

from app.models.swin.prepare_midv2020 import infer_class_name, iter_via_regions, region_to_record


def test_via_json_region_conversion(tmp_path):
    sample = {
        "_via_settings": {"project": {"name": "midv2020-test-passport"}},
        "_via_img_metadata": {
            "00.jpg123": {
                "filename": "00.jpg",
                "regions": [
                    {
                        "shape_attributes": {"name": "rect", "x": 10, "y": 20, "width": 100, "height": 30},
                        "region_attributes": {"field_name": "surname", "value": "DOE"},
                    }
                ],
            }
        },
    }
    class_name = infer_class_name(Path("test.json"), sample)
    assert class_name == "test_passport"
    regions = list(iter_via_regions(sample))
    assert len(regions) == 1
    filename, region_list = regions[0]
    assert filename == "00.jpg"
    rec = region_to_record(class_name=class_name, filename=filename, image_path=tmp_path / filename, image_size=(1000, 500), region=region_list[0])
    assert rec is not None
    assert rec["field_name"] == "surname"
    assert rec["value"] == "DOE"
    assert rec["bbox_norm"] == [0.01, 0.04, 0.1, 0.06]
