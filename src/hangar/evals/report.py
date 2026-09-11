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
    ("passed", "Passed", ">"),
    ("failed", "Failed", ">"),
    ("harness", "Lost", ">"),
    ("review", "Review", ">"),
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
    passed = summary.get("n_passed", 0)
    harness = summary.get("n_harness_errors")
    valid = summary.get("valid_call_rate")
    # Failed = ran, was graded, did not pass. Seeds the harness lost were never
    # graded and are not failures of the agent, so they come out of the middle.
    failed = ("--" if harness is None else str(max(0, n - passed - harness)))
    return {
        "case": str(summary.get("case", "?")),
        "model": str(summary.get("model") or summary.get("harness") or "-"),
        "seeds": str(n),
        "passed": f"{passed}/{n}",
        "failed": failed,
        "harness": "--" if harness is None else str(harness),
        "review": str(summary.get("n_needs_review", "--")),
        "valid": ("--" if not isinstance(valid, dict) or valid.get("median") is None
                  else f"{valid['median'] * 100:.0f}"),
        "turns": _median(summary.get("turns")),
        "wall": _median(summary.get("wall_clock_s"), "{:.0f}"),
    }


def flags(summary: dict) -> str:
    """``[1 harness error, 2 to review]`` — empty when the row needs no action."""
    parts = []
    if summary.get("n_harness_errors"):
        n = summary["n_harness_errors"]
        parts.append(f"{n} harness error{'s' if n > 1 else ''}")
    if summary.get("n_needs_review"):
        parts.append(f"{summary['n_needs_review']} to review")
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
    n_harness = sum(s.get("n_harness_errors") or 0 for s in summaries)
    n_review = sum(s.get("n_needs_review") or 0 for s in summaries)
    graded = n_seeds - n_harness
    out.append("")
    out.append(f"  {n_passed}/{graded} graded seeds passed "
               f"across {len(summaries)} cell(s)")
    if n_harness:
        out.append(f"  {n_harness} seed(s) lost to harness errors — NOT a result. "
                   "Fix the cause and re-run those cases; this table is not final.")
    if n_review:
        out.append(f"  {n_review} seed(s) need a human look "
                   "(`evals review`): the agent's verdict contradicts the grade.")
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
