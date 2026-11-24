from pipeline import dictionary


def row(dataset, column, upstream="", transformation=""):
    return {"dataset": dataset, "column_name": column, "upstream": upstream, "transformation": transformation}


def test_pii_tags_by_name_and_follow_lineage():
    rows = dictionary.tag_pii([
        row("bronze.customers", "email"),
        row("bronze.customers", "full_name"),
        row("silver.customers", "email", "bronze.customers.email", "trimmed + lowercased"),
        row("gold.sales", "customer_name", "silver.customers.full_name", "LEFT JOIN"),
        row("gold.customer_360", "contact", "silver.customers.email", "direct copy"),
        row("gold.daily", "emails", "silver.customers.email", "COUNT DISTINCT"),
        row("gold.sales", "product_name"),
        row("bronze.loyalty", "member.email"),
    ])
    tags = {f"{r['dataset']}.{r['column_name']}": (r["pii"], r["pii_origin"]) for r in rows}
    assert tags["silver.customers.email"] == ("email", "bronze.customers.email")
    assert tags["gold.sales.customer_name"] == ("name", "gold.sales.customer_name")
    assert tags["gold.customer_360.contact"] == ("email", "bronze.customers.email")
    assert tags["gold.daily.emails"] == ("", "") and tags["gold.sales.product_name"] == ("", "")
    assert tags["bronze.loyalty.member.email"][0] == "email"


def test_join_key_names_do_not_look_like_aggregates():
    rows = dictionary.tag_pii([
        {"dataset": "silver.customers", "column_name": "full_name", "upstream": "", "transformation": ""},
        {"dataset": "gold.tickets", "column_name": "customers_full_name", "upstream": "silver.customers.full_name",
         "transformation": "joined from silver.customers on account_id"},
        {"dataset": "gold.tickets", "column_name": "customers", "upstream": "silver.customers.full_name",
         "transformation": "count_distinct(full_name)"},
    ])
    assert [r["pii"] for r in rows] == ["name", "name", ""]
