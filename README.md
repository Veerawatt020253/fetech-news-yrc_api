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

## หมายเหตุ

- `date` เป็น พ.ศ. ตามที่เว็บแสดง, `date_iso` แปลงเป็น ค.ศ. รูปแบบ `YYYY-MM-DD` ให้แล้ว
- ข่าวส่วนใหญ่ของเว็บโรงเรียนเป็น PDF ล้วน `content` จึงมักว่าง เนื้อหาจริงอยู่ใน `files`
- เปิด CORS ไว้ทุก origin (`GET`) เรียกจากหน้าเว็บ/แอปได้เลย
- โครงสร้าง HTML ของเว็บต้นทางเปลี่ยนเมื่อไหร่ ตัว selector ใน `app.py` ต้องแก้ตาม
# fetech-news-yrc_api
