"""Implementation plan for a validated source: what Accept & implement will build, as JSON, Markdown and
Confluence storage HTML. The plan id hashes the inputs, so acceptance can require the approved plan."""

import hashlib
import html
import json

DEFAULT_CONTRACT = {"owner": "Onboarded source", "rows": [1, 10_000_000], "max_age_minutes": 24 * 60}


def plan_id(source: dict, links: list[dict], feed: str) -> str:
    key = {"name": source["name"], "dataset": source["dataset"], "rows": source["rows"], "feed": feed,
           "links": sorted(([lk.get("source_column"), lk.get("relation"), lk.get("target") or ""] for lk in links),
                           key=lambda x: x[0] or "")}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


def build(source: dict, links: list[dict], validation: dict, contracts: list[dict]) -> dict:
    name, feed = source["name"], validation["feed"]
    cols = [c["column_name"] for c in source["columns"]]
    by_col = {lk["source_column"]: lk for lk in links if lk.get("source_column")}
    checks = {c["source_column"]: c for c in validation["checks"]}
    joins = [lk for lk in links if lk.get("relation") == "join_key" and lk.get("target")]
    existing = next((c for c in contracts if c["dataset"] == feed), None)
    rows = source["rows"]

    join_rows = []
    for lk in joins:
        c = checks.get(lk["source_column"], {})
        ov = c.get("overlap")
        join_rows.append({"source_column": lk["source_column"], "target": lk["target"], "status": c.get("status", "ok"),
                          "overlap": ov, "expected_matches": None if ov is None else round(ov * rows),
                          "message": c.get("message", "")})
    join_cols = {lk["source_column"] for lk in joins}
    impact = validation["impact"]
    added = sorted(c["column"] for c in impact["columns"] if c["dataset"] == feed and c["status"] == "added")

    if existing:
        gold = {"dataset": feed, "action": "enrich",
                "how": f"LEFT JOIN silver.{name} on its join key; {len(added)} column(s) added, row count unchanged.",
                "expected_rows": "unchanged"}
        contract = {"dataset": feed, "action": "extend", "owner": existing.get("owner", ""),
                    "max_age_minutes": existing.get("max_age_minutes"), "add_columns": added,
                    "rules": ["Existing contract checks keep running.", "Join key must be unique in the source."]}
    else:
        targets = list(dict.fromkeys(lk["target"].rsplit(".", 1)[0] for lk in joins))
        how = (f"silver.{name} LEFT JOIN " + ", ".join(targets)) if targets else f"Copy of silver.{name}"
        gold = {"dataset": feed, "action": "create", "how": how + ".", "expected_rows": f"about {rows}"}
        rules = ["Row count within range.", "Schema matches the built columns."]
        if targets:
            rules.append(f"Columns joined from {', '.join(targets)} are part of the schema.")
        contract = {"dataset": feed, "action": "create", **DEFAULT_CONTRACT, "columns": cols, "rules": rules}

    quality = [{"rule": "duplicate_row", "layer": f"silver.{name}", "action": "drop exact duplicate rows"}]
    if join_cols:
        quality.append({"rule": "null_join_keys", "layer": f"silver.{name}",
                        "action": f"drop rows where every join key ({', '.join(sorted(join_cols))}) is empty"})
    quality.append({"rule": "typing", "layer": f"silver.{name}",
                    "action": "trim text, blank to null, numeric columns typed"})

    blockers = []
    if validation["status"] == "fail":
        blockers.append("Validation failed: fix the failing links.")
    if source.get("schema_only"):
        blockers.append("Documented schema only: connect the data (file, JSON or saved connection) to implement.")
    warnings = [c["message"] for c in validation["checks"] if c["status"] == "warn"] + impact.get("warnings", [])
    docs = source.get("docs") or {}

    return {
        "id": plan_id(source, links, feed),
        "title": f"Implementation plan: onboard {name} into {feed}",
        "source": {"name": name, "dataset": source["dataset"], "kind": source["kind"], "rows": rows,
                   "columns": len(cols), "documentation": source.get("doc")},
        "status": validation["status"],
        "implementable": not blockers,
        "blockers": blockers,
        "layers": [
            {"dataset": f"bronze.{name}", "action": "create", "how": "Raw landing, unchanged, with run metadata.",
             "expected_rows": rows},
            {"dataset": f"silver.{name}", "action": "create", "how": "Trimmed, typed and deduplicated.",
             "expected_rows": f"up to {rows}"},
            gold,
        ],
        "joins": join_rows,
        "mappings": [{"source_column": c, "relation": by_col.get(c, {}).get("relation", "new"),
                      "target": by_col.get(c, {}).get("target"),
                      "description": (docs.get(c) or {}).get("description") or by_col.get(c, {}).get("description", "")}
                     for c in cols],
        "quality": quality,
        "contract": contract,
        "impact": {"summary": impact["summary"],
                   "datasets": [n["id"] for n in impact["nodes"] if n["status"] != "unchanged"],
                   "columns": [f"{c['dataset']}.{c['column']} ({c['status']})" for c in impact["columns"]]},
        "warnings": list(dict.fromkeys(warnings)),
    }


def _md_table(head: list[str], rows: list[list]) -> list[str]:
    cell = lambda v: ("" if v is None else str(v)).replace("|", "\\|")  # noqa: E731
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(cell, r)) + " |" for r in rows]


def _pct(v) -> str:
    return "" if v is None else f"{v:.0%}"


def to_markdown(p: dict) -> str:
    s = p["source"]
    doc = s.get("documentation") or {}
    out = [f"# {p['title']}", "",
           f"Plan `{p['id']}` · validation **{p['status']}** · "
           + ("ready to implement" if p["implementable"] else "blocked"), "",
           f"Source `{s['dataset']}` ({s['kind']}): {s['rows']} rows, {s['columns']} columns."]
    if doc.get("title"):
        out.append(f"Documented in [{doc['title']}]({doc.get('url', '')}).")
    out += [f"- **Blocker:** {b}" for b in p["blockers"]]
    out += ["", "## Layers", ""] + _md_table(["Dataset", "Action", "How", "Expected rows"],
                                             [[x["dataset"], x["action"], x["how"], x["expected_rows"]] for x in p["layers"]])
    out += ["", "## Joins", ""] + (_md_table(["Source column", "Target", "Overlap", "Expected matches", "Status"],
                                             [[j["source_column"], j["target"], _pct(j["overlap"]), j["expected_matches"],
                                               j["status"]] for j in p["joins"]]) if p["joins"] else ["No joins."])
    out += ["", "## Column mappings", ""] + _md_table(["Column", "Relation", "Target", "Description"],
                                                     [[m["source_column"], m["relation"], m["target"], m["description"]]
                                                      for m in p["mappings"]])
    out += ["", "## Data quality rules", ""] + [f"- `{q['rule']}` on {q['layer']}: {q['action']}" for q in p["quality"]]
    c = p["contract"]
    out += ["", "## Data contract", "", f"- Dataset: {c['dataset']} ({c['action']})", f"- Owner: {c.get('owner') or 'n/a'}"]
    if c.get("max_age_minutes"):
        out.append(f"- Max age: {c['max_age_minutes']} minutes")
    if c.get("rows"):
        out.append(f"- Row count: {c['rows'][0]} to {c['rows'][1]}")
    if c.get("add_columns"):
        out.append(f"- Adds columns: {', '.join(c['add_columns'])}")
    out += [f"- {r}" for r in c.get("rules", [])]
    i = p["impact"]
    out += ["", "## Impact", "", ", ".join(f"{k}: {v}" for k, v in i["summary"].items() if v) or "No changes.",
            "", "Datasets: " + (", ".join(i["datasets"]) or "none")]
    if p["warnings"]:
        out += ["", "## Warnings", ""] + [f"- {w}" for w in p["warnings"]]
    return "\n".join(out) + "\n"


def to_storage(p: dict) -> str:
    """Confluence storage-format (XHTML) version of the plan."""
    e = lambda v: html.escape("" if v is None else str(v))  # noqa: E731

    def table(head, rows):
        return ("<table><tbody><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr>"
                + "".join("<tr>" + "".join(f"<td>{e(v)}</td>" for v in r) + "</tr>" for r in rows) + "</tbody></table>")

    s, c = p["source"], p["contract"]
    parts = [f"<p>Plan <code>{e(p['id'])}</code> · validation <strong>{e(p['status'])}</strong> · "
             f"{'ready to implement' if p['implementable'] else 'blocked'}. Source <code>{e(s['dataset'])}</code> "
             f"({e(s['kind'])}), {e(s['rows'])} rows.</p>",
             "<h2>Layers</h2>", table(["Dataset", "Action", "How", "Expected rows"],
                                      [[x["dataset"], x["action"], x["how"], x["expected_rows"]] for x in p["layers"]]),
             "<h2>Joins</h2>", table(["Source column", "Target", "Overlap", "Status"],
                                     [[j["source_column"], j["target"], _pct(j["overlap"]), j["status"]] for j in p["joins"]]),
             "<h2>Column mappings</h2>", table(["Column", "Relation", "Target", "Description"],
                                               [[m["source_column"], m["relation"], m["target"], m["description"]]
                                                for m in p["mappings"]]),
             "<h2>Data quality rules</h2><ul>" + "".join(f"<li>{e(q['rule'])} on {e(q['layer'])}: {e(q['action'])}</li>"
                                                        for q in p["quality"]) + "</ul>",
             f"<h2>Data contract</h2><p>{e(c['dataset'])} ({e(c['action'])}), owner {e(c.get('owner') or 'n/a')}.</p>",
             "<h2>Impact</h2><p>" + e(", ".join(p["impact"]["datasets"]) or "none") + "</p>"]
    if p["warnings"]:
        parts.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{e(w)}</li>" for w in p["warnings"]) + "</ul>")
    return "".join(parts)
