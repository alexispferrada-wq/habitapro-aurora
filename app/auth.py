from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_login import login_user, logout_user, current_user
from werkzeug.security import check_password_hash
from app.database import get_db_cursor
from app.models import Usuario
import re

auth_bp = Blueprint('auth', __name__)

def formatear_rut(rut_raw):
    if not rut_raw: return ""
    limpio = str(rut_raw).replace(".", "").replace(" ", "").strip().upper()
    if "-" not in limpio and len(limpio) > 3: limpio = limpio[:-1] + "-" + limpio[-1]
    return limpio

@auth_bp.route('/home')
def home():
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    
    rol = session.get('rol')
    if rol == 'superadmin': return redirect(url_for('superadmin.panel_superadmin'))
    if rol == 'admin': return redirect(url_for('admin.panel_admin'))
    if rol == 'conserje': return redirect(url_for('conserje.panel_conserje'))
    if rol == 'residente': return redirect(url_for('residente.panel_residente'))
    
    return render_template('index.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if current_user.is_authenticated:
            return redirect(url_for('auth.home'))
        return render_template('login.html')

    usuario_input = request.form.get('email') or request.form.get('username') or request.form.get('rut')
    password = request.form.get('password')
    pass_input = str(password).strip()
    
    # Detectar si es RUT o Email/Usuario
    if re.search(r'\d', usuario_input):
        rut_busqueda = formatear_rut(usuario_input)
    else:
        rut_busqueda = usuario_input

    with get_db_cursor() as cur:
        cur.execute("""
            SELECT * FROM usuarios 
            WHERE LOWER(email) = LOWER(%s) 
               OR rut = %s 
               OR LOWER(nombre) LIKE LOWER(%s)
        """, (usuario_input, rut_busqueda, f"%{usuario_input}%"))
        
        user_data = cur.fetchone()
        
        if user_data:
            pass_db = str(user_data.get('password', '')).strip()
            # Soporte para hash y texto plano (legacy)
            is_valid = check_password_hash(pass_db, pass_input) if pass_db.startswith(('scrypt:', 'pbkdf2:')) else (pass_db == pass_input)
            
            if is_valid:
                user_obj = Usuario()
                user_obj.rut = user_data['rut']
                user_obj.nombre = user_data['nombre']
                user_obj.rol = user_data['rol']
                user_obj.edificio_id = user_data.get('edificio_id')
                
                login_user(user_obj)
                
                session['user_id'] = user_data['rut']
                session['nombre'] = user_data['nombre']
                session['rol'] = user_data['rol']
                session['edificio_id'] = user_data.get('edificio_id')

                # Lógica especial para residentes (buscar unidad)
                if user_data['rol'] == 'residente':
                    cur.execute("""
                        SELECT id, numero FROM unidades 
                        WHERE edificio_id = %s 
                        AND (owner_json::text LIKE %s OR tenant_json::text LIKE %s)
                    """, (user_data['edificio_id'], f"%{user_data['rut']}%", f"%{user_data['rut']}%"))
                    
                    unidad = cur.fetchone()
                    
                    if unidad:
                        session['unidad_id_residente'] = unidad['id']
                        session['numero_unidad'] = unidad['numero']
                        return redirect(url_for('residente.panel_residente'))
                    else:
                        flash("Usuario válido, pero sin departamento asignado.", "warning")
                        return redirect(url_for('auth.login'))

                return redirect(url_for('auth.home'))
            else:
                flash("Contraseña incorrecta.", "error")
        else:
            flash("Usuario no encontrado.", "error")

    return redirect(url_for('auth.login'))

@auth_bp.route('/logout')
def logout():
    session.clear()
    logout_user()
    return redirect(url_for('auth.login'))