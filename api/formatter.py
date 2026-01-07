def build_context(text: str) -> str:
    # MVP: kısaltma / limit
    text = text.strip()
    body = text[:20000]  # free safety cap

    return f"""## CONTEXT SUMMARY
- Purpose:
- Key Concepts:
- Definitions:
- Rules & Constraints:
- Edge Cases:

## CLEAN SOURCE
{body}
""".strip()
