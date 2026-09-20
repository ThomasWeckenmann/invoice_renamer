"""Security/sanity tests for embedded invoice XML handling: attachment names
and XML are untrusted input, and hostile or oversized payloads must have
bounded, visible outcomes rather than crashing an otherwise readable PDF batch.
"""

import time

from invoice_renamer.documents.xml_attachments import (
    KNOWN_ATTACHMENT_NAMES,
    XmlDiscoveryStatus,
    discover_invoice_xml,
)


class _FakeReader:
    """A minimal stand-in for pypdf's PdfReader - discover_invoice_xml only ever
    reads `.attachments`, so a plain dict is enough to drive it without building
    a real PDF for tests that only care about the attachment-classification
    logic itself."""

    def __init__(self, attachments: dict[str, list[bytes]]) -> None:
        self.attachments = attachments


def test_xxe_external_entity_is_rejected_not_resolved() -> None:
    payload = b"""<?xml version="1.0"?>
<!DOCTYPE rsm:CrossIndustryInvoice [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
  <rsm:ExchangedDocument><ram:ID>&xxe;</ram:ID></rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))

    assert result.status == XmlDiscoveryStatus.INVALID
    assert result.candidate is None
    assert result.warning is not None
    assert len(result.warning) < 300  # bounded, and couldn't have resolved /etc/passwd into it


def test_bare_doctype_with_no_entities_is_still_rejected() -> None:
    # forbid_entities alone isn't enough: a DOCTYPE with an external SYSTEM
    # subset and no <!ENTITY> at all must still be rejected, since this app has
    # no legitimate use for a DTD in an invoice XML attachment.
    payload = b"""<?xml version="1.0"?>
<!DOCTYPE rsm:CrossIndustryInvoice SYSTEM "whatever.dtd">
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument><ram:ID>DTD-TEST</ram:ID></rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))

    assert result.status == XmlDiscoveryStatus.INVALID


def test_unknown_xml_declaration_encoding_is_rejected_not_crashed() -> None:
    # A garbled encoding name raises a plain LookupError from the underlying
    # codec lookup, not ParseError/DefusedXmlException - must still be treated
    # as "not usable XML", not propagate out and fail the whole job.
    payload = (
        b'<?xml version="1.0" encoding="nonexistent-charset-xyz"?>'
        b"<rsm:CrossIndustryInvoice "
        b'xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
    )
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))

    assert result.status == XmlDiscoveryStatus.INVALID
    assert result.warning is not None


def test_deeply_nested_xml_under_the_byte_cap_is_rejected_fast() -> None:
    # Small enough to sail past the byte-size cap - only the depth guard can
    # catch this one, and it must do so in well under a second.
    depth = 500
    payload = b"<a>" * depth + b"x" + b"</a>" * depth

    start = time.perf_counter()
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))
    elapsed = time.perf_counter() - start

    assert result.status == XmlDiscoveryStatus.INVALID
    assert result.warning is not None and "depth" in result.warning
    assert elapsed < 1.0


def test_same_attachment_name_with_conflicting_revisions_is_ambiguous() -> None:
    # pypdf groups multiple attachments that share one exact name into a list of
    # revisions under one dict key - discover_invoice_xml must compare all of
    # them, not just the first, or a second conflicting invoice goes unnoticed.
    result = discover_invoice_xml(
        _FakeReader({"factur-x.xml": [_VALID_CII_XML, _VALID_CII_XML.replace(b"INV-1", b"INV-2")]})
    )

    assert result.status == XmlDiscoveryStatus.AMBIGUOUS
    assert result.candidate is None


def test_revisions_beyond_the_cap_reject_rather_than_silently_truncate() -> None:
    # Five identical revisions followed by a genuinely different sixth one used
    # to classify SUPPORTED: the truncation to the first _MAX_REVISIONS_PER_NAME
    # revisions happened before the ambiguity check ever saw the sixth. Exceeding
    # the cap must reject XML use outright, not silently check only a prefix.
    different = _VALID_CII_XML.replace(b"INV-1", b"INV-DIFFERENT")
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [_VALID_CII_XML] * 5 + [different]}))

    assert result.status == XmlDiscoveryStatus.AMBIGUOUS
    assert result.candidate is None


def test_revisions_exactly_at_the_cap_still_resolve_supported() -> None:
    # Regression guard for the fix above: staying within the cap, even with the
    # maximum allowed revision count, must not itself become a rejection.
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [_VALID_CII_XML] * 5}))

    assert result.status == XmlDiscoveryStatus.SUPPORTED


def test_billion_laughs_entity_expansion_is_rejected_quickly() -> None:
    payload = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
]>
<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100">
  &lol2;
</rsm:CrossIndustryInvoice>
"""
    start = time.perf_counter()
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))
    elapsed = time.perf_counter() - start

    assert result.status == XmlDiscoveryStatus.INVALID
    assert elapsed < 1.0  # rejected on the DOCTYPE itself, never attempts expansion


def test_oversized_attachment_is_rejected_before_parsing() -> None:
    # Well past the 10 MB cap, but cheap to build: parsing would notice the
    # unclosed tag too, so only the size check proves it's rejected *before* that.
    payload = b"<rsm:CrossIndustryInvoice>" + b"A" * (11 * 1024 * 1024)
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))

    assert result.status == XmlDiscoveryStatus.INVALID
    assert result.warning is not None
    assert "byte" in result.warning


def test_invalid_byte_sequence_is_rejected_not_crashed() -> None:
    payload = b"\xff\xfe\x00\x01not valid xml at all"
    result = discover_invoice_xml(_FakeReader({"factur-x.xml": [payload]}))

    assert result.status == XmlDiscoveryStatus.INVALID
    assert result.candidate is None


_VALID_CII_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter><ram:ID>urn:cen.eu:en16931:2017</ram:ID></ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument><ram:ID>INV-1</ram:ID></rsm:ExchangedDocument>
</rsm:CrossIndustryInvoice>
"""


def test_many_case_variant_known_names_still_resolve_supported() -> None:
    # Same known logical name, several case spellings, identical content -
    # should dedupe by content hash to one SUPPORTED candidate, not choke or
    # misclassify just because several dict keys match case-insensitively.
    variants = {"factur-x.xml", "FACTUR-X.XML", "Factur-X.Xml", "xrechnung.xml", "XRECHNUNG.XML"}
    assert all(name.lower() in KNOWN_ATTACHMENT_NAMES for name in variants)

    result = discover_invoice_xml(_FakeReader({name: [_VALID_CII_XML] for name in variants}))

    assert result.status == XmlDiscoveryStatus.SUPPORTED


def test_too_many_distinctly_named_attachments_reject_rather_than_truncate() -> None:
    # More distinct known-name spellings than _MAX_KNOWN_NAMES_CHECKED allows -
    # truncating this list (rather than rejecting outright) could hide a
    # conflicting attachment under a name past the cutoff.
    base = "factur-x.xml"
    variants = {base}
    for index, char in enumerate(base):
        if char.isalpha():
            variants.add(base[:index] + char.swapcase() + base[index + 1 :])
    assert len(variants) > 10

    result = discover_invoice_xml(_FakeReader({name: [_VALID_CII_XML] for name in variants}))

    assert result.status == XmlDiscoveryStatus.AMBIGUOUS
    assert result.candidate is None


def test_no_candidate_attachment_is_ever_treated_as_a_filesystem_path() -> None:
    # Attachment names are untrusted PDF content - discover_invoice_xml must
    # only ever use one as a string label, never to open or write a real path.
    result = discover_invoice_xml(
        _FakeReader({"../../../../etc/factur-x.xml": [b"<not><valid/></not>"]})
    )

    assert result.status == XmlDiscoveryStatus.NONE  # not a known name, case-insensitively
    assert result.attachment_name is None
