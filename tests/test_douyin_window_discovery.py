from bridge.douyin_visible_runtime_patch import _parse_preferred_hwnd, candidate_score


def test_process_match_finds_live_companion_without_title():
    score = candidate_score(
        title="",
        process_name="直播伴侣.exe",
        process_path=r"D:\PFiles\webcast_mate\12.6.4.431952921\直播伴侣.exe",
        class_name="Chrome_WidgetWin_1",
        visible=False,
        iconic=False,
        width=1280,
        height=720,
        title_pattern=r".*直播伴侣.*",
        process_name_pattern=r"^直播伴侣(?:\.exe)?$",
        process_path_pattern=r"webcast_mate|直播伴侣\.exe$",
    )
    assert score is not None
    assert score >= 400


def test_unrelated_window_is_rejected():
    score = candidate_score(
        title="ALiver 控制台",
        process_name="msedge.exe",
        process_path=r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        class_name="Chrome_WidgetWin_1",
        visible=True,
        iconic=False,
        width=1280,
        height=720,
        title_pattern=r".*直播伴侣.*",
        process_name_pattern=r"^直播伴侣(?:\.exe)?$",
        process_path_pattern=r"webcast_mate|直播伴侣\.exe$",
    )
    assert score is None


def test_exact_title_still_works_when_process_path_is_unavailable():
    score = candidate_score(
        title="直播伴侣",
        process_name="",
        process_path="",
        class_name="Chrome_WidgetWin_1",
        visible=True,
        iconic=False,
        width=1280,
        height=720,
        title_pattern=r".*直播伴侣.*",
        process_name_pattern=r"^直播伴侣(?:\.exe)?$",
        process_path_pattern=r"webcast_mate|直播伴侣\.exe$",
    )
    assert score is not None
    assert score >= 200


def test_preferred_hwnd_accepts_power_shell_hex_format():
    assert _parse_preferred_hwnd("0x10788") == 0x10788
    assert _parse_preferred_hwnd(0x10788) == 0x10788
    assert _parse_preferred_hwnd("") is None
