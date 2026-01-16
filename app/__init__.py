from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from dotenv import load_dotenv
import os
from datetime import datetime

# Inicializamos extensiones
db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    
    # 1. Cargar configuración desde .env
    load_dotenv()
    
    # Fallback: Si no se cargó DB_URI, intentar cargar desde .env.txt
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

    # Configuración de seguridad y base de datos
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'clave_dev_segura_123')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DB_URI')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SESSION_COOKIE_NAME'] = 'habipro_session'

    if not app.config['SQLALCHEMY_DATABASE_URI']:
        raise RuntimeError("❌ Error Crítico: DB_URI no encontrada. Verifica tu archivo .env o .env.txt")

    # 2. Iniciar extensiones con la app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    # Context processor para inyectar variables globales a los templates
    @app.context_processor
    def inject_global_vars():
        return dict(current_year=datetime.utcnow().year)

    # 3. Importar y Registrar Blueprints
    # Nota: Importamos aquí para evitar referencias circulares
    from app.blueprints.auth import auth_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.residente import residente_bp
    from app.blueprints.conserje import conserje_bp
    from app.blueprints.superadmin import superadmin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(residente_bp)
    app.register_blueprint(conserje_bp)
    app.register_blueprint(superadmin_bp)

    return app