from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from substitute_engine import get_top_substitutes
from availability import get_free_teachers
from db import get_connection
from logger import setup_log_table
from pydantic import BaseModel
from typing import List
import csv
import io
import openpyxl
import os
import json
import google.genai as genai

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

app = FastAPI(title="SubstituteAI Backend", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    setup_log_table()
    setup_school_tables()

def setup_school_tables():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS schools (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS timetable (
            id SERIAL PRIMARY KEY,
            school_id INT REFERENCES schools(id) ON DELETE CASCADE,
            class TEXT,
            day TEXT,
            period INT,
            subject TEXT,
            teacher TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id SERIAL PRIMARY KEY,
            school_id INT REFERENCES schools(id) ON DELETE CASCADE,
            name TEXT,
            subject TEXT,
            performance FLOAT DEFAULT 4.0,
            workload INT DEFAULT 0,
            UNIQUE(school_id, name)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def extract_raw_rows(content: bytes, filename: str) -> list:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext in ("xlsx", "xls"):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        return [[str(cell) if cell is not None else "" for cell in row] for row in rows]
    else:
        text = content.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        return [row for row in reader]

def parse_with_gemini(raw_rows: list) -> list:
    sample = raw_rows[:50]
    sample_text = "\n".join([", ".join(row) for row in sample])

    prompt = f"""
You are a data parser for school timetables.
Below is raw data from an Excel/CSV file. It may have any format, column names, or structure.

Your job is to extract timetable rows and return ONLY a JSON array.
Each object must have exactly these keys: "class", "day", "period", "subject", "teacher"

Rules:
- "period" must be a number (1-8)
- "day" must be one of: Monday, Tuesday, Wednesday, Thursday, Friday, Saturday
- Skip rows that don't have all 5 fields
- Return ONLY the JSON array, no explanation, no markdown backticks

Raw data:
{sample_text}
"""

    response = gemini_client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    text = response.text.strip()

    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    return json.loads(text)

@app.get("/")
def root():
    return {"status": "SubstituteAI is running!"}

@app.post("/upload")
async def upload_timetable(
    school_name: str = Form(...),
    file: UploadFile = File(...)
):
    content = await file.read()

    try:
        raw_rows = extract_raw_rows(content, file.filename)
    except Exception as e:
        return {"error": f"File read nahi ho saka: {str(e)}"}

    try:
        rows = parse_with_gemini(raw_rows)
    except Exception as e:
        return {"error": f"AI parse failed: {str(e)}"}

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO schools (name) VALUES (%s) RETURNING id", (school_name,))
    school_id = cur.fetchone()[0]

    rows_inserted = 0
    teachers_seen = {}

    for row in rows:
        cls = str(row.get("class", "")).strip()
        day = str(row.get("day", "")).strip()
        subject = str(row.get("subject", "")).strip()
        teacher = str(row.get("teacher", "")).strip()

        try:
            period = int(float(str(row.get("period", 0))))
        except (ValueError, TypeError):
            continue

        if not all([cls, day, period, subject, teacher]):
            continue

        cur.execute("""
            INSERT INTO timetable (school_id, class, day, period, subject, teacher)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (school_id, cls, day, period, subject, teacher))

        if teacher not in teachers_seen:
            teachers_seen[teacher] = {"subject": subject, "workload": 0}
        teachers_seen[teacher]["workload"] += 1
        rows_inserted += 1

    for name, info in teachers_seen.items():
        cur.execute("""
            INSERT INTO teachers (school_id, name, subject, workload)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (school_id, name) DO UPDATE
            SET workload = EXCLUDED.workload
        """, (school_id, name, info["subject"], info["workload"]))

    conn.commit()
    cur.close()
    conn.close()

    return {
        "school_id": school_id,
        "school_name": school_name,
        "rows_imported": rows_inserted,
        "teachers_found": len(teachers_seen)
    }

@app.get("/teachers/{school_id}")
def get_teachers(school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, subject, performance, workload
        FROM teachers WHERE school_id = %s ORDER BY name
    """, (school_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"id": r[0], "name": r[1], "subject": r[2], "performance": r[3], "workload": r[4]}
        for r in rows
    ]

@app.get("/classes/{school_id}")
def get_classes(school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT class FROM timetable WHERE school_id = %s ORDER BY class", (school_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

@app.get("/subjects/{school_id}")
def get_subjects(school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT subject FROM timetable WHERE school_id = %s ORDER BY subject", (school_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

class AbsentTeacher(BaseModel):
    name: str
    leave_type: str

class SubstituteRequest(BaseModel):
    school_id: int
    absent_teachers: List[AbsentTeacher]
    class_name: str
    subject: str
    day: str
    period: int

@app.post("/auto_substitute")
def auto_substitute(req: SubstituteRequest):
    absent_list = [{"name": a.name, "leave_type": a.leave_type} for a in req.absent_teachers]
    result = get_top_substitutes(
        absent_teachers=absent_list,
        class_name=req.class_name,
        subject=req.subject,
        day=req.day,
        period=req.period,
        school_id=req.school_id
    )
    return result

@app.get("/logs")
def get_logs():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT absent_teacher, substitute_name, class_name, subject,
               day, period, leave_type, substitution_date
        FROM substitution_log ORDER BY created_at DESC LIMIT 50
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "absent_teacher": r[0], "substitute": r[1], "class": r[2],
            "subject": r[3], "day": r[4], "period": r[5],
            "leave_type": r[6], "date": str(r[7])
        }
        for r in rows
    ]
