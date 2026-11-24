"""Generate deterministic, intentionally messy raw source data for the demo."""

import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(64)
OUT = Path(__file__).resolve().parent.parent / "data" / "sources"

FIRST = ["Ana", "Luis", "Marta", "Javier", "Lucia", "Pablo", "Sofia", "Diego", "Elena", "Carlos",
         "Laura", "Miguel", "Paula", "Andres", "Irene", "Sergio", "Nerea", "Unai", "Ivan", "Clara"]
LAST = ["Garcia", "Lopez", "Martinez", "Sanchez", "Perez", "Gomez", "Fernandez", "Ruiz", "Diaz", "Alonso"]
COUNTRIES = {
    "ES": ["ES", "Spain", "spain", "España", " ES "],
    "PT": ["PT", "Portugal", "portugal"],
    "FR": ["FR", "France", "france "],
    "GB": ["GB", "UK", "United Kingdom"],
    "AR": ["AR", "Argentina"],
}
SEGMENTS = ["consumer", "Consumer", "SMB", "smb", "enterprise", "Enterprise"]
PRODUCTS = [
    ("P-100", "Noise Cancelling Headphones", "Audio", 199.0),
    ("P-101", "Wireless Earbuds", "Audio", 89.0),
    ("P-200", "4K Monitor 27\"", "Displays", 349.0),
    ("P-201", "Portable Monitor 15\"", "Displays", 179.0),
    ("P-300", "Mechanical Keyboard", "Peripherals", 129.0),
    ("P-301", "Ergonomic Mouse", "Peripherals", 59.0),
    ("P-400", "USB-C Docking Station", "Accessories", 149.0),
    ("P-401", "Laptop Stand", "Accessories", 39.0),
]
STATUSES = ["delivered", "DELIVERED", "shipped", "Shipped", "cancelled", "returned", "delivered "]
REVIEWS = {
    "positive": [
        "Absolutely love it, sound quality is amazing!",
        "Great value for the price, would buy again.",
        "Arrived early and works perfectly. Excellent!",
        "Build quality is superb, very happy with it.",
        "Support team was super helpful, great experience.",
    ],
    "neutral": [
        "It's ok. Does what it says.",
        "Average product, nothing special.",
        "Delivery took the expected time, product is fine.",
    ],
    "negative": [
        "Terrible, stopped working after two weeks.",
        "Package arrived late and the box was damaged.",
        "Way too expensive for what you get.",
        "Customer support never answered my emails. Awful.",
        "Poor quality, the cable broke on day one.",
        "Shipping was a nightmare, delayed twice.",
    ],
}


def fmt_date(d: date) -> str:
    return random.choice([d.isoformat(), d.strftime("%d/%m/%Y"), d.strftime("%Y/%m/%d")])


def customers() -> list[dict]:
    rows = []
    for i in range(1, 61):
        first, last = random.choice(FIRST), random.choice(LAST)
        email = f"{first}.{last}{i}@example.com"
        rows.append({
            "customer_id": f"C{i:04d}",
            "full_name": random.choice([f"{first} {last}", f"  {first} {last}", f"{first.upper()} {last}"]),
            "email": random.choice([email, email.upper(), f" {email} "]),
            "country": random.choice(COUNTRIES[random.choice(list(COUNTRIES))]),
            "segment": random.choice(SEGMENTS),
            "signup_date": fmt_date(date(2025, 1, 1) + timedelta(days=random.randint(0, 300))),
        })
    rows += [dict(r) for r in random.sample(rows, 5)]  # duplicates
    rows.append({"customer_id": None, "full_name": "Ghost", "email": None, "country": "??",
                 "segment": None, "signup_date": "not-a-date"})
    return rows


def orders() -> list[dict]:
    rows = []
    start = date(2026, 6, 1)
    for i in range(1, 401):
        pid, name, cat, price = random.choice(PRODUCTS)
        rows.append({
            "order_id": f"O{i:05d}",
            "order_date": fmt_date(start + timedelta(days=random.randint(0, 150))),
            "customer_id": f"C{random.randint(1, 60):04d}",
            "product_id": pid,
            "product_name": name,
            "category": cat,
            "quantity": random.choice([1, 1, 1, 2, 2, 3, 5]),
            "unit_price": random.choice([f"{price:.2f}", f"{price:.2f} EUR", str(price).replace(".", ",")]),
            "status": random.choice(STATUSES),
        })
    rows += [dict(r) for r in random.sample(rows, 12)]  # duplicates
    rows[3]["quantity"] = -2
    rows[10]["customer_id"] = ""
    rows[20]["unit_price"] = "n/a"
    rows[30]["order_date"] = "31/02/2026"
    return rows


def reviews(order_rows: list[dict]) -> list[dict]:
    rows = []
    for i, o in enumerate(random.sample(order_rows[:400], 150), start=1):
        sentiment = random.choices(list(REVIEWS), weights=[5, 2, 3])[0]
        rating = {"positive": random.choice([4, 5]), "neutral": 3, "negative": random.choice([1, 2])}[sentiment]
        rows.append({
            "review_id": f"R{i:05d}",
            "order_id": o["order_id"],
            "product_id": o["product_id"],
            "rating": random.choice([rating, str(rating), f"{rating}/5"]),
            "review_text": random.choice(REVIEWS[sentiment]),
        })
    rows.append({"review_id": "R99998", "order_id": "O00001", "product_id": "P-100", "rating": "", "review_text": ""})
    rows.append({"review_id": "R99999", "order_id": "O00002", "product_id": "P-101", "rating": "9",
                 "review_text": "   "})
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    o = orders()
    with open(OUT / "orders.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(o[0]))
        w.writeheader()
        w.writerows(o)
    with open(OUT / "customers.json", "w") as f:
        json.dump(customers(), f, indent=2, ensure_ascii=False)
    with open(OUT / "reviews.jsonl", "w") as f:
        for r in reviews(o):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote sources to {OUT}")


if __name__ == "__main__":
    main()
