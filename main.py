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
def parse_file_to_rows(content: bytes, filename: str) -> list:
    ext = filename.rsplit(".", 1)[-1].lower()

    if ext in ("xlsx", "xls"):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip().lower() if h else "" for h in rows[0]]
        result = []
        for row in rows[1:]:
            if all(cell is None for cell in row):
                continue
            row_dict = {headers[i]: (str(row[i]).strip() if row[i] is not None else "") for i in range(len(headers))}
            result.append(row_dict)
        return result
    else:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [
            {k.strip().lower(): v.strip() for k, v in row.items()}
            for row in reader
        ]
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

# ── Health Check ──────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "SubstituteAI is running!"}

# ── Upload CSV ────────────────────────────────────────────────────────
@app.post("/upload")
async def upload_timetable(
    school_name: str = Form(...),
    file: UploadFile = File(...)
):
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    conn = get_connection()
    cur = conn.cursor()

    # Create or get school
    cur.execute("INSERT INTO schools (name) VALUES (%s) RETURNING id", (school_name,))
    school_id = cur.fetchone()[0]

    rows_inserted = 0
    teachers_seen = {}

    for row in reader:
        cls     = row.get("class", "").strip()
        day     = row.get("day", "").strip()
        period  = int(row.get("period", 0))
        subject = row.get("subject", "").strip()
        teacher = row.get("teacher", "").strip()

        if not all([cls, day, period, subject, teacher]):
            continue

        cur.execute("""
            INSERT INTO timetable (school_id, class, day, period, subject, teacher)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (school_id, cls, day, period, subject, teacher))

        # Track teacher workload and subject
        if teacher not in teachers_seen:
            teachers_seen[teacher] = {"subject": subject, "workload": 0}
        teachers_seen[teacher]["workload"] += 1
        rows_inserted += 1

    # Insert teachers
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

# ── Get Teachers ──────────────────────────────────────────────────────
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

# ── Get Classes ───────────────────────────────────────────────────────
@app.get("/classes/{school_id}")
def get_classes(school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT class FROM timetable WHERE school_id = %s ORDER BY class", (school_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

# ── Get Subjects ──────────────────────────────────────────────────────
@app.get("/subjects/{school_id}")
def get_subjects(school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT subject FROM timetable WHERE school_id = %s ORDER BY subject", (school_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]

# ── AI Substitute ─────────────────────────────────────────────────────
class AbsentTeacher(BaseModel):
    name: str
    leave_type: str  # full, first, second

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

# ── Logs ──────────────────────────────────────────────────────────────
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
