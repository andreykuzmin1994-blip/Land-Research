#!/usr/bin/env python3
"""Reject env.template contents that look like real credentials (SR-12).

The ONE rule engine of the env.template guard. The pre-commit hook runs it
on the STAGED blob (stdin) and the check-env-template CI workflow runs it
on the checked-out file, so the two cannot drift:

    python3 -I .githooks/check_env_template.py env.template
    git cat-file blob :env.template | python3 -I .githooks/check_env_template.py -
    python3 -I .githooks/check_env_template.py --missing    # staged deletion

Rules (allowlists are exact tokens taken from the current env.template;
no "looks like a placeholder" shape rules):
  * an uncommented DATABASE_URL= line must exist and hold a
    scheme://USER:SLOT@HOST/... URL;
  * every scheme://USER:SLOT@ URL on ANY line (any key, comments too) must
    have SLOT in DB_SLOT_PLACEHOLDERS; a URL with several '@', or with no
    USER:SLOT before its '@', fails;
  * every password= / sslpassword= parameter must hold a DB placeholder;
  * every assignment -- commented-out, exported, quoted, any case, or a
    later NAME=value on the same line -- whose name contains KEY, TOKEN,
    SECRET or PASS must hold a SECRET_PLACEHOLDERS value;
  * a lone CR, VT, FF, NEL or U+2028 starts a new line for these rules;
  * provider-token prefixes and private-key headers fail anywhere;
  * a line that is not blank, a comment or NAME=VALUE fails;
  * an empty or missing file fails.

It NEVER prints a value or any part of one: a finding carries the line
number, plus the key name only when that is a plain upper-case identifier.
Exit status: 0 clean, 1 findings, 2 usage or internal error.
Stdlib only, Python 3.8+ syntax: the hook runs on developer machines.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import List, Optional, Tuple

DB_SLOT_PLACEHOLDERS = frozenset({"URL_ENCODED_PASSWORD", "CHANGE_ME", "PASSWORD", ""})
SECRET_PLACEHOLDERS = frozenset({"", "CHANGE_ME"})
SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS")
REQUIRED_NAME = "DATABASE_URL"
DEFAULT_LABEL = "env.template"

# `[# ...][export ]NAME = value`, any leading whitespace.
_ASSIGNMENT = re.compile(
    r"^\s*(?P<hash>#[\s#]*)?(?:export\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")
# NAME=value anywhere on a line: `A=1; B=2`, `A=1 B=2`, `# old: B=2`, URL
# query parameters and libpq key=value DSNs (?password=, password = x). The
# value is a lookahead so a match never swallows a NAME= nested in a value.
_INLINE_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9_])(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*="
    r"(?=\s*(?P<value>\"[^\"]*\"|'[^']*'|[^\s&;]*))")
_PASSWORD_PARAMS = ("PASSWORD", "SSLPASSWORD")
# Bare prefixes only (no example tokens), plus PEM private-key headers.
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:sk-ant-|ghp_|gho_|github_pat_|xox[abposr]-|AKIA|eyJ)"
    r"[A-Za-z0-9_\-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY")
# Only names like this are ever echoed (a malformed line's "name" can be
# secret material).
_PRINTABLE_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")

MSG_EMPTY = "file is empty"
MSG_MISSING = "file is missing"
MSG_NO_DATABASE_URL = "no uncommented DATABASE_URL= line"
MSG_DATABASE_URL_FORM = "must be a scheme://USER:PLACEHOLDER@HOST/... URL"
MSG_SECRET_NAME = ("secret-like key holds a value that is not an allowed "
                   "placeholder (empty or CHANGE_ME)")
MSG_URL_SLOT = ("URL password slot is not an allowed placeholder "
                "(URL_ENCODED_PASSWORD, CHANGE_ME, PASSWORD or empty)")
MSG_URL_SHAPE = ("URL credentials are not in USER:PLACEHOLDER@HOST form "
                 "(several '@', or no ':' before the '@')")
MSG_PASSWORD_PARAM = ("password parameter is not an allowed placeholder "
                      "(URL_ENCODED_PASSWORD, CHANGE_ME, PASSWORD or empty)")
MSG_INLINE_SECRET = ("a secret-like NAME=value inside the line holds a value "
                     "that is not an allowed placeholder (empty or CHANGE_ME)")
MSG_TOKEN = "looks like a provider token or a private key"
MSG_UNPARSEABLE = "line is not blank, a comment or NAME=VALUE"

Finding = Tuple[int, Optional[str], str]   # (line number or 0, name, message)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _printable(name: Optional[str]) -> Optional[str]:
    if name and _PRINTABLE_NAME.match(name) and not _TOKEN.search(name):
        return name
    return None


def _url_problems(line: str) -> List[str]:
    """Check every '://' on the line; the userinfo runs up to its '@'."""
    problems = []
    starts = [m.start() for m in re.finditer("://", line)] + [len(line)]
    for begin, end in zip(starts, starts[1:]):
        rest = line[begin + 3:end]
        if "@" not in rest:
            continue
        userinfo = rest.split("@", 1)[0]
        if rest.count("@") > 1 or ":" not in userinfo:
            problems.append(MSG_URL_SHAPE)
        elif userinfo.split(":", 1)[1] not in DB_SLOT_PLACEHOLDERS:
            problems.append(MSG_URL_SLOT)
    return problems


def _secret_like(name: str) -> bool:
    return any(part in name.upper() for part in SECRET_NAME_PARTS)


def _line_problems(line: str) -> Tuple[Optional[str], List[str]]:
    """(name of the assignment, if any; problems) for one line."""
    problems = _url_problems(line)
    if _TOKEN.search(line):
        problems.append(MSG_TOKEN)

    assignment = _ASSIGNMENT.match(line)
    for inline in _INLINE_ASSIGNMENT.finditer(line):
        if assignment and inline.start("name") == assignment.start("name"):
            continue    # the line's own assignment: checked whole, below
        inline_name = inline.group("name").upper()
        inline_value = _unquote(inline.group("value"))
        if inline_name in _PASSWORD_PARAMS:
            if inline_value not in DB_SLOT_PLACEHOLDERS:
                problems.append(MSG_PASSWORD_PARAM)
        elif _secret_like(inline_name) and inline_value not in SECRET_PLACEHOLDERS:
            problems.append(MSG_INLINE_SECRET)

    if assignment is None:
        if line.strip() and not line.lstrip().startswith("#"):
            problems.append(MSG_UNPARSEABLE)
        return None, problems

    name = assignment.group("name")
    value = _unquote(assignment.group("value"))
    if _secret_like(name) and value not in SECRET_PLACEHOLDERS:
        problems.append(MSG_SECRET_NAME)
    if name == REQUIRED_NAME and not assignment.group("hash"):
        scheme_rest = value.split("://", 1)
        if len(scheme_rest) != 2 or "@" not in scheme_rest[1]:
            problems.append(MSG_DATABASE_URL_FORM)
    return name, problems


def check(data: Optional[bytes]) -> List[Finding]:
    """All findings for env.template bytes (None = the file is missing)."""
    if data is None:
        return [(0, None, MSG_MISSING)]
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    text = data.decode("utf-8", "replace")
    if not text.strip():
        return [(0, None, MSG_EMPTY)]

    findings: List[Finding] = []
    has_database_url = False
    for lineno, physical in enumerate(text.split("\n"), 1):
        # Numbered as git counts (LF); a CRLF's CR just goes, and a lone
        # CR, VT, FF, NEL or U+2028 splits it further, as some readers do.
        for line in physical.splitlines():
            name, problems = _line_problems(line)
            if name == REQUIRED_NAME and not line.lstrip().startswith("#"):
                has_database_url = True
            for message in problems:
                finding = (lineno, _printable(name), message)
                if finding not in findings:
                    findings.append(finding)
    if not has_database_url:
        findings.append((0, None, MSG_NO_DATABASE_URL))
    return findings


def _github_escape(text: str, prop: bool = False) -> str:
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    if prop:
        text = text.replace(":", "%3A").replace(",", "%2C")
    return text


def _render(label: str, finding: Finding, github: bool) -> str:
    lineno, name, message = finding
    subject = (name + ": " if name else "") + message
    if github:
        where = "file=" + _github_escape(label, prop=True)
        if lineno:
            where += ",line=%d" % lineno
        return "::error %s::%s" % (where, _github_escape(subject))
    return ("%s:%d: " % (label, lineno) if lineno else label + ": ") + subject


def _main(argv: Optional[List[str]]) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if env.template holds anything but placeholders. "
                    "Never prints a value.")
    parser.add_argument("path", nargs="?",
                        help="env.template to check, or - to read stdin")
    parser.add_argument("--missing", action="store_true",
                        help="the file is absent (e.g. a staged deletion): fail")
    parser.add_argument("--github", action="store_true",
                        help="emit GitHub Actions ::error annotations")
    args = parser.parse_args(argv)
    if args.missing == (args.path is not None):
        parser.error("give exactly one of PATH, - or --missing")

    label = DEFAULT_LABEL if args.path in (None, "-") else args.path
    data: Optional[bytes]
    if args.missing:
        data = None
    elif args.path == "-":
        data = sys.stdin.buffer.read()
    else:
        try:
            with open(args.path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            data = None

    findings = check(data)
    if not findings:
        return 0
    for finding in findings:
        print(_render(label, finding, args.github))
    print("%s: %d problem(s); values are never printed. Real credentials "
          "belong only in the gitignored .env file." % (label, len(findings)))
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return _main(argv)
    except SystemExit:
        raise
    except BaseException:
        # Never let a traceback or exception message carry file contents.
        sys.stderr.write("check_env_template: internal error while checking "
                         "env.template; failing closed.\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
