"""Field validators shared across the extraction and run-metrics contracts."""

import pycountry


def validate_iso4217_currency(value: str) -> str:
    if len(value) != 3 or not value.isascii() or not value.isalpha() or value != value.upper():
        raise ValueError("must be a 3-letter uppercase ISO-4217 code")
    if pycountry.currencies.get(alpha_3=value) is None:
        raise ValueError(f"{value!r} is not a recognized ISO-4217 currency code")
    return value
