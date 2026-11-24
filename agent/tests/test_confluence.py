from app import confluence

PAGE = """<p>Owner: CRM.</p>
<table><tbody><tr><th>Column</th><th>Type</th><th>Description</th><th>Links to</th></tr>
<tr><td>member.customer_id</td><td>string</td><td>CRM <strong>customer</strong> number.</td><td>Join key: silver.customers.customer_id</td></tr>
<tr><td>points</td><td>integer</td><td>Points<br/>balance</td></tr>
<tr><td></td><td>string</td><td>ignored: no name</td><td></td></tr>
</tbody></table>
<table><tbody><tr><th>Owner</th><th>Team</th></tr><tr><td>a</td><td>b</td></tr></tbody></table>
<table><tbody><tr><td><b>Field</b></td><td>Meaning</td></tr><tr><td>tier</td><td>Programme tier</td></tr></tbody></table>"""


def test_extract_schemas_reads_schema_tables_only():
    schemas = confluence.extract_schemas(PAGE)
    assert schemas == [
        [{"name": "member.customer_id", "type": "string", "description": "CRM customer number.",
          "links_to": "Join key: silver.customers.customer_id"},
         {"name": "points", "type": "integer", "description": "Points balance", "links_to": ""}],
        [{"name": "tier", "type": "", "description": "Programme tier", "links_to": ""}],
    ]


def test_cql_quotes_user_input():
    assert confluence.cql('say "hi"', "DATA", "data-source") == (
        'type = page AND space = "DATA" AND label = "data-source" AND (title ~ "say \\"hi\\"" OR text ~ "say \\"hi\\"")')
    assert confluence.cql() == "type = page"


def test_page_text_skips_tables():
    assert confluence._text(PAGE) == "Owner: CRM."
