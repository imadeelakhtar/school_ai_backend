import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    database_url = os.getenv("DATABASE_URL")
    # Remove "DATABASE_URL=" prefix if accidentally included
    if database_url and database_url.startswith("DATABASE_URL="):
        database_url = database_url.replace("DATABASE_URL=", "")
    conn = psycopg2.connect(database_url)
    return conn