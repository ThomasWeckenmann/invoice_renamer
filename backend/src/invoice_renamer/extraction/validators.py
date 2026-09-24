"""Field validators shared across the extraction and run-metrics contracts."""

# Active ISO-4217 codes (including fund, precious-metal, and test codes), kept
# inline instead of depending on a currency database package. Update when
# ISO 4217 adds or withdraws a code.
_ISO4217_CODES = frozenset(
    (
        "AED AFN ALL AMD AOA ARS AUD AWG AZN BAM BBD BDT BHD BIF BMD BND BOB BOV BRL BSD BTN "
        "BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUP CVE CZK DJF DKK DOP DZD "
        "EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR "
        "IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD "
        "MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD "
        "OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE "
        "SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI "
        "UYU UYW UZS VED VES VND VUV WST XAD XAF XAG XAU XBA XBB XBC XBD XCD XCG XDR XOF XPD "
        "XPF XPT XSU XTS XUA XXX YER ZAR ZMW ZWG"
    ).split()
)


def validate_iso4217_currency(value: str) -> str:
    if len(value) != 3 or not value.isascii() or not value.isalpha() or value != value.upper():
        raise ValueError("must be a 3-letter uppercase ISO-4217 code")
    if value not in _ISO4217_CODES:
        raise ValueError(f"{value!r} is not a recognized ISO-4217 currency code")
    return value
