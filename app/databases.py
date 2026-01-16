import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from flask import current_app

# Gestor de Contexto: Abre, entrega el cursor, y cierra automáticamente.
@contextmanager
def get_db_cursor(commit=False):
    conn = psycopg2.connect(
        current_app.config['SQLALCHEMY_DATABASE_URI'], 
        cursor_factory=RealDictCursor
    )
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()