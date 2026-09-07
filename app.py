"""
YRC News API — ดึงข่าวประชาสัมพันธ์จากเว็บโรงเรียนยุพราชวิทยาลัย ออกมาเป็น JSON

รัน:  uvicorn app:app --reload --port 8000
ดู:   http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

BASE_URL = "https://www.yupparaj.ac.th/"
LIST_URL = urljoin(BASE_URL, "news-total.php")
DETAIL_URL = urljoin(BASE_URL, "news_detail.php")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YRCLifeNewsAPI/1.0)"}
CACHE_TTL = 600  # วินาที

app = FastAPI(
    title="YRC News API",
    description="ข่าวประชาสัมพันธ์โรงเรียนยุพราชวิทยาลัย ในรูปแบบ JSON",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, producer):
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    value = producer()
    _cache[key] = (now, value)
    return value


def fetch(url: str, params: dict | None = None) -> BeautifulSoup:
    try:
        r = httpx.get(url, params=params, headers=HEADERS, timeout=20.0, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ดึงข้อมูลจากเว็บโรงเรียนไม่สำเร็จ: {e}") from e
    r.encoding = r.encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_thai_date(raw: str) -> dict[str, Any]:
    """'01/09/2569' (พ.ศ.) -> {'raw': ..., 'iso': '2026-09-01'}"""
    out = {"raw": clean(raw), "iso": None}
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", out["raw"])
    if not m:
        return out
    day, month, year = (int(x) for x in m.groups())
    if year > 2400:  # พ.ศ. -> ค.ศ.
        year -= 543
    try:
        out["iso"] = datetime(year, month, day).date().isoformat()
    except ValueError:
        pass
    return out


def parse_list(soup: BeautifulSoup) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="news_detail.php?id="]'):
        m = re.search(r"id=(\d+)", a["href"])
        if not m:
            continue
        news_id = m.group(1)
        if news_id in seen:
            continue
        seen.add(news_id)

        li = a.find_parent("li")
        label = li.find("label") if li else None
        date = parse_thai_date(label.get_text() if label else "")

        items.append(
            {
                "id": int(news_id),
                "title": clean(a.get_text()),
                "date": date["raw"],
                "date_iso": date["iso"],
                "url": urljoin(BASE_URL, a["href"]),
                "api_url": f"/news/{news_id}",
            }
        )
    return items


def field_after(soup: BeautifulSoup, label: str) -> str:
    """หยิบค่าจาก <p><strong>label :</strong> ค่า</p>"""
    for p in soup.find_all("p"):
        strong = p.find("strong")
        if strong and label in strong.get_text():
            return clean(p.get_text().replace(clean(strong.get_text()), "", 1))
    return ""


def parse_detail(soup: BeautifulSoup, news_id: int) -> dict[str, Any]:
    h3 = soup.select_one("h3.text-left")
    if h3 is None:
        raise HTTPException(status_code=404, detail=f"ไม่พบข่าว id={news_id}")

    title = re.sub(r"^เรื่อง\s*", "", clean(h3.get_text()))
    date = parse_thai_date(field_after(soup, "แก้ไขล่าสุด"))

    files: list[dict[str, str]] = []
    for p in soup.find_all("p"):
        strong = p.find("strong")
        if strong and "ดาวน์โหลดเอกสาร" in strong.get_text():
            for a in p.find_all("a", href=True):
                files.append({"name": clean(a.get_text()), "url": urljoin(BASE_URL, a["href"])})

    for obj in soup.select("object[data]"):
        url = urljoin(BASE_URL, obj["data"].lstrip("./"))
        if url not in {f["url"] for f in files}:
            files.append({"name": title, "url": url})

    body = soup.select_one("div.news-content")
    images = [
        urljoin(BASE_URL, img["src"])
        for img in (body.find_all("img", src=True) if body else [])
        if img["src"].strip()
    ]

    return {
        "id": news_id,
        "title": title,
        "author": field_after(soup, "เขียนโดย"),
        "date": date["raw"],
        "date_iso": date["iso"],
        "content": clean(body.get_text()) if body else "",
        "content_html": body.decode_contents().strip() if body else "",
        "files": files,
        "images": images,
        "url": f"{DETAIL_URL}?id={news_id}",
    }


@app.get("/")
def root():
    return {
        "name": "YRC News API",
        "source": LIST_URL,
        "endpoints": {
            "GET /news": "รายการข่าว (query: limit, offset, q)",
            "GET /news/{id}": "รายละเอียดข่าว 1 รายการ",
            "GET /health": "สถานะ",
            "GET /docs": "เอกสาร Swagger",
        },
    }


@app.get("/health")
def health():
    return {"status": "ok", "cached_keys": len(_cache)}


@app.get("/news")
def list_news(
    limit: int = Query(20, ge=1, le=200, description="จำนวนข่าวที่ต้องการ"),
    offset: int = Query(0, ge=0, description="ข้ามข่าวกี่รายการ"),
    q: str | None = Query(None, description="คำค้นในหัวข้อข่าว"),
):
    items = cached("list", lambda: parse_list(fetch(LIST_URL)))
    if q:
        needle = q.strip().lower()
        items = [i for i in items if needle in i["title"].lower()]
    return {
        "source": LIST_URL,
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "items": items[offset : offset + limit],
    }


@app.get("/news/{news_id}")
def get_news(news_id: int):
    return cached(
        f"detail:{news_id}",
        lambda: parse_detail(fetch(DETAIL_URL, {"id": news_id}), news_id),
    )
