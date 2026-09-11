"""Presentation of cell summaries — terminal and markdown, one source.

The campaign runner renders the same table twice: once to the terminal so a run
is readable while it is happening, and once to ``table.md`` beside the results so
it is readable afterwards without re-deriving anything. Both come from here, over
the summary dicts ``regrade.regrade_file`` produces, so the live table and the
final table can never disagree about a number.

``Ambig`` and ``Rep-dis`` ride in the same row as ``Passed`` on purpose. A
pass-rate alone hides the two ways it misleads — a score that turned on run
ORDER, and a seed whose own report contradicts the effect grade — and a reader
who sees only ``0/3`` will read a genuine failure. They travel together or the
number travels wrong.
"""

from __future__ import annotations

COLUMNS = [
    ("case", "Case", "<"),
    ("model", "Model", "<"),
    ("seeds", "Seeds", ">"),
    ("ran", "Ran", ">"),
    ("passed", "Passed", ">"),
    ("ambig", "Ambig", ">"),
    ("repdis", "Rep-dis", ">"),
    ("valid", "Valid%", ">"),
    ("turns", "Turns", ">"),
    ("wall", "Wall s", ">"),
]


def _median(block, fmt: str = "{:.3g}") -> str:
    if not isinstance(block, dict) or block.get("median") is None:
        return "--"
    return fmt.format(block["median"])


def summary_cells(summary: dict) -> dict[str, str]:
    """One summary dict -> the string cell for each column in ``COLUMNS``."""
    n = summary.get("n_seeds", 0)
    valid = summary.get("valid_call_rate")
    return {
        "case": str(summary.get("case", "?")),
        "model": str(summary.get("model") or summary.get("harness") or "-"),
        "seeds": str(n),
        "ran": f"{summary.get('n_completed', 0)}/{n}",
        "passed": f"{summary.get('n_passed', 0)}/{n}",
        "ambig": str(summary.get("n_ambiguous", "--")),
        "repdis": str(summary.get("n_report_disagrees", "--")),
        "valid": ("--" if not isinstance(valid, dict) or valid.get("median") is None
                  else f"{valid['median'] * 100:.0f}"),
        "turns": _median(summary.get("turns")),
        "wall": _median(summary.get("wall_clock_s"), "{:.0f}"),
    }


def flags(summary: dict) -> str:
    """``[3 ambig, 2 rep-dis]`` — empty when the pass-rate reads at face value."""
    parts = []
    if summary.get("n_ambiguous"):
        parts.append(f"{summary['n_ambiguous']} ambig")
    if summary.get("n_report_disagrees"):
        parts.append(f"{summary['n_report_disagrees']} rep-dis")
    return f"[{', '.join(parts)}]" if parts else ""


def render_terminal(summaries: list[dict], title: str = "") -> str:
    """The aligned plain-text table printed during and after a campaign."""
    if not summaries:
        return "(no cells yet)"
    rows = [summary_cells(s) for s in summaries]
    widths = {
        key: max(len(head), *(len(r[key]) for r in rows))
        for key, head, _ in COLUMNS
    }
    def line(cells, sep="  "):
        return sep.join(
            f"{cells[key]:{align}{widths[key]}}" for key, _, align in COLUMNS
        ).rstrip()

    head = {key: head for key, head, _ in COLUMNS}
    out = []
    if title:
        out.append(title)
    out.append(line(head))
    out.append("  ".join("-" * widths[key] for key, _, _ in COLUMNS))
    out.extend(line(r) for r in rows)

    n_seeds = sum(s.get("n_seeds", 0) for s in summaries)
    n_passed = sum(s.get("n_passed", 0) for s in summaries)
    n_ambig = sum(s.get("n_ambiguous") or 0 for s in summaries)
    n_repdis = sum(s.get("n_report_disagrees") or 0 for s in summaries)
    total = f"  {n_passed}/{n_seeds} seeds passed across {len(summaries)} cell(s)"
    if n_ambig or n_repdis:
        total += f" — {n_ambig} ambiguous, {n_repdis} report-disagree"
    out.append("")
    out.append(total)
    if n_ambig or n_repdis:
        out.append("  those counts gate the pass-rate: read it at face value only "
                   "where both are 0.")
    return "\n".join(out)


def render_markdown(summaries: list[dict], note: str = "") -> str:
    """The same table as ``table.md`` beside the campaign's results."""
    header = [head for _, head, _ in COLUMNS]
    lines = []
    if note:
        lines.append(f"<!-- {note} -->")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for summary in summaries:
        cells = summary_cells(summary)
        lines.append("| " + " | ".join(cells[key] for key, _, _ in COLUMNS) + " |")
    return "\n".join(lines) + "\n"
