# Tech Summit demo: Devin + Parser AI

A monorepo with a medallion data pipeline, an S3 data lake with full data lineage, an MCP server, and a LangGraph chatbot with a web UI. One command starts everything:

```bash
cp .env.example .env   # optional: add an LLM key
docker compose up --build
```

| URL | What |
|---|---|
| http://localhost:3000 | Chat UI, lineage graph, data quality report |
| http://localhost:8080/docs | Agent API (FastAPI) |
| http://localhost:8000/mcp | MCP server (streamable HTTP) |
| http://localhost:8333 | SeaweedFS S3 endpoint (the data lake) |

## Architecture

```mermaid
flowchart LR
    subgraph sources[Raw sources]
        O[orders.csv]
        C[customers.json]
        R[reviews.jsonl]
    end
    subgraph lake[S3 data lake: SeaweedFS]
        B[(bronze<br/>raw files)]
        S[(silver<br/>clean + AI enriched)]
        G[(gold<br/>business tables)]
        L[(lineage<br/>OpenLineage events + tables)]
    end
    O & C & R --> B --> S --> G
    S -. LLM sentiment/topic .-> S
    B & S & G -.-> L
    G & L --> MCP[MCP server<br/>DuckDB, read-only SQL]
    MCP --> AG[LangGraph agent<br/>FastAPI]
    AG --> UI[React chat UI]
    L -. optional .-> MQ[Marquez]
```

| Layer | Content | Code |
|---|---|---|
| Sources | Messy sample data: duplicates, mixed date and price formats, bad rows, free-text reviews | `scripts/generate_sources.py`, `data/sources/` |
| Bronze | Raw files landed unchanged under `<source>/ingested_at=<ts>/` | `pipeline/src/pipeline/bronze.py` |
| Silver | Deduplicated, typed, normalised, invalid rows dropped; reviews enriched with sentiment and topic by an LLM | `silver.py`, `enrich.py` |
| Gold | `sales`, `customer_360`, `product_performance`, `daily_revenue`, `review_insights` | `gold.py` |
| MCP | Tools: `list_tables`, `describe_table`, `query`, `get_lineage`, `get_column_lineage`, `get_data_quality` | `mcp-server/` |
| Agent | LangGraph tool-calling agent over the MCP tools, served by FastAPI | `agent/` |
| UI | Chat, lineage graph with column tracing, data quality report | `ui/` |

## Data lineage

Every number in a gold table can be traced back to the raw file it came from:

- **Row level:** every silver and gold row has `_run_id` and `_processed_at`. Silver rows also have `_source_uri`, the exact bronze object they were read from.
- **Job level:** each step (3 bronze, 3 silver, 5 gold) emits OpenLineage `START`/`COMPLETE`/`FAIL` events with schema, column lineage and row count facets. Raw events are stored at `s3://lineage/events/run_id=<id>/events.jsonl`.
- **Reporting tables** in the `lineage` bucket, queryable via MCP/SQL:

| Table | Content |
|---|---|
| `lineage_runs` | One row per pipeline run |
| `lineage_outputs` | Rows and column count of every dataset written, per run |
| `lineage_jobs` | One row per job: inputs, outputs, rows in/out/dropped, status, duration |
| `lineage_edges` | Dataset to dataset edges |
| `lineage_columns` | Column to column edges with the transformation applied |
| `data_quality` | Rows dropped per rule (e.g. `invalid_order_date`, `non_positive_quantity`) |

Lineage history is appended, so you can compare runs. Ask the chatbot "Where does customer revenue come from?" or open the **Lineage** tab and click a column to trace it to its source.

## Data dictionary and impact analysis

- **Data dictionary tab:** every bronze, silver and gold column with its type, description, null %, distinct count, sample values and upstream columns. The pipeline profiles each run into the `data_dictionary` lineage table; descriptions live in `pipeline/src/pipeline/dictionary.py`. MCP tool: `get_data_dictionary`.
- **Impact analysis tab:** describe a new requirement as changes (add/rename/drop a column, change logic, add/drop a dataset or source) and preview the lineage before building it. Added nodes and edges are dashed green, removals dashed red, downstream columns that lose some inputs are amber (`impacted`) and those that lose all inputs are red (`broken`). Export the proposal and its impact as JSON. Nothing runs: the analysis is applied to the latest run's column lineage. MCP tool: `analyze_change_impact`; API: `POST /api/impact`.

Example request body:

```json
{"changes": [{"type": "remove_column", "dataset": "silver.customers", "column": "email"}]}
```

### Data contracts and freshness

Each gold dataset has a contract in `pipeline/src/pipeline/contracts.py`: owner, row-count range, required columns and types, not-null columns, a unique key and a `max_age_minutes`. Every run writes the results to `lineage.contract_checks`; the MCP tool `get_contracts` adds freshness (`fresh` ≤ max age, `stale` ≤ 2×, `expired` beyond) and a status: green, amber (stale) or red (failed check or expired). Badges appear in the **Data dictionary**; details are in **Data quality**. Onboarded datasets get a default contract (accepted schema, ≥ 1 row, 24 h).

### Ask about a column

Every column in the **Data dictionary** has a **?** button, and a traced column in **Lineage** has **Ask about this column**. Both open the chat with "How is `<dataset>.<column>` calculated and where does it come from?". The answer comes from the MCP tool `explain_column`: the description and profile, each upstream hop with its transformation, downstream uses, the data quality rules of the job that builds the column, and the dataset's contract status. Demo mode answers any question that contains a `layer.dataset.column` reference.

### Run history and run-to-run diff

The **Run history** tab lists every pipeline run (status, duration, datasets written, gold rows, rows dropped, contract failures) with a summary of what changed vs the run before. Select a run to see, per dataset, rows before/after with the delta and %, columns added/removed and type changes, plus data-quality rules and contract checks whose result changed. Compare with any other run from the dropdown, or start a new run with **Run pipeline now**. Built from `lineage_outputs`, `data_dictionary`, `data_quality` and `contract_checks`; MCP tools `get_run_history` and `compare_runs` let the chat answer "what changed since the last run?".

### Onboarding a new data source

In the **Data dictionary** tab, **+ Add data source** walks through:

1. **Source:** upload a CSV/JSON/JSONL file, paste a JSON object, pick a table from a **saved connection**, or read a database URL directly (read-only, max 5000 rows). Try `data/samples/loyalty.json`, `data/samples/returns.csv`, or the demo Postgres: `docker compose --profile sources up`, then `postgresql://reader:reader@source-db:5432/erp`, table `support_tickets`.
2. **Profile:** types, nulls, distinct counts and samples, like the dictionary.
3. **Linkages:** each column is suggested as a `join_key`, `maps_to` an existing column, or a `new` attribute. Candidates come from name similarity and real value overlap with silver/gold data; with an LLM configured the model reviews them and explains each choice. Edit any relation or target.
4. **Validate:** join keys are checked against the data (% of values found, orphan examples, whether the target is unique), and the impact graph shows the planned `source → bronze → silver → feed` lineage. Export it all as JSON.
5. **Implementation plan:** layers with expected rows, joins with expected matches, data-quality rules, the data contract (new, or the existing one extended), impacted datasets and warnings. Download it as Markdown or JSON, or publish it to Confluence. Tick the approval box to enable implementing.
6. **Accept & implement** (disabled while any check fails or the plan isn't approved): the agent rebuilds the plan from the submitted links and refuses if its id differs from the approved one (HTTP 409), then the `pipeline-api` service saves the spec (and file content) to `s3://registry/sources/` and re-runs the pipeline. Onboarded sources then flow through the medallion layers on every run:
   - `bronze.<name>`: the file as delivered (databases are snapshotted to JSONL).
   - `silver.<name>`: trimmed, typed, deduplicated; rows missing every join key are dropped (recorded in data quality).
   - Feed into a **new** gold dataset: source rows LEFT JOINed to each join-key target (clashing target columns prefixed). Feed into an **existing** gold dataset (e.g. `gold.customer_360`): the source's attributes are added via a join key the dataset already has; the key must be unique in the source.
   - Lineage, column lineage, dictionary and data quality are recorded like the core sources. Remove an onboarded source with its × chip in the dictionary.

### Confluence

Source documentation can come from Confluence: search pages by text and label (default `data-source`) in `CONFLUENCE_SPACE`. Any page table with a **Column** header and a **Type** or **Description** header is read as a schema; an optional **Links to** column naming a `silver.`/`gold.` column (e.g. `Join key: silver.customers.customer_id`) is used as a link hint.

- **Attach documentation** to a file, JSON or connection source: descriptions show in the profile and linkages, documented links confirm or override the heuristic ones (and are given to the LLM), coverage lists undocumented columns and documented columns missing from the data, and accepted descriptions go into the data dictionary.
- **Confluence page** as the source: a documented-only source with no data yet. Links are checked by name and type, the plan and impact graph show what would change, and implementing stays blocked until real data is connected.

Without credentials the stack uses the bundled **mock Confluence** (`confluence-mock`, http://localhost:8095) with pages for the loyalty, support tickets, returns and a planned marketing campaigns source; published plans stay in its memory. For Confluence Cloud set `CONFLUENCE_URL=https://<site>.atlassian.net`, `CONFLUENCE_EMAIL` and `CONFLUENCE_API_TOKEN` in `.env`.

### Saved connections

**Saved connection → Manage connections** stores named connection strings in `s3://registry/connections/` after a successful test. Supported: `postgresql://`, `mysql://`, `sqlite:///` and `http(s)://` CSV/JSON/JSONL files. Passwords are masked in every API response, and a schema browser lists each table and its columns so you can pick one (or write a single `SELECT`). Reads are read-only: Postgres and MySQL use read-only transactions, SQLite opens in `mode=ro`, and only one `SELECT`/`WITH` statement is accepted. Accepted sources reference the connection by name and are re-read on every pipeline run; a connection in use can't be deleted.

Until accepted, staged sources live in the MCP server's memory only. Database connection strings are stored in the registry as entered (masked in the API); use a read-only demo user. MCP tools: `stage_source`, `get_staged_source`, `suggest_source_links`, `validate_source_links` (not offered to the chat agent).

**Marquez (optional):** to also see the lineage in the reference OpenLineage UI:

```bash
OPENLINEAGE_URL=http://marquez:5000 docker compose --profile marquez up --build
```

Then open http://localhost:3001.

## LLM configuration

Set these in `.env`:

| Variable | Values |
|---|---|
| `LLM_PROVIDER` | `none` (default), `openai`, `anthropic` |
| `LLM_MODEL` | e.g. `gpt-4o-mini`, `claude-sonnet-4-5`, `claude-sonnet-5-5` |
| `LLM_TEMPERATURE` | Optional. Empty uses the model default; `claude-sonnet-5-5` and `gpt-5` models only accept the default |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Your key |

With `LLM_PROVIDER=none` everything still runs: review enrichment uses keyword rules (rows are tagged `method=heuristic`), and the chatbot answers a fixed set of questions (revenue, products, customers, complaints, lineage, data quality) through the same MCP tools.

## Local development

```bash
# data lake
docker compose up -d seaweedfs

# pipeline
cd pipeline && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m pipeline.run
.venv/bin/pytest -q

# MCP server
cd mcp-server && python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m datalake_mcp.server     # :8000
.venv/bin/pytest -q

# agent
cd agent && python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8080

# UI (proxies /api to :8080)
cd ui && npm install && npm run dev        # :5173
```

Rerun the pipeline in Compose with `docker compose run --rm pipeline`. Regenerate the sample sources with `python scripts/generate_sources.py`.

## Repository layout

```
data/sources/        sample raw files
infra/seaweedfs/     S3 identities (demo credentials: admin for the pipeline, read-only for MCP)
pipeline/            bronze, silver, gold, lineage
mcp-server/          MCP server over the lake (DuckDB)
agent/               LangGraph agent + FastAPI
ui/                  React (Vite) chat UI, served by nginx
docker-compose.yml   the whole stack
```
