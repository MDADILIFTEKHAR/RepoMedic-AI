from tokenizer import tokenize_csv

def test_standard_csv():
    assert tokenize_csv("alpha, beta, gamma") == ["alpha", "beta", "gamma"]

def test_empty_string():
    assert tokenize_csv("") == []
    assert tokenize_csv("   ") == []
