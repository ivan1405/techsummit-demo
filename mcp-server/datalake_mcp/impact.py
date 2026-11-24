"""What-if lineage: apply proposed changes to the current column lineage and report the impact.

Nothing is executed; the result is a diff of the lineage graph (added/removed datasets and edges)
plus every downstream column that would break or change, with the reason.
"""

Col = tuple[str, str]

CHANGE_TYPES = ("add_dataset", "remove_dataset", "add_column", "remove_column", "rename_column", "change_logic")
ROOT = {"removed", "renamed", "modified"}
LOST = {"removed", "broken"}


class ChangeError(ValueError):
    pass


def split_ref(ref: str) -> Col:
    """'gold.sales.amount' -> ('gold.sales', 'amount'); dataset ids may contain dots (source.orders.csv)."""
    dataset, _, column = ref.strip().rpartition(".")
    if not dataset or "." not in dataset:
        raise ChangeError(f"'{ref}' is not a <layer>.<dataset>.<column> reference.")
    return dataset, column


def analyze(columns: dict[str, list[str]], deps: dict[Col, list[Col]], edges: list[dict],
            changes: list[dict]) -> dict:
    """columns: {dataset: [column]}, deps: {(dataset, column): [(src_dataset, src_column)]},
    edges: [{source, target}]. Returns the impact report for `changes`."""
    datasets = {d: "unchanged" for d in columns} | {e[k]: "unchanged" for e in edges for k in ("source", "target")}
    cols = {(d, c): "unchanged" for d, cs in columns.items() for c in cs}
    deps = {k: list(v) for k, v in deps.items()}
    edge_status = {(e["source"], e["target"]): "unchanged" for e in edges}
    reasons: dict[Col, str] = {}
    renamed: dict[Col, str] = {}
    warnings: list[str] = []

    def require(change: dict, *keys: str) -> list[str]:
        missing = [k for k in keys if not str(change.get(k) or "").strip()]
        if missing:
            raise ChangeError(f"{change.get('type')}: missing {', '.join(missing)}.")
        return [str(change[k]).strip() for k in keys]

    def existing(col: Col) -> Col:
        if col not in cols:
            raise ChangeError(f"Unknown column {col[0]}.{col[1]}.")
        return col

    def add_dataset(dataset: str, inputs: list[str]) -> None:
        if "." not in dataset:
            raise ChangeError(f"Dataset '{dataset}' must look like <layer>.<name>, e.g. gold.returns.")
        if datasets.get(dataset, "removed") == "removed":
            datasets[dataset] = "added"
        for src in inputs:
            link(src, dataset)

    def link(src: str, dst: str) -> None:
        if src not in datasets:
            if not src.startswith("source."):
                warnings.append(f"{dst} reads from unknown dataset {src}.")
            datasets[src] = "added"
        edge_status.setdefault((src, dst), "added")

    for change in changes:
        kind = change.get("type")
        if kind not in CHANGE_TYPES:
            raise ChangeError(f"Unknown change type '{kind}'. Use one of: {', '.join(CHANGE_TYPES)}.")
        if kind == "add_dataset":
            (dataset,) = require(change, "dataset")
            add_dataset(dataset, [i for i in change.get("inputs") or [] if i])
        elif kind == "remove_dataset":
            (dataset,) = require(change, "dataset")
            if dataset not in datasets:
                raise ChangeError(f"Unknown dataset {dataset}.")
            datasets[dataset] = "removed"
            for key in edge_status:
                if dataset in key:
                    edge_status[key] = "removed"
            for col in [c for c in cols if c[0] == dataset]:
                cols[col], reasons[col] = "removed", "dataset removed"
        elif kind == "add_column":
            dataset, column = require(change, "dataset", "column")
            if (dataset, column) in cols and cols[(dataset, column)] != "removed":
                if change.get("if_missing"):
                    continue
                raise ChangeError(f"{dataset}.{column} already exists.")
            if dataset not in datasets or datasets[dataset] == "removed":
                add_dataset(dataset, [])
            sources = [split_ref(s) for s in change.get("sources") or [] if s.strip()]
            for src in sources:
                existing(src)
                link(src[0], dataset)
            cols[(dataset, column)] = "added"
            deps[(dataset, column)] = sources
            how = change.get("transformation") or "new column"
            reasons[(dataset, column)] = how if sources else f"{how} (no upstream column: new source field)"
        elif kind == "remove_column":
            col = existing(tuple(require(change, "dataset", "column")))
            cols[col], reasons[col] = "removed", change.get("reason") or "column removed"
        elif kind == "rename_column":
            dataset, column, new_name = require(change, "dataset", "column", "new_name")
            col = existing((dataset, column))
            cols[col], renamed[col] = "renamed", new_name
            reasons[col] = f"renamed to {new_name}"
        elif kind == "change_logic":
            dataset, column, how = require(change, "dataset", "column", "transformation")
            col = existing((dataset, column))
            cols[col], reasons[col] = "modified", f"new logic: {how}"

    _propagate(cols, deps, reasons, renamed)

    for col, status in cols.items():
        if status == "added":
            lost = [f"{d}.{c}" for d, c in deps.get(col, []) if cols.get((d, c)) in LOST]
            if lost:
                warnings.append(f"New column {col[0]}.{col[1]} depends on removed {', '.join(lost)}.")
    for dataset, status in datasets.items():
        if status != "unchanged":
            continue
        own = [cols[c] for c in cols if c[0] == dataset]
        if "broken" in own:
            datasets[dataset] = "broken"
        elif any(s in ROOT or s == "added" for s in own):
            datasets[dataset] = "modified"
        elif "impacted" in own:
            datasets[dataset] = "impacted"

    changed = [
        {"dataset": d, "column": c, "status": s, "reason": reasons.get((d, c), ""),
         **({"new_name": renamed[(d, c)]} if (d, c) in renamed else {})}
        for (d, c), s in cols.items() if s != "unchanged"
    ]
    order = {"removed": 0, "broken": 1, "renamed": 2, "modified": 3, "impacted": 4, "added": 5}
    changed.sort(key=lambda r: (order[r["status"]], r["dataset"], r["column"]))
    summary = {s: sum(r["status"] == s for r in changed) for s in order}
    summary["datasets_impacted"] = sum(s in ("broken", "impacted") for s in datasets.values())
    return {
        "summary": summary,
        "columns": changed,
        "nodes": [{"id": d, "layer": d.split(".", 1)[0], "name": d.split(".", 1)[1], "status": s,
                   "columns": sorted(c for (ds, c), st in cols.items() if ds == d and st != "removed")}
                  for d, s in sorted(datasets.items())],
        "edges": [{"source": s, "target": t, "status": st} for (s, t), st in edge_status.items()],
        "warnings": warnings,
    }


def _propagate(cols: dict[Col, str], deps: dict[Col, list[Col]], reasons: dict[Col, str],
               renamed: dict[Col, str]) -> None:
    """Mark downstream columns broken (every input lost) or impacted (some input lost or changed)."""
    explicit = {c for c, s in cols.items() if s in ROOT or s == "added"}
    for _ in range(len(cols) + 1):
        changed = False
        for col, inputs in deps.items():
            if col in explicit or col not in cols or not inputs:
                continue
            status, reason = _evaluate(inputs, cols, renamed)
            if (status, reason) != (cols[col], reasons.get(col)):
                cols[col] = status
                reasons[col] = reason
                changed = True
        if not changed:
            return


def _evaluate(inputs: list[Col], cols: dict[Col, str], renamed: dict[Col, str]) -> tuple[str, str | None]:
    lost = [f"{d}.{c}" for d, c in inputs if cols.get((d, c), "removed") in LOST]
    if lost and len(lost) == len(set(inputs)):
        return "broken", f"all inputs removed: {', '.join(lost)}"
    if lost:
        return "impacted", f"loses input {', '.join(lost)}"
    notes = []
    for d, c in dict.fromkeys(inputs):
        status = cols.get((d, c))
        if status == "renamed":
            notes.append(f"update mapping: {d}.{c} renamed to {renamed[(d, c)]}")
        elif status in ("modified", "impacted"):
            notes.append(f"values change via {d}.{c}")
    return ("impacted", "; ".join(notes)) if notes else ("unchanged", None)
