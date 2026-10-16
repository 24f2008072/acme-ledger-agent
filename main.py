import json
import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Acme Appliances Order Ledger Q&A")

# These are the personalized endpoints supplied by the assignment.
BASE_PARAMS = (
    "email=24f2008072%40ds.study.iitm.ac.in"
    "&quizSign=OcWTxMkE%2BAbaRSstAUSIOyXCm7uZLPX484jaJ5BdZRAdRkeJ2MJJp14S0s8uztz4%2FDXSyCk5GnuU8ksRLUOavauwj50A00i5ztQIBdXbQAsH684Wfb82fmO01Q2lcv3iPT28qsmzdPtiaQw%2BHXnk8xztA%2BJns4BK9l1gDJzbZn%2BD%2BXrhrNTimpqhtG2iHGoOU7Yh%2FtvNCb0U1dyEMtbbWzViwOcV63bns6zBcxX2jc6d8Trii2%2FkGPZeVV7DAV3UhPF0%2BrwUupqH3BvH1VNO0t05wqto5oM7eJXL7jE7PjOSHLqIciwTbUoVXOnuj2p6RkHXCMHLylomHBktuFes9Q%3D%3D"
    "&questionId=q-ledger-agent-server"
)
ROOT = "https://exam.sanand.workers.dev/questionData?"
ORDERS_URL = ROOT + BASE_PARAMS + "&path=%2Forders&page=1"
RATES_URL = ROOT + BASE_PARAMS + "&path=%2Frates"
EXPORT_URL = ROOT + BASE_PARAMS + "&path=%2Fexport"

TZ = ZoneInfo("Asia/Kolkata")
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
    "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
DATA = {"orders": None, "rates": None}


class QuestionRequest(BaseModel):
    question: str


def parse_export_response(resp):
    """Accept JSON arrays/objects or newline-delimited JSON."""
    text = resp.text.strip()
    try:
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("orders", "data", "results", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
    except Exception:
        pass

    rows = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict) and "id" in item:
                rows.append(item)
            elif isinstance(item, list):
                rows.extend(x for x in item if isinstance(x, dict))
        except Exception:
            continue
    if not rows:
        raise RuntimeError("Could not parse the export endpoint response")
    return rows


def fetch_data(force=False):
    if DATA["orders"] is not None and DATA["rates"] is not None and not force:
        return DATA["orders"], DATA["rates"]

    session = requests.Session()
    session.headers.update({"User-Agent": "AcmeLedgerAgent/1.0"})
    # Prefer export for speed; fall back to paging through all 85 pages.
    orders = None
    try:
        r = session.get(EXPORT_URL, timeout=8)
        r.raise_for_status()
        orders = parse_export_response(r)
    except Exception:
        collected = []
        url = ORDERS_URL
        seen = set()
        for _ in range(100):
            r = session.get(url, timeout=8)
            r.raise_for_status()
            payload = r.json()
            collected.extend(payload.get("orders", []))
            nxt = payload.get("next")
            if not nxt or nxt in seen:
                break
            seen.add(nxt)
            url = nxt
        orders = collected

    r = session.get(RATES_URL, timeout=8)
    r.raise_for_status()
    rates_payload = r.json()
    rates = rates_payload.get("usd_per_unit", {})
    if not orders or not rates:
        raise RuntimeError("The orders or rates endpoint returned no usable data")

    # Deduplicate by order ID: keep the row with the latest updated_at timestamp.
    latest = {}
    for order in orders:
        oid = order.get("id")
        if not oid:
            continue
        current = latest.get(oid)
        if current is None or parse_datetime(order.get("updated_at")) > parse_datetime(current.get("updated_at")):
            latest[oid] = order

    DATA["orders"] = list(latest.values())
    DATA["rates"] = rates
    return DATA["orders"], DATA["rates"]


def parse_datetime(value):
    if not value:
        return datetime.min.replace(tzinfo=TZ)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def month_and_year(question):
    q = question.lower()
    month = None
    for name, num in MONTHS.items():
        if re.search(r"\b" + re.escape(name) + r"\b", q):
            month = num
            break
    years = re.findall(r"\b(20\d{2})\b", q)
    return month, int(years[0]) if years else None


def filters_from_question(question, orders):
    q = question.lower()
    month, year = month_and_year(q)
    region = None
    for r in sorted({str(o.get("region", "")) for o in orders}, key=len, reverse=True):
        if r and re.search(r"\b" + re.escape(r.lower()) + r"\b", q):
            region = r
            break

    product = None
    for p in sorted({str(o.get("product", "")) for o in orders}, key=len, reverse=True):
        if p and p.lower() in q:
            product = p
            break

    customer = None
    m = re.search(r"\bC\d{4}\b", question, re.I)
    if m:
        customer = m.group(0).upper()

    filtered = []
    for o in orders:
        dt = parse_datetime(o.get("created_at"))
        if month and dt.month != month:
            continue
        if year and dt.year != year:
            continue
        if region and o.get("region") != region:
            continue
        if product and o.get("product") != product:
            continue
        if customer and o.get("customer", "").upper() != customer:
            continue
        filtered.append(o)
    return filtered, {"month": month, "year": year, "region": region,
                      "product": product, "customer": customer}


def usd_amount(order, rates):
    currency = str(order.get("currency", "USD")).upper()
    rate = float(rates.get(currency, 0))
    return float(order.get("amount", 0)) * rate


def answer_question(question, orders, rates):
    q = question.lower().strip()
    selected, filt = filters_from_question(question, orders)

    # The API's documented revenue rule: only paid orders count as revenue.
    if any(w in q for w in ("refund", "refunded", "refunds", "returned")):
        rows = [o for o in selected if str(o.get("status", "")).lower() == "refunded"]
        if any(w in q for w in ("how many", "number of", "count", "number")) and "amount" not in q and "value" not in q and "total" not in q:
            return len(rows)
        if any(w in q for w in ("which product", "product had", "most refunded", "most refunds", "highest refund")):
            totals = {}
            for o in rows:
                totals[o["product"]] = totals.get(o["product"], 0) + usd_amount(o, rates)
            return max(totals, key=totals.get) if totals else ""
        return round(sum(usd_amount(o, rates) for o in rows), 2)

    if any(w in q for w in ("revenue", "sales", "turnover", "earned", "income")) or ("total" in q and "amount" in q):
        rows = [o for o in selected if str(o.get("status", "")).lower() == "paid"]
        # Product/region/customer ranking questions are handled separately below.
        if any(w in q for w in ("which product", "best-selling product", "top product", "most revenue", "highest sales", "most sales")):
            totals = {}
            for o in rows:
                totals[o["product"]] = totals.get(o["product"], 0) + usd_amount(o, rates)
            return max(totals, key=totals.get) if totals else ""
        if any(w in q for w in ("which customer", "top customer", "customer generated", "highest-spending customer")):
            totals = {}
            for o in rows:
                totals[o["customer"]] = totals.get(o["customer"], 0) + usd_amount(o, rates)
            return max(totals, key=totals.get) if totals else ""
        return round(sum(usd_amount(o, rates) for o in rows), 2)

    if any(w in q for w in ("how many customers", "number of customers", "unique customers", "distinct customers")):
        return len({o.get("customer") for o in selected})
    if any(w in q for w in ("how many orders", "number of orders", "order count", "count orders", "count of orders")):
        return len(selected)
    if any(w in q for w in ("units sold", "items sold", "quantity sold", "total quantity", "total units")):
        return sum(int(o.get("qty", 0)) for o in selected if o.get("status") == "paid")
    if any(w in q for w in ("average order", "average value", "mean order")):
        rows = [o for o in selected if o.get("status") == "paid"]
        return round(sum(usd_amount(o, rates) for o in rows) / len(rows), 2) if rows else 0
    if any(w in q for w in ("most orders", "most popular product", "best selling", "best-selling", "most sold")):
        totals = {}
        for o in selected:
            if o.get("status") == "paid":
                totals[o["product"]] = totals.get(o["product"], 0) + int(o.get("qty", 0))
        return max(totals, key=totals.get) if totals else ""
    if any(w in q for w in ("which product", "top product", "most revenue", "highest sales")):
        rows = [o for o in selected if o.get("status") == "paid"]
        totals = {}
        for o in rows:
            totals[o["product"]] = totals.get(o["product"], 0) + usd_amount(o, rates)
        return max(totals, key=totals.get) if totals else ""

    # Safe default: return a useful count rather than fabricate a value.
    return len(selected)


@app.get("/")
def health():
    return {"status": "ok", "service": "Acme Appliances order ledger Q&A", "endpoint": "POST /"}


@app.post("/")
def ask(req: QuestionRequest):
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        orders, rates = fetch_data()
        answer = answer_question(question, orders, rates)
        return {"answer": answer}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not query ledger data: {str(exc)}")
