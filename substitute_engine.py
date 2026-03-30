from availability import get_free_teachers
from db import get_connection
from logger import log_substitution
import datetime

def get_last_sub_days(teacher_name: str, school_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT substitution_date FROM substitution_log
            WHERE substitute_name = %s
            ORDER BY substitution_date DESC LIMIT 1
        """, (teacher_name,))
        row = cur.fetchone()
        if row:
            delta = (datetime.date.today() - row[0]).days
            return delta
        return 30
    except:
        return 30
    finally:
        cur.close()
        conn.close()

def score_teacher(teacher: dict, required_subject: str, school_id: int) -> float:
    workload = min(teacher["workload"] / 20, 5)
    performance = teacher["performance"]
    last_sub_days = get_last_sub_days(teacher["name"], school_id)

    if teacher["subject"].lower() == required_subject.lower():
        subject_match_bonus = 5.0
    elif teacher["subject"].lower() in required_subject.lower():
        subject_match_bonus = 2.5
    else:
        subject_match_bonus = 0.0

    score = (5 - workload) * 2 + performance + (last_sub_days * 0.1) + subject_match_bonus
    return round(score, 2)

def is_teacher_absent_for_period(absent_teachers: list, teacher_name: str, period: int) -> bool:
    for a in absent_teachers:
        if a["name"] == teacher_name:
            leave_type = a.get("leave_type", "full")
            if leave_type == "full":
                return True
            elif leave_type == "first" and 1 <= period <= 4:
                return True
            elif leave_type == "second" and 5 <= period <= 8:
                return True
    return False

def get_top_substitutes(
    absent_teachers: list,
    class_name: str,
    subject: str,
    day: str,
    period: int,
    school_id: int,
    top_n: int = 3
):
    free_teachers = get_free_teachers(day, period, school_id)

    # Remove teachers who are absent for this period
    free_teachers = [
        t for t in free_teachers
        if not is_teacher_absent_for_period(absent_teachers, t["name"], period)
    ]

    if not free_teachers:
        return {"error": "No free teachers available for this slot"}

    scored = []
    for teacher in free_teachers:
        s = score_teacher(teacher, subject, school_id)
        scored.append({**teacher, "score": s})

    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_n]

    # Log substitution
    absent_names = [a["name"] for a in absent_teachers]
    for absent_name in absent_names:
        leave_type = next((a.get("leave_type","full") for a in absent_teachers if a["name"] == absent_name), "full")
        log_substitution(absent_name, top[0]["name"] if top else None, class_name, subject, day, period, leave_type)

    return {
        "absent_teachers": absent_teachers,
        "class": class_name,
        "subject": subject,
        "day": day,
        "period": period,
        "top_substitutes": top
    }
