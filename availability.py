from db import get_connection

def get_busy_teachers(day: str, period: int, school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT teacher
        FROM timetable
        WHERE day = %s AND period = %s AND school_id = %s
    """, (day, period, school_id))
    busy = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return busy

def get_free_teachers(day: str, period: int, school_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT name, subject, performance, workload
        FROM teachers
        WHERE school_id = %s
    """, (school_id,))
    all_teachers = cur.fetchall()
    busy = get_busy_teachers(day, period, school_id)
    free = [
        {"name": t[0], "subject": t[1], "performance": t[2], "workload": t[3]}
        for t in all_teachers if t[0] not in busy
    ]
    cur.close()
    conn.close()
    return free
