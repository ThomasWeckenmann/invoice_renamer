"""Finds and classifies an embedded ZUGFeRD/Factur-X invoice XML attachment in a PDF,
without reading page text, running OCR, or mapping any field values.

Supported format matrix (first implementation - see docs/plans_open/05_zugferd-extraction.md
for the verification this matrix was checked against): CII (UN/CEFACT Cross Industry
Invoice) D16B as embedded by Factur-X 1.0 / ZUGFeRD 2.x, root element
{urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100}CrossIndustryInvoice, EN16931
("COMFORT") profile only (urn:cen.eu:en16931:2017). MINIMUM, BASIC WL, BASIC, EXTENDED,
and XRechnung CIUS profiles are recognized but classified unsupported. UBL invoices,
standalone XML imports, and older ZUGFeRD 1.x (CII D16A-based) documents are out of scope.
"""

import hashlib
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import cast
from xml.etree.ElementTree import Element
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml.ElementTree import iterparse as defused_iterparse
from pypdf import PdfReader

# Standard attachment filenames used by the ZUGFeRD/Factur-X specs, checked
# case-insensitively.
KNOWN_ATTACHMENT_NAMES = {
    "zugferd-invoice.xml",
    "factur-x.xml",
    "xrechnung.xml",
}

# Bounds how many distinct (post case-fold) attachment names we'll even ask pypdf to
# decompress. Cheap to keep low: there are only 3 known names, so this only matters
# against a PDF that spells them with many different casings.
_MAX_KNOWN_NAMES_CHECKED = 10

# Bounds how many same-named revisions are considered per name. pypdf groups every
# attachment sharing one exact name into a single dict entry as a list of revisions
# (see the comment in discover_invoice_xml below) - this caps how many of those we
# compare for ambiguity, not just how many bytes each one can be.
_MAX_REVISIONS_PER_NAME = 5

# A real CII invoice, even with several line items, is tens of KB. Reject anything
# past this before parsing.
_MAX_XML_ATTACHMENT_BYTES = 10 * 1024 * 1024

# Real CII invoices nest a handful of levels deep (single digits). This is generous
# headroom while still rejecting a pathologically nested payload in milliseconds via
# the incremental parse below, instead of paying to fully parse and tree-build it.
_MAX_XML_DEPTH = 50

_RSM_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_RAM_NS = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_UDT_NS = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
NAMESPACES = {"rsm": _RSM_NS, "ram": _RAM_NS, "udt": _UDT_NS}

_ROOT_TAG = f"{{{_RSM_NS}}}CrossIndustryInvoice"
_PROFILE_PATH = "rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID"
SUPPORTED_PROFILE_IDS = {"urn:cen.eu:en16931:2017"}

_MAX_WARNING_DETAIL_CHARS = 200


class XmlDiscoveryStatus(str, Enum):
    NONE = "none"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class XmlCandidate:
    """A single supported invoice XML attachment, parsed and ready for field mapping."""

    attachment_name: str
    raw_bytes: bytes
    root: Element
    profile_id: str


@dataclass(frozen=True)
class XmlDiscoveryResult:
    status: XmlDiscoveryStatus
    # Only set when status is SUPPORTED.
    candidate: XmlCandidate | None
    # Best-known single attachment name, for metrics/UI - set whenever exactly one
    # distinct candidate was found (supported, unsupported, or invalid), None for
    # NONE and AMBIGUOUS (where no single attachment can be pointed to truthfully).
    attachment_name: str | None
    # Detected profile id, when the candidate parsed far enough to read one.
    profile_id: str | None
    # A single concise, actionable reason the status isn't SUPPORTED. Never contains
    # attachment content - only structural facts (tag names, byte counts, profile ids).
    warning: str | None


def _truncate(text: str) -> str:
    return (
        text if len(text) <= _MAX_WARNING_DETAIL_CHARS else f"{text[:_MAX_WARNING_DETAIL_CHARS]}..."
    )


def _no_candidate() -> XmlDiscoveryResult:
    return XmlDiscoveryResult(
        status=XmlDiscoveryStatus.NONE,
        candidate=None,
        attachment_name=None,
        profile_id=None,
        warning=None,
    )


def _invalid(name: str, reason: str) -> XmlDiscoveryResult:
    return XmlDiscoveryResult(
        status=XmlDiscoveryStatus.INVALID,
        candidate=None,
        attachment_name=name,
        profile_id=None,
        warning=f"{name!r} {reason}",
    )


def _unsupported(name: str, reason: str, *, profile_id: str | None = None) -> XmlDiscoveryResult:
    return XmlDiscoveryResult(
        status=XmlDiscoveryStatus.UNSUPPORTED,
        candidate=None,
        attachment_name=name,
        profile_id=profile_id,
        warning=f"{name!r} {reason}",
    )


def _too_many(reason: str) -> XmlDiscoveryResult:
    # AMBIGUOUS, not INVALID: exceeding a count cap means we can no longer prove
    # there's only one candidate invoice, which is exactly what AMBIGUOUS means
    # elsewhere in this module - never silently proceed on a truncated view.
    return XmlDiscoveryResult(
        status=XmlDiscoveryStatus.AMBIGUOUS,
        candidate=None,
        attachment_name=None,
        profile_id=None,
        warning=f"{reason}; skipping XML extraction",
    )


class _XmlTooDeep(Exception):
    pass


def _parse_bounded(data: bytes) -> Element:
    """Parses untrusted XML bytes into an Element tree, forbidding DTDs (so no
    entity expansion or external references are even reachable) and aborting as
    soon as nesting exceeds _MAX_XML_DEPTH - before paying to parse and tree-build
    the rest of a pathologically nested payload.

    Deliberately catches nothing here: every exception this can raise (ParseError
    for malformed XML, defusedxml's DTD/entity/external-reference exceptions,
    _XmlTooDeep, or anything else a hostile encoding declaration or byte sequence
    provokes from the underlying codec/parser, e.g. LookupError or
    UnicodeDecodeError) is the caller's job to treat uniformly as "not usable
    invoice XML."
    """
    depth = 0
    root: Element | None = None
    for event, elem in defused_iterparse(BytesIO(data), events=("start", "end"), forbid_dtd=True):
        if event == "start":
            depth += 1
            if depth > _MAX_XML_DEPTH:
                raise _XmlTooDeep(f"exceeds maximum XML nesting depth ({_MAX_XML_DEPTH})")
            if root is None:
                root = cast(Element, elem)
        else:
            depth -= 1
    if root is None:
        raise XmlParseError("no element found")
    return root


def discover_invoice_xml(reader: PdfReader) -> XmlDiscoveryResult:
    # reader.attachments is a LazyDict: iterating its keys (via `for name in mapping`)
    # only lists attachment names cheaply. Decompression happens per key, inside
    # pypdf, the moment that key's value is first accessed - and it decompresses
    # *every* attachment sharing that exact name in one call, not just one (pypdf
    # groups multiple attachment objects that share one exact name into a single
    # dict entry, as a list of revisions). We can't bound that decompression before
    # it happens (no lower-level pypdf API to preview a stream's size first), so we
    # bound what we control instead: only known names are ever looked up. Exceeding
    # either cap below rejects outright rather than silently checking only a
    # truncated subset - a truncated view could hide a conflicting attachment we
    # never even looked at, which defeats the ambiguity check further down.
    mapping = reader.attachments
    matching_names = [name for name in mapping if name.lower() in KNOWN_ATTACHMENT_NAMES]
    if not matching_names:
        return _no_candidate()
    if len(matching_names) > _MAX_KNOWN_NAMES_CHECKED:
        return _too_many(
            f"found {len(matching_names)} distinctly-named invoice XML attachments, more "
            "than can be safely checked for conflicts"
        )

    raw_candidates: list[tuple[str, bytes]] = []
    for name in matching_names:
        revisions = mapping[name]
        if len(revisions) > _MAX_REVISIONS_PER_NAME:
            return _too_many(
                f"{name!r} has {len(revisions)} attachment revisions, more than can be "
                "safely checked for conflicts"
            )
        for data in revisions:
            if data:
                raw_candidates.append((name, data))

    if not raw_candidates:
        return _no_candidate()

    distinct_by_hash: dict[bytes, tuple[str, bytes]] = {}
    for name, data in raw_candidates:
        distinct_by_hash.setdefault(hashlib.sha256(data).digest(), (name, data))

    if len(distinct_by_hash) > 1:
        names = ", ".join(sorted({name for name, _ in distinct_by_hash.values()}))
        return XmlDiscoveryResult(
            status=XmlDiscoveryStatus.AMBIGUOUS,
            candidate=None,
            attachment_name=None,
            profile_id=None,
            warning=(
                f"multiple distinct invoice XML attachments found ({names}); "
                "skipping XML extraction"
            ),
        )

    name, data = next(iter(distinct_by_hash.values()))

    if len(data) > _MAX_XML_ATTACHMENT_BYTES:
        return _invalid(name, f"exceeds the {_MAX_XML_ATTACHMENT_BYTES}-byte limit for invoice XML")

    try:
        root = _parse_bounded(data)
    except Exception as error:
        # Untrusted external XML can fail parsing in more ways than the "normal"
        # ParseError/DefusedXmlException pair - an unrecognized XML declaration
        # encoding raises a plain LookupError, for one. Any of them means this
        # attachment isn't usable invoice XML; none of them should crash the job.
        return _invalid(name, f"is not valid XML: {_truncate(str(error))}")

    if root.tag != _ROOT_TAG:
        return _unsupported(name, f"has an unsupported root element {root.tag!r}")

    profile_elements = root.findall(_PROFILE_PATH, NAMESPACES)
    if len(profile_elements) > 1:
        return _invalid(name, "declares more than one conflicting invoice profile")
    profile_id = (profile_elements[0].text or "").strip() if profile_elements else None

    if profile_id not in SUPPORTED_PROFILE_IDS:
        return _unsupported(
            name, f"uses an unsupported invoice profile {profile_id!r}", profile_id=profile_id
        )

    return XmlDiscoveryResult(
        status=XmlDiscoveryStatus.SUPPORTED,
        candidate=XmlCandidate(
            attachment_name=name, raw_bytes=data, root=root, profile_id=profile_id
        ),
        attachment_name=name,
        profile_id=profile_id,
        warning=None,
    )
