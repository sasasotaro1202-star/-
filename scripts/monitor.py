import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import quote, urlparse, parse_qs
from urllib.request import Request, urlopen

JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST).replace(microsecond=0)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "Mozilla/5.0 (compatible; BF-PC-Price-Monitor/1.0)"

def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def fetch(url, timeout=20):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5"
    })
    with urlopen(req, timeout=timeout) as r:
        enc = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(enc, errors="replace"), r.geturl()

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            if d.get("href"):
                self.links.append(d["href"])

def search_web(query):
    html, _ = fetch("https://html.duckduckgo.com/html/?q=" + quote(query), 25)
    p = LinkParser()
    p.feed(html)
    urls = []
    seen = set()
    for href in p.links:
        if "uddg=" in href:
            u = parse_qs(urlparse(href).query).get("uddg")
            if u:
                href = u[0]
        if href.startswith("http") and href not in seen:
            seen.add(href)
            urls.append(href)
    return urls[:15]

def allowed(url, domains):
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in domains)

def jsonld(html):
    blocks = re.findall(
        r'<script[^>]+type=["\\\']application/ld\\+json["\\\'][^>]*>(.*?)</script>',
        html, re.I | re.S
    )
    out = []
    for block in blocks:
        try:
            x = json.loads(block.strip())
            if isinstance(x, list):
                out.extend(x)
            elif isinstance(x, dict):
                out.append(x)
        except Exception:
            pass
    return out

def extract_price(text):
    nums = []
    for m in re.findall(r'(?:¥|￥)\\s*([0-9]{2,3}(?:,[0-9]{3})+|[0-9]{5,7})', text):
        try:
            nums.append(int(m.replace(",", "")))
        except Exception:
            pass
    return min((x for x in nums if 50000 <= x <= 1000000), default=None)

def detect_stock(text):
    s = text.lower()
    if any(x in s for x in ["在庫切れ", "売り切れ", "out of stock", "sold out"]):
        return "out_of_stock"
    if any(x in s for x in ["残りわずか", "low stock", "only 1", "only one"]):
        return "low_stock"
    if any(x in s for x in ["取り寄せ", "予約", "preorder", "back order"]):
        return "preorder_or_backorder"
    if any(x in s for x in ["在庫あり", "in stock", "available"]):
        return "in_stock"
    return "unknown"

def extract_specs(text):
    s = " ".join(text.split())
    out = {}
    patterns = {
        "gpu": r'(RTX\\s*(?:5090|5080|5070\\s*Ti|5070|5060\\s*Ti|5060)\\s*Laptop(?:\\s*GPU)?)',
        "tgp_w": r'(?:TGP|Total Graphics Power|最大(?:GPU)?電力)[^0-9]{0,25}(\\d{2,3})\\s*W',
        "ram_gb": r'(?:メモリ|Memory|RAM)[^0-9]{0,15}(\\d{2,3})\\s*GB',
        "refresh_hz": r'(\\d{2,3})\\s*Hz'
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, s, re.I)
        if m:
            out[key] = int(m.group(1)) if key.endswith("_gb") or key.endswith("_w") or key.endswith("_hz") else m.group(1)
    m = re.search(r'(?:VRAM|ビデオメモリ)[^0-9]{0,20}(\\d{1,2})\\s*GB', s, re.I)
    if m:
        out["vram_gb"] = int(m.group(1))
    m = re.search(r'((?:Core\\s+Ultra\\s+[579])[^,;|]{0,35}|(?:Ryzen\\s+(?:AI\\s+)?[975])[^,;|]{0,35})', s, re.I)
    if m:
        out["cpu"] = m.group(1).strip()
    m = re.search(r'(?:SSD|ストレージ|Storage)[^0-9]{0,15}(\\d+(?:\\.\\d+)?)\\s*(TB|GB)', s, re.I)
    if m:
        out["ssd"] = m.group(1) + " " + m.group(2)
    return out

def parse_page(url, html):
    products = jsonld(html)
    name = None
    offer_price = None
    for obj in products:
        if "Product" in str(obj.get("@type", "")):
            name = obj.get("name") or name
            offers = obj.get("offers")
            if isinstance(offers, dict):
                try:
                    offer_price = int(float(str(offers.get("price")).replace(",", "")))
                except Exception:
                    pass
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\\s+", " ", text)
    return {
        "url": url,
        "store": urlparse(url).netloc.lower(),
        "name": name or url,
        "price_jpy": offer_price or extract_price(text),
        "stock_status": detect_stock(text),
        "spec": extract_specs(text[:300000]),
        "fetch_status": "ok"
    }

def main():
    cfg = read_json(os.path.join(ROOT, "config/products.json"), {})
    latest_path = os.path.join(ROOT, "data/current_latest.json")
    events_path = os.path.join(ROOT, "data/change_events.jsonl")
    previous = read_json(latest_path, {}).get("products", [])
    previous_by_url = {x["url"]: x for x in previous}

    urls = []
    for query in cfg.get("search_queries", []):
        try:
            urls.extend(search_web(query))
        except Exception:
            continue

    seen = set()
    found = []
    for url in urls:
        if url in seen or not allowed(url, cfg.get("allowed_domains", [])):
            continue
        seen.add(url)
        try:
            html, final_url = fetch(url, 20)
            item = parse_page(final_url, html)
            blob = (item["name"] + " " + json.dumps(item["spec"], ensure_ascii=False)).lower()
            if any(g in blob for g in ["rtx 5080", "rtx 5090", "rtx 5070 ti"]) and any(k in blob for k in ["laptop", "ノート", "gaming", "ゲーミング"]):
                fp_src = json.dumps({
                    "name": item["name"],
                    "price_jpy": item["price_jpy"],
                    "stock_status": item["stock_status"],
                    "spec": item["spec"]
                }, sort_keys=True, ensure_ascii=False)
                item["fingerprint"] = hashlib.sha256(fp_src.encode()).hexdigest()
                item["observed_at"] = NOW.isoformat()
                found.append(item)
        except Exception:
            continue
        if len(found) >= 50:
            break

    changes = []
    for item in found:
        prev = previous_by_url.get(item["url"])
        if not prev or prev.get("fingerprint") != item["fingerprint"]:
            changes.append({
                "observed_at": item["observed_at"],
                "url": item["url"],
                "store": item["store"],
                "name": item["name"],
                "old_price_jpy": prev.get("price_jpy") if prev else None,
                "new_price_jpy": item.get("price_jpy"),
                "old_stock": prev.get("stock_status") if prev else None,
                "new_stock": item.get("stock_status"),
                "old_spec": prev.get("spec") if prev else None,
                "new_spec": item.get("spec"),
                "event_type": "new_observation" if not prev else "change"
            })

    write_json(latest_path, {
        "generated_at": NOW.isoformat(),
        "products": found
    })

    if changes:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)
        with open(events_path, "a", encoding="utf-8") as f:
            for event in changes:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    report_path = os.path.join(ROOT, "data/reports/latest.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    candidates = [
        x for x in found
        if x.get("price_jpy") and x["price_jpy"] <= cfg.get("budget_jpy", 400000)
    ]
    candidates.sort(key=lambda x: x["price_jpy"])

    lines = [
        "# Latest observation",
        "",
        "Observed: " + NOW.isoformat(),
        "",
        "Observed products: " + str(len(found)),
        "Change events: " + str(len(changes)),
        ""
    ]
    for x in candidates[:10]:
        lines.append(
            "- " + x["name"] + " | ¥" + format(x["price_jpy"], ",") +
            " | " + x["stock_status"] + " | " + x["url"]
        )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(json.dumps({
        "observed_at": NOW.isoformat(),
        "found": len(found),
        "changes": len(changes)
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
