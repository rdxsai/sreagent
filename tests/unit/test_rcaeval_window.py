from sentinel_rcaeval.normalize import in_window, make_window, rebase


def test_window_bounds_and_span():
    w = make_window(1000, pre=180, post=300)
    assert (w.start_abs, w.end_abs, w.span_seconds) == (820, 1300, 480)
    assert w.onset_second == 180


def test_rebase_and_membership():
    w = make_window(1000)
    assert rebase(820, w) == 0
    assert rebase(1000, w) == 180
    assert in_window(820, w) and in_window(1300, w)
    assert not in_window(819, w)
    assert not in_window(1301, w)
