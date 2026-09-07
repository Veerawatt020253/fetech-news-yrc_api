FROM python:3.12-slim

WORKDIR /app

# ติดตั้ง dependency ก่อนแยกเลเยอร์ ให้ cache build เร็วขึ้นเวลาแก้แค่โค้ด
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
