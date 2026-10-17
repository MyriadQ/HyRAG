"""judge/figures.py — the report's figures, drawn from `results.csv` and the judge logs.

    uv run python -m hyrag.judge.figures          # -> diagrams/judge/*.png

Four figures, each answering one question a table of numbers makes hard to read:

1. **quality by criterion** — the 4 × 3 matrix as bars, one panel per class, with the control
   (`Directness`) in the same frame as the three quality criteria. A row that wins on length
   shows up here as a row that wins Comprehensiveness and loses Directness.
2. **what a row costs against what it scores** — cost per query (log axis) against mean judged
   quality, so the report can say what the extra spend bought rather than only that it was made.
3. **pairwise win rates** with the per-replicate range, per class — the headline, with its
   spread visible instead of implied.
4. **length against Directness** — one point per answer, the control's own scatter. If long
   answers bought quality it would slope up; the report's claim is that it does not.

Colours are stable per row across every figure, so a reader learns the mapping once. Nothing
here recomputes a metric: the numbers come from `table.build_cells`, the same function that
writes `results.csv`, or the figure would be a second, quieter source of truth.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display in a headless run; the file is the deliverable
import matplotlib.pyplot as plt  # noqa: E402

from hyrag.judge import config, records, table  # noqa: E402
from hyrag.judge.prompts import criteria  # noqa: E402
from hyrag.parse.config import REPO  # noqa: E402

OUT = REPO / "diagrams" / "judge"
COLOURS = {"R1": "#6b7280", "R2": "#2563eb", "R3": "#dc2626"}
RUBRIC = list(criteria.RUBRIC)               # the papers' three — what the bars plot
ACCURACY = list(criteria.ACCURACY)           # reported beside the rubric, not in it
CRITERIA_LABEL = {item["key"]: item["name"] for item in criteria.CRITERIA}
CLASSES = ("factoid", "multi-hop", "thematic")


def _save(figure, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)
    print(f"  wrote {path}")
    return path


def figure_quality(cells: list[dict]) -> Path:
    figure, axes = plt.subplots(1, len(CLASSES), figsize=(4.4 * len(CLASSES), 4.2), sharey=True)
    for axis, label in zip(axes, CLASSES):
        subset = [cell for cell in cells if cell["class"] == label]
        keys = [key for key in ACCURACY + RUBRIC
                if any(cell.get(key) is not None for cell in subset)]
        width = 0.8 / max(len(subset), 1)
        for index, cell in enumerate(subset):
            values = [cell.get(key) or 0 for key in keys]
            errors = [cell.get(f"{key}_sd") or 0 for key in keys]
            positions = [position + index * width for position in range(len(keys))]
            axis.bar(positions, values, width, yerr=errors, capsize=3,
                     color=COLOURS.get(cell["row"], "#999"), label=cell["label"])
        axis.set_title(f"{label}  (n={subset[0]['n'] if subset else 0})")
        axis.set_xticks([position + 0.4 - width / 2 for position in range(len(keys))])
        axis.set_xticklabels([CRITERIA_LABEL[key] for key in keys], rotation=20, ha="right")
        axis.set_ylim(0, 1.05)
        axis.grid(axis="y", alpha=0.25)
    axes[0].set_ylabel("judged score (0–1)")
    axes[-1].legend(loc="lower right", fontsize=8)
    figure.suptitle("Judged quality on the papers' rubric — accuracy reported beside it", y=1.02)
    return _save(figure, "quality-by-criterion")


def figure_cost(cells: list[dict]) -> Path:
    figure, axes = plt.subplots(1, len(CLASSES), figsize=(4.4 * len(CLASSES), 4.2))
    for axis, label in zip(axes, CLASSES):
        subset = [cell for cell in cells if cell["class"] == label]
        for cell in subset:
            # Quality without the control: the control has its own figure, and folding a
            # padding penalty into a "quality" axis makes the axis mean two things at once.
            quality = cell.get("rubric_mean") or 0
            axis.scatter(cell["cost_usd"], quality, s=90, color=COLOURS.get(cell["row"], "#999"))
            axis.annotate(f"{cell['label']}\n{cell['latency_s']}s", (cell["cost_usd"], quality),
                          textcoords="offset points", xytext=(7, 4), fontsize=8)
        axis.set_xscale("log")
        axis.set_title(label)
        axis.set_xlabel("$ per query (log)")
        axis.grid(alpha=0.25)
    axes[0].set_ylabel("mean judged score (papers' rubric)")
    axes[0].set_ylim(0, 1.05)
    figure.suptitle("Cost against judged quality — what the spend bought", y=1.02)
    return _save(figure, "cost-vs-quality")


def figure_pairwise(pairwise: list[dict]) -> Path | None:
    if not pairwise:
        return None
    classes = [label for label in CLASSES if any(cell["class"] == label for cell in pairwise)]
    figure, axis = plt.subplots(figsize=(1.6 * len(classes) + 4, 4.2))
    rows = sorted({cell["row"] for cell in pairwise})
    width = 0.8 / max(len(rows), 1)
    for index, row in enumerate(rows):
        cells = [next((cell for cell in pairwise if cell["class"] == label and cell["row"] == row), None)
                 for label in classes]
        positions = [position + index * width for position in range(len(classes))]
        values = [cell["win_rate"] if cell else 0 for cell in cells]
        low = [cell["win_rate"] - cell["replicate_min"] if cell else 0 for cell in cells]
        high = [cell["replicate_max"] - cell["win_rate"] if cell else 0 for cell in cells]
        axis.bar(positions, values, width, yerr=[low, high], capsize=3,
                 color=COLOURS.get(row, "#999"), label=config.ROWS[row]["label"])
    chance = 1 / max(len(rows), 1)
    axis.axhline(chance, color="#111", linestyle="--", linewidth=1)
    axis.annotate(f"chance ({chance:.0%})", (len(classes) - 0.5, chance), fontsize=8, va="bottom")
    axis.set_xticks([position + 0.4 - width / 2 for position in range(len(classes))])
    axis.set_xticklabels(classes)
    axis.set_ylim(0, 1.0)
    axis.set_ylabel("win share (blind pairwise, 3 replicates)")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(fontsize=8)
    figure.suptitle("Pairwise headline — bars are the mean, whiskers the per-replicate range")
    return _save(figure, "pairwise-win-rates")


def figure_length_control() -> Path | None:
    scores = table.load_scores()
    if not scores:
        return None
    rows = records.load_all()
    criterion = criteria.CONTROL
    figure, axis = plt.subplots(figsize=(8, 4.6))
    for row in config.ROW_ORDER:
        points = []
        for (record_row, question_id, key), replicates in table._scores_by_item(scores).items():
            if record_row != row or key != criterion:
                continue
            record = rows.get(row, {}).get(question_id)
            if record is None:
                continue
            points.append((record.answer_chars, statistics.mean(replicates.values())))
        if points:
            axis.scatter([point[0] for point in points], [point[1] for point in points],
                         s=26, alpha=0.65, color=COLOURS.get(row, "#999"),
                         label=config.ROWS[row]["label"])
    axis.set_xscale("log")
    axis.set_xlabel("answer length (characters, log)")
    axis.set_ylabel(f"{CRITERIA_LABEL[criterion]} score (0–1)")
    axis.set_ylim(0, 1.05)
    axis.grid(alpha=0.25)
    axis.legend(fontsize=8)
    figure.suptitle("The control: length does not buy Directness")
    return _save(figure, "length-vs-directness")


def main() -> int:
    cells = table.build_cells()
    if not cells:
        print("  no cells yet — run the judge first")
        return 1
    figure_quality(cells)
    figure_cost(cells)
    figure_pairwise(table.build_pairwise())
    print(f"  cells behind the figures: {len(cells)} · "
          f"judge calls {table._judge_call_totals()['calls']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
