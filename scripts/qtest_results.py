"""Parse qtest's authoritative XML result set and matching harness log."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ResultError(ValueError):
    pass


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNEXPECTED_PASS = "unexpected-pass"
    EXPECTED_FAIL = "expected-fail"


@dataclass(frozen=True)
class Result:
    suite: str
    category: str
    ordinal: int
    description: str
    outcome: Outcome

    @property
    def id(self) -> str:
        return f"{self.category} {self.ordinal}"

    @property
    def test(self) -> str:
        return self.category

    @property
    def subtest(self) -> str:
        return self.description

    @property
    def passed(self) -> bool:
        return self.outcome in (Outcome.PASS, Outcome.UNEXPECTED_PASS)


@dataclass(frozen=True)
class Summary:
    total: int
    passes: int
    failures: int
    unexpected_passes: int
    expected_failures: int


@dataclass(frozen=True)
class RunResults:
    results: tuple[Result, ...]
    summary: Summary
    invalid_suites: tuple[str, ...]


_LOG_RESULT_RE = re.compile(
    r"^(?P<category>[A-Za-z0-9][A-Za-z0-9_+.-]*)"
    r"(?:\s+test)?\s+(?P<ordinal>\d+)\s+"
    r"\((?P<description>.+)\)(?:\s+\.\.\.)?\s+"
    r"(?P<status>PASSED-UNEXP|PASSED|FAILED \(exp\)|FAILED)\s*$"
)
_ATTRIBUTE_NAME_RE = re.compile(rb"[A-Za-z_:][A-Za-z0-9_.:-]*")
_QTEST_BYTE_ENTITY_RUN_RE = re.compile(rb"(?:&#[xX][0-9A-Fa-f]+;)+")
_QTEST_BYTE_ENTITY_RE = re.compile(rb"&#[xX]([0-9A-Fa-f]+);")
_XML_ENTITY_RE = re.compile(r"&(?:#(?:[xX][0-9A-Fa-f]+|[0-9]+)|amp|apos|gt|lt|quot);")
_XML_WHITESPACE = b" \t\r\n"


@dataclass(frozen=True)
class _Counters:
    summary: Summary
    missing_cases: int
    extra_cases: int


@dataclass(frozen=True)
class _XmlCase:
    suite: str
    category: str
    ordinal: int
    description: str
    actual_outcome: Outcome

    @property
    def id(self) -> str:
        return f"{self.category} {self.ordinal}"


@dataclass(frozen=True)
class _LogCase:
    category: str
    ordinal: int
    description: str
    actual_outcome: Outcome
    outcome: Outcome

    @property
    def id(self) -> str:
        return f"{self.category} {self.ordinal}"


@dataclass(frozen=True)
class _DescriptionProvenance:
    testid: str
    description: str | None


def _counters(element: ET.Element, *, scope: str) -> _Counters:
    try:
        return _Counters(
            summary=Summary(
                total=int(element.attrib["total-cases"]),
                passes=int(element.attrib["passes"]),
                failures=int(element.attrib["failures"]),
                unexpected_passes=int(element.attrib["unexpected-passes"]),
                expected_failures=int(element.attrib["expected-failures"]),
            ),
            missing_cases=int(element.attrib["missing-cases"]),
            extra_cases=int(element.attrib["extra-cases"]),
        )
    except (KeyError, ValueError) as exc:
        raise ResultError(f"invalid {scope} summary") from exc


def _case_counters(cases: list[Result]) -> Summary:
    return Summary(
        total=len(cases),
        passes=sum(case.outcome is Outcome.PASS for case in cases),
        failures=sum(case.outcome is Outcome.FAIL for case in cases),
        unexpected_passes=sum(
            case.outcome is Outcome.UNEXPECTED_PASS for case in cases
        ),
        expected_failures=sum(
            case.outcome is Outcome.EXPECTED_FAIL for case in cases
        ),
    )


def _validate_summary(
    scope: str, counters: _Counters, cases: list[Result]
) -> None:
    actual = _case_counters(cases)
    expected = counters.summary
    for name, expected_value, actual_value in (
        ("total", expected.total, actual.total),
        ("passes", expected.passes, actual.passes),
        ("failures", expected.failures, actual.failures),
        ("unexpected passes", expected.unexpected_passes, actual.unexpected_passes),
        ("expected failures", expected.expected_failures, actual.expected_failures),
    ):
        if expected_value != actual_value:
            raise ResultError(
                f"{scope} {name} mismatch: summary {expected_value}, cases {actual_value}"
            )


def _validate_root_matches_children(
    root: _Counters, children: list[_Counters]
) -> None:
    child_summary = Summary(
        total=sum(child.summary.total for child in children),
        passes=sum(child.summary.passes for child in children),
        failures=sum(child.summary.failures for child in children),
        unexpected_passes=sum(
            child.summary.unexpected_passes for child in children
        ),
        expected_failures=sum(child.summary.expected_failures for child in children),
    )
    for name, root_value, child_value in (
        ("total", root.summary.total, child_summary.total),
        ("passes", root.summary.passes, child_summary.passes),
        ("failures", root.summary.failures, child_summary.failures),
        (
            "unexpected passes",
            root.summary.unexpected_passes,
            child_summary.unexpected_passes,
        ),
        (
            "expected failures",
            root.summary.expected_failures,
            child_summary.expected_failures,
        ),
        (
            "missing cases",
            root.missing_cases,
            sum(child.missing_cases for child in children),
        ),
        (
            "extra cases",
            root.extra_cases,
            sum(child.extra_cases for child in children),
        ),
    ):
        if root_value != child_value:
            raise ResultError(
                f"root {name} mismatch: root {root_value}, children {child_value}"
            )


def _decode_xml_attribute(value: bytes) -> str:
    """Decode an XML attribute value without applying qtest byte restoration."""
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResultError(f"malformed XML attribute: {exc}") from exc

    def decode_entity(match: re.Match[str]) -> str:
        entity = match.group()
        if entity == "&amp;":
            return "&"
        if entity == "&apos;":
            return "'"
        if entity == "&gt;":
            return ">"
        if entity == "&lt;":
            return "<"
        if entity == "&quot;":
            return '"'
        number = entity[2:-1]
        base = 16 if number[:1].lower() == "x" else 10
        try:
            return chr(int(number[1:] if base == 16 else number, base))
        except ValueError:
            return entity

    return _XML_ENTITY_RE.sub(decode_entity, text)


def _restore_qtest_description(value: bytes) -> str | None:
    """Decode qtest's high-byte entities in one description attribute."""
    restored: list[str] = []
    position = 0
    changed = False
    for match in _QTEST_BYTE_ENTITY_RUN_RE.finditer(value):
        restored.append(_decode_xml_attribute(value[position : match.start()]))
        encoded = match.group()
        values = [
            int(number, 16) for number in _QTEST_BYTE_ENTITY_RE.findall(encoded)
        ]
        if all(0x7F <= number <= 0xFF for number in values):
            try:
                restored.append(bytes(values).decode("utf-8"))
                changed = True
            except UnicodeDecodeError:
                restored.append(_decode_xml_attribute(encoded))
        else:
            restored.append(_decode_xml_attribute(encoded))
        position = match.end()
    if not changed:
        return None
    restored.append(_decode_xml_attribute(value[position:]))
    return "".join(restored)


def _testcase_tag_end(data: bytes, start: int) -> int | None:
    quote: int | None = None
    for position in range(start + len(b"<testcase"), len(data)):
        byte = data[position]
        if quote is not None:
            if byte == quote:
                quote = None
        elif byte in b"\"'":
            quote = byte
        elif byte == ord(">"):
            return position
    return None


def _testcase_attributes(tag: bytes) -> dict[bytes, bytes]:
    position = len(b"<testcase")
    attributes: dict[bytes, bytes] = {}
    while position < len(tag):
        while position < len(tag) and tag[position] in _XML_WHITESPACE:
            position += 1
        if position == len(tag) or tag[position] in b"/>":
            return attributes
        name = _ATTRIBUTE_NAME_RE.match(tag, position)
        if name is None:
            raise ResultError("malformed testcase provenance")
        position = name.end()
        while position < len(tag) and tag[position] in _XML_WHITESPACE:
            position += 1
        if position == len(tag) or tag[position] != ord("="):
            raise ResultError("malformed testcase provenance")
        position += 1
        while position < len(tag) and tag[position] in _XML_WHITESPACE:
            position += 1
        if position == len(tag) or tag[position] not in b"\"'":
            raise ResultError("malformed testcase provenance")
        quote = tag[position]
        value_start = position + 1
        value_end = tag.find(bytes((quote,)), value_start)
        if value_end == -1:
            raise ResultError("malformed testcase provenance")
        attributes[name.group()] = tag[value_start:value_end]
        position = value_end + 1
    raise ResultError("malformed testcase provenance")


def _collect_description_provenance(
    xml_path: Path, *, chunk_size: int = 64 * 1024
) -> list[_DescriptionProvenance]:
    """Collect only testcase description metadata without copying the XML file."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    cases: list[_DescriptionProvenance] = []
    marker = b"<testcase"
    buffer = b""
    try:
        with xml_path.open("rb") as xml:
            while chunk := xml.read(chunk_size):
                buffer += chunk
                position = 0
                while True:
                    start = buffer.find(marker, position)
                    if start == -1:
                        buffer = buffer[-(len(marker) - 1) :]
                        break
                    after_marker = start + len(marker)
                    if after_marker == len(buffer):
                        buffer = buffer[start:]
                        break
                    if buffer[after_marker] not in _XML_WHITESPACE + b"/>":
                        position = after_marker
                        continue
                    end = _testcase_tag_end(buffer, start)
                    if end is None:
                        buffer = buffer[start:]
                        break
                    attributes = _testcase_attributes(buffer[start : end + 1])
                    raw_description = attributes.get(b"description")
                    cases.append(
                        _DescriptionProvenance(
                            testid=_decode_xml_attribute(
                                attributes.get(b"testid", b"")
                            ),
                            description=(
                                _restore_qtest_description(raw_description)
                                if raw_description is not None
                                else None
                            ),
                        )
                    )
                    position = end + 1
    except OSError as exc:
        raise ResultError(f"malformed XML: {exc}") from exc
    return cases


def _parse_xml(xml_path: Path) -> ET.Element:
    try:
        return ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise ResultError(f"malformed XML: {exc}") from exc


def _apply_description_provenance(
    root: ET.Element, provenance: list[_DescriptionProvenance]
) -> None:
    cases = list(root.iter("testcase"))
    if len(cases) != len(provenance):
        raise ResultError("XML testcase provenance mismatch: testcase count")
    for position, (case, raw_case) in enumerate(zip(cases, provenance), start=1):
        if case.attrib.get("testid", "") != raw_case.testid:
            raise ResultError(
                "XML testcase provenance mismatch: "
                f"testid at position {position}"
            )
        if raw_case.description is not None:
            if "description" not in case.attrib:
                raise ResultError(
                    "XML testcase provenance mismatch: "
                    f"description at position {position}"
                )
            case.attrib["description"] = raw_case.description


def _parse_xml_case(case: ET.Element, suite: str) -> _XmlCase:
    testid = case.attrib.get("testid", "")
    category, separator, ordinal_text = testid.rpartition(" ")
    if not separator or not category or not ordinal_text.isdecimal():
        raise ResultError(f"invalid testid: {testid!r}")
    try:
        actual_outcome = Outcome(case.attrib["outcome"])
        description = case.attrib["description"]
    except (KeyError, ValueError) as exc:
        raise ResultError(f"invalid testcase {testid!r}") from exc
    if actual_outcome not in (Outcome.PASS, Outcome.FAIL):
        raise ResultError(f"invalid actual outcome for {testid!r}")
    return _XmlCase(
        suite=suite,
        category=category,
        ordinal=int(ordinal_text),
        description=description,
        actual_outcome=actual_outcome,
    )


def _parse_log_case(match: re.Match[str]) -> _LogCase:
    actual_outcome, outcome = {
        "PASSED": (Outcome.PASS, Outcome.PASS),
        "FAILED": (Outcome.FAIL, Outcome.FAIL),
        "FAILED (exp)": (Outcome.FAIL, Outcome.EXPECTED_FAIL),
        "PASSED-UNEXP": (Outcome.PASS, Outcome.UNEXPECTED_PASS),
    }[match["status"]]
    return _LogCase(
        category=match["category"],
        ordinal=int(match["ordinal"]),
        description=match["description"].strip(),
        actual_outcome=actual_outcome,
        outcome=outcome,
    )


def parse_run(log_path: Path, xml_path: Path) -> RunResults:
    """Join the harness log to the XML result set from one qtest invocation."""
    provenance = _collect_description_provenance(xml_path)
    root = _parse_xml(xml_path)
    if root.tag != "qtest-results":
        raise ResultError(f"malformed XML: unexpected root {root.tag!r}")
    _apply_description_provenance(root, provenance)

    root_summaries = root.findall("./testsummary")
    if len(root_summaries) != 1:
        raise ResultError("invalid root summary")
    root_counters = _counters(root_summaries[0], scope="root")

    xml_results: dict[str, _XmlCase] = {}
    invalid_ids: set[str] = set()
    invalid_suites: list[str] = []
    child_summaries: list[tuple[str, _Counters, list[_XmlCase]]] = []

    for suite in root.findall("testsuite"):
        try:
            suite_name = Path(suite.attrib["file"]).stem
        except KeyError as exc:
            raise ResultError("invalid testsuite") from exc
        summaries = suite.findall("./testsummary")
        if not summaries:
            invalid_suites.append(suite_name)
            invalid_ids.update(
                case.attrib.get("testid", "") for case in suite.findall("./testcase")
            )
            continue
        if len(summaries) != 1:
            raise ResultError(f"invalid child summary for {suite_name}")
        cases = [
            _parse_xml_case(case, suite_name)
            for case in suite.findall("./testcase")
        ]
        counters = _counters(summaries[0], scope=f"child {suite_name}")
        child_summaries.append((suite_name, counters, cases))
        for case in cases:
            if case.id in xml_results:
                raise ResultError(f"duplicate XML testid: {case.id}")
            xml_results[case.id] = case

    log_results: dict[str, _LogCase] = {}
    try:
        with log_path.open(encoding="utf-8", errors="replace") as log:
            for raw_line in log:
                match = _LOG_RESULT_RE.match(raw_line.rstrip("\n"))
                if match is None:
                    continue
                record = _parse_log_case(match)
                if record.id in invalid_ids and record.id not in xml_results:
                    continue
                previous = log_results.get(record.id)
                if previous is not None:
                    if previous != record:
                        raise ResultError(f"conflicting log identity: {record.id}")
                    continue
                log_results[record.id] = record
    except OSError as exc:
        raise ResultError(f"unable to read harness log: {exc}") from exc

    xml_ids = set(xml_results)
    log_ids = set(log_results)
    if xml_ids != log_ids:
        xml_only = sorted(xml_ids - log_ids)
        log_only = sorted(log_ids - xml_ids)
        raise ResultError(
            "XML/log identity drift: "
            f"XML-only={xml_only!r}, log-only={log_only!r}"
        )

    parsed: list[Result] = []
    for identity, xml_case in xml_results.items():
        log_case = log_results[identity]
        if xml_case.description != log_case.description:
            raise ResultError(f"description mismatch for {identity}")
        if xml_case.actual_outcome is not log_case.actual_outcome:
            raise ResultError(f"actual outcome mismatch for {identity}")
        parsed.append(
            Result(
                suite=xml_case.suite,
                category=xml_case.category,
                ordinal=xml_case.ordinal,
                description=xml_case.description,
                outcome=log_case.outcome,
            )
        )

    results_by_id = {result.id: result for result in parsed}
    child_counters: list[_Counters] = []
    for suite_name, counters, cases in child_summaries:
        _validate_summary(
            f"child {suite_name}",
            counters,
            [results_by_id[case.id] for case in cases],
        )
        child_counters.append(counters)
    _validate_summary("root", root_counters, parsed)
    _validate_root_matches_children(root_counters, child_counters)

    return RunResults(
        results=tuple(
            sorted(parsed, key=lambda result: (result.category, result.ordinal))
        ),
        summary=root_counters.summary,
        invalid_suites=tuple(invalid_suites),
    )
