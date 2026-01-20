# ==========================================
# 1. IMPORTACIONES Y CONFIGURACIÓN HABIPRO
# ==========================================
import os, json, random, calendar, io, csv, requests
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date, datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
# 1. CARGAR VARIABLES DE ENTORNO
load_dotenv()

# Fallback: Si no se cargó DB_URI, intentar cargar desde .env.txt (error común al crear el archivo)
if not os.getenv('DB_URI') and os.path.exists('.env.txt'):
    print("⚠️  AVISO: Cargando configuración desde .env.txt")
    load_dotenv('.env.txt')
    
    # FIX: Si .env.txt tiene solo la URL (sin DB_URI=), la leemos manualmente
    if not os.getenv('DB_URI'):
        try:
            with open('.env.txt', 'r') as f:
                content = f.read().strip()
                if content.startswith('postgresql://'):
                    os.environ['DB_URI'] = content
        except: pass

app = Flask(__name__)

# 2. CONFIGURACIÓN MANUAL FORZADA
# Intentamos obtener la URI desde el archivo .env
database_uri = os.getenv('DB_URI')

# Si la URI está vacía, usamos el valor directo para que no falle (SOLO PARA PRUEBAS)
if not database_uri:
    raise RuntimeError("⚠️ ERROR CRÍTICO: La variable de entorno DB_URI no está configurada.")

# --- FIX: CORRECCIÓN DE URI PARA PSYCOPG2 ---
if database_uri:
    database_uri = database_uri.strip().strip("'").strip('"')  # Eliminar espacios y comillas
    if database_uri.startswith("postgres://"):
        database_uri = database_uri.replace("postgres://", "postgresql://", 1)
    
    # FIX: Re-codificar query params para evitar errores con caracteres especiales (ej: options=project=...)
    try:
        if "://" in database_uri:
            from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
            u = urlparse(database_uri)
            if u.query:
                q_args = parse_qsl(u.query, keep_blank_values=True)
                
                # FIX for malformed .env file where GEMINI_API_KEY is concatenated to sslmode
                new_q_args = []
                for k, v in q_args:
                    if k == 'sslmode' and 'GEMINI_API_KEY' in v:
                        sslmode_val = v.split('GEMINI_API_KEY')[0]
                        new_q_args.append((k, sslmode_val))
                    else:
                        new_q_args.append((k, v))
                q_args = new_q_args
                # ---
                
                new_query = urlencode(q_args)
                database_uri = urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception as e:
        print(f"⚠️ Advertencia: No se pudo normalizar la URI de BD: {e}")

app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'habitex_secret_key_master_2026')
app.config['SESSION_COOKIE_NAME'] = 'habitex_session'

# 3. INICIALIZACIÓN DE LA BASE DE DATOS
# Ahora SQLAlchemy encontrará la URI cargada en app.config
db = SQLAlchemy(app, engine_options={
    "pool_pre_ping": True, 
    "pool_recycle": 280,
    "pool_size": 10,
    "max_overflow": 5,
    "pool_timeout": 30
})
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

# Registro de Blueprints
from .blueprints.auth import auth_bp
from .blueprints.admin import admin_bp
from .blueprints.conserje import conserje_bp
from .blueprints.residente import residente_bp
from .blueprints.superadmin import superadmin_bp
from .blueprints.main import main_bp
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(conserje_bp)
app.register_blueprint(residente_bp)
app.register_blueprint(superadmin_bp)
app.register_blueprint(main_bp)

# Importar modelos para registrar el user_loader de Flask-Login
from . import models

# --- FIX: Alias para compatibilidad con templates antiguos ---
@app.route('/login_redirect')
def login():
    return redirect(url_for('auth.login'))

@app.route('/logout_redirect')
def logout():
    return redirect(url_for('auth.logout'))

def create_app():
    """Función fábrica para retornar la instancia de la aplicación."""
    return app