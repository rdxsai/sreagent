from sentinel.sandbox.presentation import present


def test_short_output_passes_through_with_footer():
    body = present("ad cpu shift at 420\n", None, 4)
    assert "ad cpu shift at 420" in body
    assert "[exit:0 | 4ms]" in body


def test_error_appends_stderr_and_nonzero_exit():
    body = present("partial\n", "Traceback...\nValueError: boom", 7)
    assert "[stderr]" in body
    assert "ValueError: boom" in body
    assert "[exit:1 | 7ms]" in body


def test_long_output_is_truncated_with_note():
    big = "\n".join(f"line {i}" for i in range(1000))
    body = present(big, None, 5)
    assert "output truncated" in body
    assert "line 0" in body
    assert "line 999" not in body
