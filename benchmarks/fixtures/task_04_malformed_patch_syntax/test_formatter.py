from formatter import format_title

def test_format_title():
    assert format_title("  hello world  ") == "Hello World"
