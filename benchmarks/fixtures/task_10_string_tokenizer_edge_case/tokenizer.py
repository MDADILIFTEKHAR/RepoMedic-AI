"""CSV line tokenizer."""

def tokenize_csv(line: str) -> list[str]:
    # Bug: crashes on empty string with split without strip
    return [p.strip() for p in line.split(",") if p.strip()]
