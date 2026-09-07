# YRC News API

API ดึงข่าวประชาสัมพันธ์จาก https://www.yupparaj.ac.th/news-total.php ออกมาเป็น JSON
(FastAPI + BeautifulSoup, มี in-memory cache 10 นาที กันยิงเว็บโรงเรียนถี่เกินไป)

## ติดตั้ง & รัน

```bash
pip install -r requirements.txt
```

```bash
uvicorn app:app --reload --port 8000
```

เปิดเอกสาร Swagger ที่ http://127.0.0.1:8000/docs

## Endpoints

| Method | Path | คำอธิบาย |
|---|---|---|
| GET | `/news` | รายการข่าวทั้งหมด (100 รายการล่าสุดจากหน้าเว็บ) |
| GET | `/news/{id}` | รายละเอียดข่าว 1 รายการ + ไฟล์แนบ |
| POST | `/student/login` | ตรวจรหัส + ดึงโปรไฟล์นักเรียน |
| POST | `/student/data` | ดึงข้อมูลนักเรียนตามหมวดที่เลือก |
| GET | `/student/sections` | รายชื่อหมวดข้อมูลที่ดึงได้ |
| GET | `/health` | เช็คสถานะ |
| GET | `/docs` | Swagger UI |

### `GET /news`

query: `limit` (1–200, default 20), `offset` (default 0), `q` (ค้นจากหัวข้อข่าว)

```bash
curl "http://127.0.0.1:8000/news?limit=2"
```

```json
{
  "source": "https://www.yupparaj.ac.th/news-total.php",
  "total": 100,
  "limit": 2,
  "offset": 0,
  "items": [
    {
      "id": 648,
      "title": "ตารางสอบปลายภาคเรียนที่ 1 ปีการศึกษา 2569",
      "date": "01/09/2569",
      "date_iso": "2026-09-01",
      "url": "https://www.yupparaj.ac.th/news_detail.php?id=648",
      "api_url": "/news/648"
    }
  ]
}
```

### `GET /news/{id}`

```bash
curl "http://127.0.0.1:8000/news/648"
```

```json
{
  "id": 648,
  "title": "ตารางสอบปลายภาคเรียนที่ 1 ปีการศึกษา 2569",
  "author": "ฝ่ายประชาสัมพันธ์โรงเรียน",
  "date": "01/09/2569",
  "date_iso": "2026-09-01",
  "content": "",
  "content_html": "",
  "files": [
    {
      "name": "ตารางสอบปลายภาคเรียนที่ 1 ปีการศึกษา 2569",
      "url": "https://www.yupparaj.ac.th/uploads/pdf/1788343712_161_3242.pdf"
    }
  ],
  "images": [],
  "url": "https://www.yupparaj.ac.th/news_detail.php?id=648"
}
```

## Student Portal API

ดึงข้อมูลนักเรียนจาก https://portal.yupparaj.ac.th โดยล็อกอินด้วยรหัสของนักเรียนเอง
**รหัส (`username`/`password`) ส่งใน body ของ POST เท่านั้น** ไม่รับผ่าน query string
เพื่อกันรหัสหลุดไปติดใน log/URL และ API นี้ไม่ได้เก็บรหัสไว้ (ล็อกอินสดทุกครั้งแล้วปิด session ทิ้ง)

### `POST /student/login`

ตรวจว่ารหัสถูกไหม แล้วคืนโปรไฟล์พื้นฐาน

```bash
curl -X POST http://127.0.0.1:8000/student/login \
  -H "Content-Type: application/json" \
  -d '{"username":"53421","password":"53421"}'
```

```json
{
  "ok": true,
  "profile": {
    "full_name": "นาย วีราวรรธนุ์ กันธิพันธ์",
    "student_id": "53421",
    "class_room": "ม.5/5",
    "academic_year": null
  }
}
```

รหัสผิดจะได้ `401 {"detail": "username หรือ password ไม่ถูกต้อง"}`

### `POST /student/data`

ดึงข้อมูลตามหมวด — ใส่ `sections` เพื่อเลือกเอง ถ้าไม่ใส่ใช้ค่าเริ่มต้น
(`personal`, `family`, `nutrition`, `talent`, `disability`, `clubs`)

```bash
curl -X POST http://127.0.0.1:8000/student/data \
  -H "Content-Type: application/json" \
  -d '{"username":"53421","password":"53421","sections":["family","clubs"]}'
```

```json
{
  "profile": { "full_name": "...", "student_id": "53421", "class_room": "ม.5/5" },
  "sections": {
    "family": { "father_name": "อนุรักษ์", "mother_name": "จิราพร", "...": "..." },
    "clubs": [ { "กิจกรรม": "...", "ครูผู้รับผิดชอบ": "...", "...": "..." } ]
  }
}
```

หมวดที่มี (ดูได้จาก `GET /student/sections`):

| key | หน้า | รูปแบบผล |
|---|---|---|
| `personal` | ข้อมูลส่วนตัว | object |
| `family` | ข้อมูลครอบครัว | object |
| `nutrition` | ส่วนสูง/น้ำหนัก | object |
| `talent` | ความสามารถพิเศษ | list |
| `disability` | ความพิการ | list |
| `clubs` | กิจกรรมพัฒนาผู้เรียน/ชุมนุม | list |
| `second_language` | วิชาเลือกภาษาที่ 2 | object |
| `behavior` | คะแนนพฤติกรรมทุกช่วงเวลา (รายการหักทุกครั้ง + สรุปคงเหลือ) | object |

หมวด `behavior` จะยิงรายงานแบบ `all_time` ให้เอง คืนคะแนนที่ถูกหักทุกครั้ง
(วันที่ + พฤติกรรม + ระดับ + คะแนน) พร้อมสรุป `carried_points` / `deducted_points` /
`remaining_points` / `rating` — เริ่มต้นทุกคนที่ 100 คะแนน

## หมายเหตุ

- `date` เป็น พ.ศ. ตามที่เว็บแสดง, `date_iso` แปลงเป็น ค.ศ. รูปแบบ `YYYY-MM-DD` ให้แล้ว
- ข่าวส่วนใหญ่ของเว็บโรงเรียนเป็น PDF ล้วน `content` จึงมักว่าง เนื้อหาจริงอยู่ใน `files`
- เปิด CORS ไว้ทุก origin (`GET`) เรียกจากหน้าเว็บ/แอปได้เลย
- โครงสร้าง HTML ของเว็บต้นทางเปลี่ยนเมื่อไหร่ ตัว selector ใน `app.py` ต้องแก้ตาม
# fetech-news-yrc_api
