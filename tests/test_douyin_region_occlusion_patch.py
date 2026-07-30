from bridge.douyin_region_occlusion_patch import _rects_overlap


def test_side_by_side_windows_do_not_block_ocr_region():
    ocr_region = (2139, 331, 2401, 730)
    browser_window = (0, 0, 1139, 900)
    assert _rects_overlap(ocr_region, browser_window) is False


def test_touching_edges_are_not_treated_as_occlusion():
    ocr_region = (100, 100, 300, 300)
    adjacent_window = (0, 0, 100, 500)
    assert _rects_overlap(ocr_region, adjacent_window) is False


def test_window_covering_ocr_region_is_detected():
    ocr_region = (100, 100, 300, 300)
    overlapping_window = (250, 150, 500, 450)
    assert _rects_overlap(ocr_region, overlapping_window) is True
