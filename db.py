import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    database_url = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(database_url, sslmode='require')
    return conn