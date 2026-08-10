#!/usr/bin/env python3
"""
syntax_legal_cli.py — Convene the Syntax Legal Mind from the terminal.

The Legal Mind is the Syntax-facing replacement for the old OutClaw entry
point. The audit boundary remains ``SyntaxSwarm.convene_legal``; this module
only handles input and presentation.

The default renderer is a small, dependency-free TTY UI: black, white, and a
single red accent, with no animation or terminal control sequences that would
make logs unusable. ``--raw`` (or a non-TTY stdout) keeps the compact,
pipe-friendly digest for scripts and mobile shells.

Usage:
    syntax legal "text with citations"
    python3 SyntaxIntelligence/syntax_legal_cli.py --file brief.txt
    echo "text" | python3 SyntaxIntelligence/syntax_legal_cli.py
    syntax legal --raw "text"       # stable, unstyled output
    syntax legal --llm "text"       # enable the optional LLM fallback layer
    syntax legal --boardroom "text"  # request Vertical AI oversight guidance

Exit codes: 0 = audit produced, 1 = no text / audit failed / unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

# Run as a script: sys.path[0] is this file's dir, not the repo root.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class _Ink:
    """Small ANSI palette; deliberately avoids a third-party terminal lib."""

    RED = "\033[31m"
    BRIGHT_RED = "\033[91m"
    WHITE = "\033[97m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def _supports_color(stream: TextIO) -> bool:
    """Return whether styling is safe for this stream."""
    return bool(
        hasattr(stream, "isatty")
        and stream.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM", "dumb") != "dumb"
    )


def _tone(value: object, style: str, enabled: bool) -> str:
    """Apply one ANSI style when enabled, otherwise return plain text."""
    text = str(value)
    return f"{style}{text}{_Ink.RESET}" if enabled else text


def _rule(width: int = 58, *, enabled: bool = False) -> str:
    """Render the quiet red divider used by the terminal dossier."""
    return _tone("─" * width, _Ink.RED, enabled)


def _label(name: str, *, enabled: bool) -> str:
    """Pad before coloring so invisible ANSI bytes cannot shift alignment."""
    return _tone(name.ljust(18), _Ink.RED, enabled)


def _read_input(args: argparse.Namespace) -> str:
    """Resolve the text to audit from --file, a positional arg, or stdin."""
    if args.file:
        path = Path(args.file)
        if not path.exists():
            sys.stderr.write(f"File not found: {args.file}\n")
            raise SystemExit(1)
        return path.read_text(encoding="utf-8", errors="replace")
    if args.text:
        return args.text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _print_raw_digest(
    offered: dict[str, Any], result: dict[str, Any] | None, stream: TextIO,
) -> int:
    """Render the stable, unstyled digest used by pipes and automation."""
    status = (result or {}).get("status", "no_result")
    digest = (result or {}).get("digest") or {}
    audit_completed = status == "completed" and bool(digest)
    display_status = status if audit_completed else ("audit_incomplete" if status == "completed" else status)
    print(f"Legal Mind convened → adapter status: {display_status}", file=stream)
    task_id = offered.get("task_id")
    if task_id:
        print(f"  task_id: {task_id}  offer: {offered.get('status')}", file=stream)

    if not result:
        print("  no adapter result (OutClaw did not claim the task)", file=stream)
        return 1

    digest = result.get("digest") or {}
    if digest:
        print(f"  audit_id: {digest.get('audit_id')}", file=stream)
        print(
            f"  severity_counts: {json.dumps(digest.get('severity_counts', {}))}",
            file=stream,
        )
        print(f"  safe_to_draft: {digest.get('safe_to_draft')}", file=stream)
        print(f"  high_count: {digest.get('high_count')}", file=stream)
        for finding in digest.get("high_findings", [])[:5]:
            print(
                f"    [HIGH] rule={finding.get('rule')}  "
                f"fp={finding.get('citation_fp')}  "
                f"excerpt={finding.get('excerpt')!r}",
                file=stream,
            )
    else:
        print(f"  reason: {result.get('reason')}", file=stream)

    return 0 if audit_completed else 1


def _print_tui_digest(
    offered: dict[str, Any], result: dict[str, Any] | None, *,
    stream: TextIO, source: str = "stdin / argument", enabled: bool = True,
) -> int:
    """Render a compact terminal dossier for a human operator."""
    status = (result or {}).get("status", "no_result")
    digest = (result or {}).get("digest") or {}
    audit_completed = status == "completed" and bool(digest)
    status_word = "CLEARED" if audit_completed else ("AUDIT INCOMPLETE" if status == "completed" else status.replace("_", " ").upper())
    status_style = _Ink.BRIGHT_RED if not audit_completed else _Ink.WHITE
    task_id = offered.get("task_id") or "not issued"

    print(file=stream)
    print(_tone("  SYNTAX // LEGAL MIND", _Ink.BOLD + _Ink.WHITE, enabled), file=stream)
    print(_tone("  CITATION INTEGRITY DOSSIER", _Ink.DIM, enabled), file=stream)
    print(_rule(enabled=enabled), file=stream)
    print(f"  {_label('CASE', enabled=enabled)} {source}", file=stream)
    print(f"  {_label('TASK', enabled=enabled)} {task_id}", file=stream)
    print(f"  {_label('ADAPTER', enabled=enabled)} Syntax / Legal Mind", file=stream)
    print(_rule(enabled=enabled), file=stream)
    print(f"  {_label('FINDINGS', enabled=enabled)} {_tone(status_word, status_style, enabled)}", file=stream)

    if not result:
        print("  no result — the Legal Mind did not claim the task", file=stream)
        print(_rule(enabled=enabled), file=stream)
        return 1

    digest = result.get("digest") or {}
    if digest:
        counts = digest.get("severity_counts", {})
        high_count = digest.get("high_count", 0)
        safe_to_draft = digest.get("safe_to_draft")
        print(f"  {_label('AUDIT ID', enabled=enabled)} {digest.get('audit_id')}", file=stream)
        print(f"  {_label('SEVERITY', enabled=enabled)} {json.dumps(counts, sort_keys=True)}", file=stream)
        print(f"  {_label('HIGH RISK', enabled=enabled)} {high_count}", file=stream)
        print(f"  {_label('DRAFT GATE', enabled=enabled)} {safe_to_draft}", file=stream)

        findings = digest.get("high_findings", [])[:5]
        if findings:
            print(_rule(enabled=enabled), file=stream)
            print(f"  {_tone('HIGH-SEVERITY REVIEW', _Ink.BOLD + _Ink.WHITE, enabled)}", file=stream)
            for finding in findings:
                rule = finding.get("rule", "unknown")
                fingerprint = finding.get("citation_fp", "n/a")
                excerpt = finding.get("excerpt", "")
                print(
                    f"  {_tone('[HIGH]', _Ink.BRIGHT_RED, enabled)} "
                    f"{rule}  ·  {fingerprint}  ·  {excerpt!r}",
                    file=stream,
                )
    else:
        print(f"  {_label('NOTE', enabled=enabled)} {result.get('reason')}", file=stream)

    print(_rule(enabled=enabled), file=stream)
    if status != "completed":
        verdict = "AUDIT INCOMPLETE"
    elif not digest:
        verdict = "AUDIT INCOMPLETE"
    elif not digest.get("safe_to_draft", False):
        verdict = "DO NOT DRAFT"
    else:
        verdict = "REVIEWED"
    print(
        f"  {_label('VERDICT', enabled=enabled)} "
        f"{_tone(verdict, status_style, enabled)}",
        file=stream,
    )
    print(file=stream)
    return 0 if audit_completed else 1


def _print_digest(
    offered: dict[str, Any], result: dict[str, Any] | None, *,
    stream: TextIO | None = None, styled: bool = False, source: str = "stdin / argument",
) -> int:
    """Render either the operator TUI or the stable raw digest."""
    output = stream or sys.stdout
    if styled:
        return _print_tui_digest(
            offered, result, stream=output, source=source, enabled=True,
        )
    return _print_raw_digest(offered, result, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="syntax legal",
        description="Convene the Syntax Legal Mind for a citation integrity audit.",
    )
    parser.add_argument("text", nargs="?", default="", help="text to audit")
    parser.add_argument("--file", default="", help="read audit text from a file")
    parser.add_argument("--llm", action="store_true",
                        help="enable the optional LLM fallback layer (network required)")
    parser.add_argument("--raw", action="store_true",
                        help="emit the stable unstyled digest for scripts and pipes")
    parser.add_argument("--boardroom", action="store_true",
                        help="request explicit Vertical AI Boardroom oversight guidance")
    args = parser.parse_args(argv)

    text = _read_input(args).strip()
    if not text:
        parser.print_usage(sys.stderr)
        sys.stderr.write("syntax legal: no text to audit "
                         "(pass text, use --file, or pipe stdin)\n")
        return 1

    try:
        from SyntaxIntelligence.syntax_core import SyntaxSwarm
    except ImportError as exc:
        sys.stderr.write(f"Syntax swarm unavailable: {exc}\n")
        return 1

    # auto_assemble=False keeps the boot light (no costume wardrobe) and
    # portable to Termux; convene_legal registers the auditor on demand.
    swarm = SyntaxSwarm(auto_assemble=False)
    try:
        if args.boardroom:
            outcome = swarm.convene_legal(
                text, use_llm=args.llm, request_boardroom=True,
            )
        else:
            # Preserve the established call shape for lightweight callers and
            # injected swarm doubles that predate boardroom coordination.
            outcome = swarm.convene_legal(text, use_llm=args.llm)
    except ImportError as exc:
        sys.stderr.write(f"Legal Mind unavailable on this device: {exc}\n")
        return 1
    except Exception as exc:  # swarm boundary: report, do not crash
        sys.stderr.write(f"Legal Mind convening failed: {type(exc).__name__}: {exc}\n")
        return 1

    styled = not args.raw and _supports_color(sys.stdout)
    source = f"file: {args.file}" if args.file else "stdin / argument"
    exit_code = _print_digest(
        outcome.get("offered", {}), outcome.get("adapter_result"),
        styled=styled, source=source,
    )
    coordination = outcome.get("coordination", {})
    guidance = coordination.get("guidance") if isinstance(coordination, dict) else None
    if args.boardroom and guidance:
        print(
            f"  BOARDROOM: {guidance.get('verdict', 'unknown')} | "
            f"{guidance.get('guidance', '')}",
        )
        print("  OVERSIGHT: human approval required; execution disabled")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
