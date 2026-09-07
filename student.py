"""
YRC Student Portal API — ล็อกอินเข้า https://portal.yupparaj.ac.th
แล้วดึงข้อมูลนักเรียนของบัญชีนั้นออกมาเป็น JSON

ใช้เป็น router ต่อเข้ากับ app.py ที่ path /student
รหัส (username/password) ต้องส่งมาใน "body" ของ POST เท่านั้น ไม่รับผ่าน query string
เพื่อกันรหัสหลุดไปติดใน log/URL
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

BASE_URL = "https://portal.yupparaj.ac.th/"
LOGIN_URL = BASE_URL + "login.php"
LOGIN_HANDLER = BASE_URL + "app/login_handler.php"
INDEX_URL = BASE_URL + "index.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; YRCLifeStudentAPI/1.0)"}
TIMEOUT = 25.0

# ค่าที่ถือว่า "ยังไม่ได้เลือก/ว่าง" ใน dropdown ของพอร์ทัล -> แปลงเป็น None
PLACEHOLDERS = {
    "", "none", "เลือก", "เลือกวัน", "เลือกเดือน", "เลือกปี",
    "-- เลือก --", "กรุณาเลือก", "โปรดเลือก",
}

# ทะเบียนหน้าข้อมูล: key -> (ไฟล์ php, ชนิดการอ่าน)
SECTIONS: dict[str, tuple[str, str]] = {
    "personal": ("edit_data.php", "form"),
    "family": ("family.php", "form"),
    "nutrition": ("nutritional_status.php", "form"),
    "talent": ("talent.php", "table"),
    "disability": ("disability.php", "table"),
    "clubs": ("learner_clubs.php", "table"),
    "second_language": ("second_language_subjects.php", "form"),
    "behavior": ("student_behavior_report.php", "behavior"),
}

# ตัวย่อเดือนไทย -> เลขเดือน (ใช้แปลงวันที่ในรายงานพฤติกรรม)
THAI_MONTHS_ABBR = {
    "ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
    "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12,
}
DEFAULT_SECTIONS = ["personal", "family", "nutrition", "talent", "disability", "clubs", "behavior"]

router = APIRouter(prefix="/student", tags=["student"])


# ---------- utils ----------

def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _nullify(value: str | None) -> str | None:
    """แปลงค่า placeholder/ว่าง เป็น None"""
    v = clean(value)
    return None if v.lower() in PLACEHOLDERS else v


class Credentials(BaseModel):
    username: str = Field(..., description="รหัสประจำตัวนักเรียน (username พอร์ทัล)")
    password: str = Field(..., description="รหัสผ่านพอร์ทัล")


class DataRequest(Credentials):
    sections: list[str] | None = Field(
        None, description="หมวดที่ต้องการ (ไม่ใส่ = ใช้ค่าเริ่มต้น)"
    )


# ---------- login ----------

def login(username: str, password: str) -> httpx.Client:
    """
    ล็อกอินพอร์ทัลแล้วคืน httpx.Client ที่ถือ session (คุกกี้) ไว้แล้ว
    ผู้เรียกต้อง client.close() เองเมื่อใช้เสร็จ
    โยน HTTPException 401 ถ้ารหัสผิด, 502 ถ้าเชื่อมต่อเว็บไม่ได้
    """
    client = httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    try:
        page = client.get(LOGIN_URL)
        page.raise_for_status()
        token_el = BeautifulSoup(page.text, "html.parser").select_one('input[name="csrf_token"]')
        if token_el is None or not token_el.get("value"):
            raise HTTPException(status_code=502, detail="หาช่อง csrf_token ในหน้า login ไม่เจอ (เว็บอาจเปลี่ยนโครงสร้าง)")

        resp = client.post(
            LOGIN_HANDLER,
            data={
                "username": username,
                "password": password,
                "Login": "",
                "csrf_token": token_el["value"],
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        client.close()
        raise HTTPException(status_code=502, detail=f"เชื่อมต่อพอร์ทัลไม่สำเร็จ: {e}") from e

    # ล็อกอินสำเร็จจะ redirect ไป index.php; ถ้ารหัสผิดจะถูกส่งกลับหน้า login.php
    if resp.url.path.endswith("login.php"):
        client.close()
        raise HTTPException(status_code=401, detail="username หรือ password ไม่ถูกต้อง")

    return client


def _get_soup(client: httpx.Client, url: str) -> BeautifulSoup:
    try:
        r = client.get(url)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ดึงหน้า {url} ไม่สำเร็จ: {e}") from e
    if r.url.path.endswith("login.php"):
        raise HTTPException(status_code=401, detail="session หมดอายุระหว่างดึงข้อมูล")
    return BeautifulSoup(r.text, "html.parser")


# ---------- parsers ----------

def parse_profile(soup: BeautifulSoup) -> dict[str, Any]:
    """ดึงชื่อ / เลขประจำตัว / ชั้น / ปีการศึกษา จาก header ของ index.php"""
    student_id = class_room = academic_year = None
    for p in soup.find_all(["p", "span", "div", "h1", "h2", "h3", "h4"]):
        t = clean(p.get_text())
        if student_id is None and (m := re.search(r"เลขประจำตัว\s*(\S+)", t)):
            student_id = m.group(1)
        if class_room is None and (m := re.search(r"ชั้น\s*(ม\.?\s*[\d/]+)", t)):
            class_room = clean(m.group(1))

    # ชื่อ: หา element ที่ขึ้นต้นด้วยคำนำหน้าไทยและสั้นๆ (ไม่ใช่ทั้งบล็อก)
    full_name = None
    for el in soup.find_all(string=re.compile(r"^\s*(นาย|นางสาว|นาง|เด็กชาย|เด็กหญิง)\s")):
        cand = clean(el)
        if 3 < len(cand) <= 60 and "เลขประจำตัว" not in cand:
            full_name = cand
            break

    # ปีการศึกษา: อ่านจาก hidden input year บนหน้าฟอร์มไม่ได้ที่นี่ ใช้จาก edit_data ภายหลัง
    year_el = soup.select_one('input[name="year"]')
    if year_el and year_el.get("value"):
        academic_year = year_el["value"]

    return {
        "full_name": full_name,
        "student_id": student_id,
        "class_room": class_room,
        "academic_year": academic_year,
    }


def read_form(soup: BeautifulSoup) -> dict[str, Any]:
    """
    อ่านค่าปัจจุบันของทุกฟิลด์ในฟอร์มที่กรอกข้อมูลไว้ล่วงหน้า -> dict
    - input text/tel/email/number/date : ใช้ค่า value
    - radio/checkbox : เอาอันที่ถูกเลือก (checked) พร้อม label ข้างๆ
    - select : เอา option ที่ selected
    - textarea : เอาเนื้อหาข้างใน
    ข้าม field ระบบ (csrf_token / st_id / submit / ปุ่ม)
    """
    skip = {"csrf_token", "st_id", "login"}
    data: dict[str, Any] = {}

    forms = soup.find_all("form")
    fields = []
    for f in forms:
        fields += f.find_all(["input", "select", "textarea"])

    for el in fields:
        name = el.get("name")
        if not name:
            continue
        key = name.rstrip("[]")
        if key.lower() in skip or key.endswith("_hidden"):
            continue

        if el.name == "input":
            itype = (el.get("type") or "text").lower()
            if itype in ("submit", "button", "reset", "image", "file"):
                continue
            if itype in ("radio", "checkbox"):
                if el.has_attr("checked"):
                    label = _radio_label(el)
                    if el.get("type") == "checkbox":
                        data.setdefault(key, []).append(label)
                    else:
                        data[key] = label
                else:
                    data.setdefault(key, data.get(key))  # คงคีย์ไว้เป็น None ถ้ายังไม่มีค่า
            else:
                val = _nullify(el.get("value"))
                # source_friends ฯลฯ เก็บ '[]' ให้ข้าม
                if val == "[]":
                    val = None
                data[key] = val if val is not None else data.get(key)

        elif el.name == "select":
            opt = el.find("option", selected=True) or _first_nonplaceholder_selected(el)
            data[key] = _nullify(opt.get_text()) if opt else None

        elif el.name == "textarea":
            data[key] = _nullify(el.get_text())

    return data


def _first_nonplaceholder_selected(select) -> Any:
    for opt in select.find_all("option"):
        if opt.has_attr("selected"):
            return opt
    return None


def _radio_label(inp) -> str | None:
    """หา label ของ radio/checkbox: จาก <label for=id> ก่อน ไม่งั้นใช้ข้อความถัดไป"""
    _id = inp.get("id")
    if _id:
        lab = inp.find_parent().find("label", {"for": _id}) if inp.find_parent() else None
        if lab:
            return clean(lab.get_text())
    sib = inp.find_next(string=lambda s: s and s.strip())
    return clean(sib) if sib else _nullify(inp.get("value"))


def read_table(soup: BeautifulSoup) -> list[dict[str, str]]:
    """อ่านตารางแรกที่มีข้อมูลจริง -> list ของ dict (คีย์ = หัวตาราง)"""
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [clean(c.get_text()) for c in rows[0].find_all(["th", "td"])]
        out: list[dict[str, str]] = []
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            values = [clean(c.get_text()) for c in cells]
            if not any(values):
                continue
            if len(values) == 1 and "ไม่มีข้อมูล" in values[0]:
                continue  # ตารางว่าง
            row = {}
            for i, v in enumerate(values):
                col = headers[i] if i < len(headers) and headers[i] else f"col{i}"
                row[col] = v
            out.append(row)
        return out
    return []


def _behavior_date_iso(raw: str) -> str | None:
    """'07 ก.พ. 68 เวลา ...' -> '2025-02-07' (ปี 2 หลักเป็น พ.ศ.)"""
    m = re.match(r"\s*(\d{1,2})\s+([ก-ฮ.]+)\s+(\d{2,4})", raw)
    if not m:
        return None
    day, mon_abbr, year = m.group(1), m.group(2), int(m.group(3))
    month = THAI_MONTHS_ABBR.get(mon_abbr)
    if not month:
        return None
    if year < 100:      # '68' -> 2568
        year += 2500
    if year > 2400:     # พ.ศ. -> ค.ศ.
        year -= 543
    try:
        from datetime import date
        return date(year, month, int(day)).isoformat()
    except ValueError:
        return None


def read_behavior(client: httpx.Client) -> dict[str, Any]:
    """ดึงคะแนนพฤติกรรมทุกช่วงเวลา (report_type=all_time) พร้อมรายการหักทุกครั้งและสรุป"""
    url = BASE_URL + "student_behavior_report.php"
    soup = _get_soup(client, url)
    form = soup.find("form")
    if form is None:
        raise HTTPException(status_code=502, detail="หาฟอร์มรายงานพฤติกรรมไม่เจอ")

    # เอาค่าปัจจุบันของฟอร์มมาเป็นฐาน แล้วบังคับ report_type=all_time
    payload: dict[str, str] = {}
    for el in form.find_all(["input", "select"]):
        name = el.get("name")
        if not name or (el.get("type") or "").lower() in ("submit", "button"):
            continue
        if el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            payload[name] = opt.get("value", "") if opt else ""
        else:
            payload[name] = el.get("value", "") or ""
    payload["report_type"] = "all_time"
    payload["SearchCheck"] = ""

    try:
        r = client.post(url, data=payload)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ดึงรายงานพฤติกรรมไม่สำเร็จ: {e}") from e
    res = BeautifulSoup(r.text, "html.parser")

    # รายการหักคะแนนแต่ละครั้ง (ข้ามแถวสรุป 'รวมคะแนนที่ถูกหัก:')
    records: list[dict[str, Any]] = []
    total_deducted: int | None = None
    table = res.find("table")
    if table:
        rows = table.find_all("tr")
        headers = [clean(c.get_text()) for c in rows[0].find_all(["th", "td"])] if rows else []
        for tr in rows[1:]:
            cells = [clean(c.get_text()) for c in tr.find_all(["td", "th"])]
            if not any(cells):
                continue
            if "รวมคะแนนที่ถูกหัก" in cells[0]:
                nums = re.findall(r"-?\d+", " ".join(cells))
                total_deducted = int(nums[-1]) if nums else None
                continue
            if len(cells) >= 5:
                records.append({
                    "no": cells[0],
                    "date": cells[1],
                    "date_iso": _behavior_date_iso(cells[1]),
                    "behavior": cells[2],
                    "level": cells[3],
                    "points": int(m.group()) if (m := re.search(r"\d+", cells[4])) else None,
                })

    # สรุปคะแนน
    text = "\n".join(l.strip() for l in res.get_text("\n").splitlines() if l.strip())
    def _grab(label: str) -> int | None:
        m = re.search(label + r"\s*\n?\s*(-?\d+)", text)
        return int(m.group(1)) if m else None

    carried = _grab("คะแนนยกมา")
    deducted = _grab("คะแนนถูกหัก")
    remaining = _grab(r"คงเหลือในช่วงที่เลือก")
    rating = None
    if remaining is not None:
        rm = re.search(r"คงเหลือในช่วงที่เลือก\s*\n?\s*-?\d+\s*\n?\s*([^\n\d]+)", text)
        rating = clean(rm.group(1)) if rm else None

    return {
        "starting_points": 100,
        "carried_points": carried,
        "deducted_points": deducted,
        "total_deducted": total_deducted,
        "remaining_points": remaining,
        "rating": rating,
        "records": records,
    }


def read_section(client: httpx.Client, key: str) -> Any:
    page, kind = SECTIONS[key]
    if kind == "behavior":
        return read_behavior(client)
    soup = _get_soup(client, BASE_URL + page)
    return read_table(soup) if kind == "table" else read_form(soup)


# ---------- endpoints ----------

@router.post("/login", summary="ตรวจรหัสและดึงข้อมูลพื้นฐานของนักเรียน")
def student_login(cred: Credentials):
    """ล็อกอินเพื่อเช็ครหัส แล้วคืนโปรไฟล์พื้นฐาน (ชื่อ/เลขประจำตัว/ชั้น)"""
    client = login(cred.username, cred.password)
    try:
        profile = parse_profile(_get_soup(client, INDEX_URL))
    finally:
        client.close()
    return {"ok": True, "profile": profile}


@router.post("/data", summary="ดึงข้อมูลนักเรียนตามหมวดที่เลือก")
def student_data(cred: DataRequest):
    """
    ล็อกอินครั้งเดียวแล้วดึงข้อมูลตามหมวดที่ระบุใน `sections`
    (ถ้าไม่ระบุ ใช้ค่าเริ่มต้น: personal, family, nutrition, talent, disability, clubs)

    หมวดที่มี: personal, family, nutrition, talent, disability, clubs,
    second_language, behavior
    """
    wanted = cred.sections or DEFAULT_SECTIONS
    unknown = [s for s in wanted if s not in SECTIONS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"ไม่รู้จักหมวด: {unknown} — หมวดที่มี: {list(SECTIONS)}",
        )

    client = login(cred.username, cred.password)
    try:
        result: dict[str, Any] = {"profile": parse_profile(_get_soup(client, INDEX_URL)), "sections": {}}
        for key in wanted:
            result["sections"][key] = read_section(client, key)
    finally:
        client.close()
    return result


@router.get("/sections", summary="รายชื่อหมวดข้อมูลที่ดึงได้")
def list_sections():
    return {
        "sections": [
            {"key": k, "page": v[0], "type": v[1], "default": k in DEFAULT_SECTIONS}
            for k, v in SECTIONS.items()
        ]
    }
