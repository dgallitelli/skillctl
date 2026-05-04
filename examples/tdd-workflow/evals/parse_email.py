import re


def parse_email(email: str) -> dict | None:
    """Parse an email address into local and domain parts.

    Known bug: rejects addresses containing a '+' in the local part.
    """
    pattern = r"^([a-zA-Z0-9_.]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})$"
    match = re.match(pattern, email)
    if not match:
        return None
    return {"local": match.group(1), "domain": match.group(2)}
