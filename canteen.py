"""
YRC Canteen API — ล็อกอินระบบโรงอาหาร https://canteen.yupparaj.ac.th
แล้วดึงยอดเงินในบัตร + ประวัติการใช้จ่ายทั้งหมด ออกมาเป็น JSON

ต่อเป็น router เข้ากับ app.py ที่ path /canteen
รหัส (username/password) ส่งใน body ของ POST เท่านั้น ไม่รับผ่าน query string
ใช้ตัวช่วยยิง request แบบทน Cloudflare (retry+backoff) ร่วมกับโมดูล student
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from student import HEADERS, TIMEOUT, _request, clean

BASE_URL = "https://canteen.yupparaj.ac.th/"
LOGIN_URL = BASE_URL + "login"
DASHBOARD_URL = BASE_URL + "student/dashboard"
HISTORY_URL = BASE_URL + "student/history"

router = APIRouter(prefix="/canteen", tags=["canteen"])


class CanteenCredentials(BaseModel):
    username: str = Field(..., description="ชื่อผู้ใช้ระบบโรงอาหาร (ปกติคือรหัสประจำตัวนักเรียน)")
    password: str = Field(..., description="รหัสผ่านระบบโรงอาหาร")
    user_type: str = Field("student", description="ประเภทผู้ใช้: student / seller / admin")


class HistoryRequest(CanteenCredentials):
    start_date: str | None = Field(None, description="กรองจากวันที่ (YYYY-MM-DD) ไม่ใส่ = ทั้งหมด")
    end_date: str | None = Field(None, description="กรองถึงวันที่ (YYYY-MM-DD) ไม่ใส่ = ทั้งหมด")


# ---------- utils ----------

def _money(text: str | None) -> float | None:
    """'-฿35.00' / '฿600.00' -> -35.0 / 600.0"""
    if not text:
        return None
    m = re.search(r"([-+]?)\s*฿\s*([\d,]+(?:\.\d+)?)", text)
    if not m:
        return None
    val = float(m.group(2).replace(",", ""))
    return -val if m.group(1) == "-" else val


def _dt_iso(raw: str) -> str | None:
    """'02/09/2026 12:19' (ค.ศ.) -> '2026-09-02T12:19'"""
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+(\d{1,2}):(\d{2})", raw)
    if not m:
        return None
    d, mo, y, hh, mm = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, hh, mm).isoformat(timespec="minutes")
    except ValueError:
        return None


# ---------- login ----------

def login(cred: CanteenCredentials) -> httpx.Client:
    """ล็อกอินโรงอาหารแล้วคืน httpx.Client ที่ถือ session ไว้ ผู้เรียกต้อง close เอง"""
    client = httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    try:
        page = _request(client, "GET", LOGIN_URL)
        page.raise_for_status()
        token_el = BeautifulSoup(page.text, "html.parser").select_one('input[name="_csrf_token"]')
        if token_el is None or not token_el.get("value"):
            raise HTTPException(status_code=502, detail="หาช่อง _csrf_token ในหน้า login โรงอาหารไม่เจอ")

        resp = _request(
            client,
            "POST",
            LOGIN_URL,
            data={
                "_csrf_token": token_el["value"],
                "user_type": cred.user_type,
                "username": cred.username,
                "password": cred.password,
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        client.close()
        raise HTTPException(
            status_code=502,
            detail=f"ระบบโรงอาหารตอบกลับผิดพลาด ({e.response.status_code}) — อาจโดน Cloudflare บล็อกชั่วคราว ลองใหม่อีกครั้ง",
        ) from e
    except httpx.HTTPError as e:
        client.close()
        raise HTTPException(status_code=502, detail=f"เชื่อมต่อระบบโรงอาหารไม่สำเร็จ: {e}") from e

    # ล็อกอินสำเร็จจะ redirect ไป /student/dashboard; ล้มเหลวจะยังอยู่ที่ /login
    if resp.url.path.rstrip("/").endswith("/login"):
        client.close()
        raise HTTPException(status_code=401, detail="username หรือ password ไม่ถูกต้อง")

    return client


def _get_soup(client: httpx.Client, url: str) -> BeautifulSoup:
    try:
        r = _request(client, "GET", url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ดึงหน้า {url} ไม่สำเร็จ: {e}") from e
    if r.url.path.rstrip("/").endswith("/login"):
        raise HTTPException(status_code=401, detail="session หมดอายุระหว่างดึงข้อมูล")
    return BeautifulSoup(r.text, "html.parser")


# ---------- parsers ----------

def parse_account(soup: BeautifulSoup) -> dict[str, Any]:
    """ดึงชื่อ / รหัสบัตร / ยอดคงเหลือ จากหน้า dashboard"""
    text = clean(soup.get_text(" "))
    name = None
    m = re.search(r"(นาย|นางสาว|นาง|เด็กชาย|เด็กหญิง)\s*\S.*?\S(?=\s+(?:รหัสบัตร|หน้าหลัก|ยอดเงิน))", text)
    if m:
        name = clean(m.group(0))
    card_m = re.search(r"รหัสบัตร[:\s]*([0-9]+)", text)
    bal_m = re.search(r"ยอดเงินคงเหลือ\s*(฿[\d,]+\.\d{2})", text) or re.search(r"ยอดคงเหลือ\s*(฿[\d,]+\.\d{2})", text)
    return {
        "name": name,
        "card_id": card_m.group(1) if card_m else None,
        "balance": _money(bal_m.group(1)) if bal_m else None,
    }


def parse_transactions(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """อ่านรายการธุรกรรมทั้งหมดจากหน้า history (การ์ดแต่ละใบ)"""
    items: list[dict[str, Any]] = []
    for p_type in soup.select("p.font-medium.text-gray-900"):
        info = p_type.parent
        if info is None:
            continue
        date_p = info.find("p", class_="text-gray-500")
        if date_p is None:
            continue
        dt_raw = clean(date_p.get_text())
        if not re.search(r"\d{2}/\d{2}/\d{4}", dt_raw):
            continue  # ไม่ใช่การ์ดธุรกรรม

        span = p_type.find("span")
        vendor = clean(span.get_text()).lstrip("@ ").strip() if span else None
        tx_type = clean(p_type.get_text().replace(span.get_text(), "") if span else p_type.get_text())

        # ไต่ขึ้นหาแถวที่มีทั้งจำนวนเงินและ "คงเหลือ"
        row = info
        for _ in range(4):
            row = row.parent
            if row is None:
                break
            if "คงเหลือ" in row.get_text():
                break
        row_text = clean(row.get_text()) if row else ""

        amount = balance_after = None
        amt_m = re.search(r"([-+]?)\s*฿\s*[\d,]+\.\d{2}", row_text)
        if amt_m:
            amount = _money(amt_m.group(0))
        bal_m = re.search(r"คงเหลือ\s*(฿[\d,]+\.\d{2})", row_text)
        if bal_m:
            balance_after = _money(bal_m.group(1))

        items.append({
            "type": tx_type,
            "vendor": vendor,
            "datetime": dt_raw,
            "datetime_iso": _dt_iso(dt_raw),
            "amount": amount,
            "balance_after": balance_after,
        })
    return items


def parse_history_summary(soup: BeautifulSoup) -> dict[str, Any]:
    text = clean(soup.get_text(" "))

    def grab(label: str) -> float | None:
        m = re.search(re.escape(label) + r"\s*(฿[\d,]+\.\d{2})", text)
        return _money(m.group(1)) if m else None

    count_m = re.search(r"รายการทั้งหมด\s*\((\d+)", text)
    return {
        "balance": grab("ยอดคงเหลือ"),
        "total_topup": grab("ยอดเติมเงินรวม"),
        "total_spent": grab("ยอดใช้จ่ายรวม"),
        "total_refund": grab("ยอดคืนเงินรวม"),
        "count": int(count_m.group(1)) if count_m else None,
    }


# ---------- endpoints ----------

@router.post("/balance", summary="ยอดเงินในบัตร + ข้อมูลบัญชี")
def canteen_balance(cred: CanteenCredentials):
    """ล็อกอินแล้วคืนชื่อ / รหัสบัตร / ยอดเงินคงเหลือ"""
    client = login(cred)
    try:
        account = parse_account(_get_soup(client, DASHBOARD_URL))
    finally:
        client.close()
    return {"ok": True, "account": account}


@router.post("/history", summary="ยอดเงิน + ประวัติการใช้จ่ายทั้งหมด")
def canteen_history(req: HistoryRequest):
    """
    ล็อกอินแล้วดึงยอดเงินคงเหลือ, สรุปยอด (เติม/ใช้จ่าย/คืน), และรายการธุรกรรมทั้งหมด
    ใส่ start_date/end_date (YYYY-MM-DD) เพื่อกรองช่วงวันที่ ไม่ใส่ = ดึงทั้งหมด
    """
    client = login(req)
    try:
        account = parse_account(_get_soup(client, DASHBOARD_URL))
        params: dict[str, str] = {}
        if req.start_date:
            params["start_date"] = req.start_date
        if req.end_date:
            params["end_date"] = req.end_date
        try:
            r = _request(client, "GET", HISTORY_URL, params=params or None)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"ดึงประวัติไม่สำเร็จ: {e}") from e
        soup = BeautifulSoup(r.text, "html.parser")
        summary = parse_history_summary(soup)
        transactions = parse_transactions(soup)
    finally:
        client.close()

    return {
        "account": account,
        "summary": summary,
        "transactions": transactions,
    }
