from db import get_connection
import datetime

def setup_log_table():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS substitution_log (
            id SERIAL PRIMARY KEY,
            absent_teacher TEXT,
            substitute_name TEXT,
            class_name TEXT,
            subject TEXT,
            day TEXT,
            period INT,
            leave_type TEXT DEFAULT 'full',
            substitution_date DATE DEFAULT CURRENT_DATE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def log_substitution(absent, substitute, class_name, subject, day, period, leave_type='full'):
    try:
        setup_log_table()
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO substitution_log
            (absent_teacher, substitute_name, class_name, subject, day, period, leave_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (absent, substitute, class_name, subject, day, period, leave_type))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Logging error: {e}")
