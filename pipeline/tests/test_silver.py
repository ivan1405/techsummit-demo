import json

from pipeline import silver


def test_clean_orders_drops_invalid_rows_and_reports_reasons():
    raw = (
        "order_id,order_date,customer_id,product_id,product_name,category,quantity,unit_price,status\n"
        "O1,2026-07-01,C1,P1,A,Audio,2,\"10,5\",Delivered \n"
        "O1,2026-07-01,C1,P1,A,Audio,2,10.5,delivered\n"
        "O2,31/02/2026,C1,P1,A,Audio,1,10,shipped\n"
        "O3,01/07/2026,,P1,A,Audio,1,10,shipped\n"
        "O4,2026/07/03,C2,P1,A,Audio,-1,10,shipped\n"
        "O5,2026/07/03,C2,P1,A,Audio,1,n/a,shipped\n"
        "O6,03/07/2026,C2,P1,A,Audio,3,99.00 EUR,SHIPPED\n"
    ).encode()
    df, dropped = silver.clean_orders(raw)
    assert list(df["order_id"]) == ["O1", "O6"]
    assert list(df["amount"]) == [21.0, 297.0]
    assert list(df["status"]) == ["delivered", "shipped"]
    assert dropped == {
        "duplicate_order_id": 1, "missing_customer_id": 1, "invalid_order_date": 1,
        "invalid_unit_price": 1, "non_positive_quantity": 1,
    }


def test_clean_customers_normalises_fields():
    raw = json.dumps([
        {"customer_id": "C1", "full_name": "  ana GARCIA", "email": " ANA@X.COM ", "country": "España",
         "segment": "SMB", "signup_date": "01/02/2025"},
        {"customer_id": "C1", "full_name": "dup", "email": "", "country": "ES", "segment": "", "signup_date": ""},
        {"customer_id": None, "full_name": "ghost", "email": None, "country": "??", "segment": None,
         "signup_date": "x"},
    ]).encode()
    df, dropped = silver.clean_customers(raw)
    row = df.iloc[0]
    assert len(df) == 1
    assert (row.full_name, row.email, row.country, row.segment) == ("Ana Garcia", "ana@x.com", "ES", "smb")
    assert dropped == {"missing_customer_id": 1, "duplicate_customer_id": 1}


def test_clean_reviews_enriches_with_heuristic_without_llm():
    raw = "\n".join(json.dumps(r) for r in [
        {"review_id": "R1", "order_id": "O1", "product_id": "P1", "rating": "5/5", "review_text": "Love it, great!"},
        {"review_id": "R2", "order_id": "O1", "product_id": "P1", "rating": 1,
         "review_text": "Package arrived late, terrible"},
        {"review_id": "R3", "order_id": "O1", "product_id": "P1", "rating": "9", "review_text": "bad rating"},
        {"review_id": "R4", "order_id": "O1", "product_id": "P1", "rating": "3", "review_text": "  "},
    ]).encode()
    df, dropped = silver.clean_reviews(raw)
    assert list(df["sentiment"]) == ["positive", "negative"]
    assert list(df["topic"]) == ["other", "delivery"]
    assert set(df["enrichment_method"]) == {"heuristic"}
    assert dropped == {"empty_review_text": 1, "invalid_rating": 1, "duplicate_review_id": 0}
