# ==========================================
# 1. IMPORTACIONES Y CONFIGURACIÓN HABIPRO
# ==========================================
import os, json, random, calendar, io, csv, requests
import psycopg2 # <--- ESTA LÍNEA ES LA QUE FALTA O ESTÁ MAL UBICADA
from psycopg2.extras import RealDictCursor # <--- NECESARIA PARA RealDictCursor
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

app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    print("⚠️  SECRET_KEY no encontrada. Usando clave temporal para desarrollo.")
    app.config['SECRET_KEY'] = 'dev_key_temporal_12345'
app.config['SESSION_COOKIE_NAME'] = 'habipro_session'

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
from app.auth import auth_bp
app.register_blueprint(auth_bp)

def get_db_connection():
    return psycopg2.connect(app.config['SQLALCHEMY_DATABASE_URI'], cursor_factory=RealDictCursor)

def parse_json_field(field_data):
    if isinstance(field_data, dict): return field_data
    try: return json.loads(field_data or '{}')
    except: return {}

def formatear_rut(rut_raw):
    if not rut_raw: return ""
    limpio = str(rut_raw).replace(".", "").replace(" ", "").strip().upper()
    if "-" not in limpio and len(limpio) > 3: limpio = limpio[:-1] + "-" + limpio[-1]
    return limpio

CACHE_INDICADORES = {'data': None, 'fecha': None}
def obtener_indicadores():
    global CACHE_INDICADORES
    hoy = date.today()
    if CACHE_INDICADORES['data'] and CACHE_INDICADORES['fecha'] == hoy: return CACHE_INDICADORES['data']
    try:
        r = requests.get('https://mindicador.cl/api', timeout=5).json()
        datos = {'uf': r['uf']['valor'], 'utm': r['utm']['valor'], 'dolar': r['dolar']['valor']}
        CACHE_INDICADORES = {'data': datos, 'fecha': hoy}
        return datos
    except: return {'uf': 38200, 'utm': 66000, 'dolar': 975}

def get_safe_date_params():
    now = date.today()
    y = request.args.get('year', now.year, type=int)
    m = request.args.get('month', now.month, type=int)
    return y, m

def calcular_navegacion(m, y):
    return {'prev_m': 12 if m == 1 else m - 1, 'prev_y': y - 1 if m == 1 else y,
            'next_m': 1 if m == 12 else m + 1, 'next_y': y + 1 if m == 12 else y}
    
@app.route('/')
def landing():
    # Siempre mostrar la landing page en la raíz
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Redirigir a la lógica de paneles por rol definida en auth.home
    return redirect(url_for('auth.home'))

def create_app():
    """Función fábrica para retornar la instancia de la aplicación."""
    return app