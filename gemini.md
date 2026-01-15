# Respaldo Proyecto HabitaPro Aurora

## app.py
```python
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
    "pool_recycle": 300
})
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ==========================================
# 2. MODELOS Y UTILIDADES CRÍTICAS
# ==========================================
class Usuario(UserMixin, db.Model): 
    __tablename__ = 'usuarios'
    rut = db.Column(db.String(20), primary_key=True)
    nombre = db.Column(db.String(100)); email = db.Column(db.String(100))
    rol = db.Column(db.String(50)); edificio_id = db.Column(db.Integer)
    activo = db.Column(db.Boolean, default=True)
    def get_id(self): return self.rut

@login_manager.user_loader
def load_user(user_rut): 
    # session.get es el estándar actual para SQLAlchemy 2.0
    return db.session.get(Usuario, user_rut)

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
def home():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    
    rol = session.get('rol')
    if rol == 'superadmin': return redirect(url_for('panel_superadmin'))
    if rol == 'admin': return redirect(url_for('panel_admin'))
    if rol == 'conserje': return redirect(url_for('panel_conserje'))
    if rol == 'residente': return redirect(url_for('panel_residente'))
    
    return render_template('index.html')

# --- NUEVAS RUTAS PARA LA SELECCIÓN MÚLTIPLE ---

@app.route('/seleccionar_unidad')
def seleccionar_unidad():
    opciones = session.get('opciones_login')
    if not opciones:
        return redirect(url_for('home'))
    return render_template('select_unit.html', opciones=opciones)

@app.route('/set_unidad/<int:uid>')
def set_unidad(uid):
    opciones = session.get('opciones_login')
    if not opciones: return redirect(url_for('home'))
    
    # Buscar la unidad seleccionada en las opciones guardadas (por seguridad)
    seleccionada = next((item for item in opciones if item["id"] == uid), None)
    
    if seleccionada:
        session['rol'] = 'residente'
        session['unidad_id_residente'] = seleccionada['id']
        session['edificio_id'] = seleccionada['edificio_id']
        session['nombre'] = seleccionada['nombre_usuario']
        session['numero_unidad'] = seleccionada['numero']
        session.pop('opciones_login', None) # Limpiar temp
        return redirect(url_for('panel_residente'))
    
    return redirect(url_for('home'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

# --- UTILIDADES PARKING ---
# --- LÓGICA DE PARKING REAL (CONECTADA A BD) ---
# --- LÓGICA DE PARKING REAL (CORREGIDA) ---
def obtener_estado_parking_real(edificio_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Traemos los espacios configurados
    cur.execute("SELECT * FROM estacionamientos_visita WHERE edificio_id = %s ORDER BY id ASC", (edificio_id,))
    slots_db = cur.fetchall()
    
    # Si no existen, creamos 5 por defecto
    if not slots_db:
        for i in range(1, 6):
            cur.execute("INSERT INTO estacionamientos_visita (edificio_id, nombre, estado) VALUES (%s, %s, 'libre')", (edificio_id, f"V-{i}"))
        conn.commit()
        cur.execute("SELECT * FROM estacionamientos_visita WHERE edificio_id = %s ORDER BY id ASC", (edificio_id,))
        slots_db = cur.fetchall()

    # 2. Buscamos visitas activas usando la columna CORRECTA (parking_id)
    cur.execute("""
        SELECT v.parking_id, v.patente, v.ingreso, v.nombre_visita, u.numero as unidad
        FROM visitas v 
        LEFT JOIN unidades u ON v.unidad_id = u.id 
        WHERE v.edificio_id = %s AND v.salida IS NULL AND v.parking_id IS NOT NULL
    """, (edificio_id,))
    
    # Convertimos a diccionario usando el ID como clave (string)
    visitas_activas = {str(v['parking_id']): v for v in cur.fetchall()}
    
    mapa = []
    for slot in slots_db:
        s_id = str(slot['id'])
        
        # Estado base desde la tabla de estacionamientos (Forzamos minúsculas para CSS)
        estado_bd = str(slot['estado']).lower()
        estado_final = 'libre'
        
        patente = None
        tiempo = ''
        unidad = ''

        # PRIORIDAD 1: MANTENCIÓN
        if estado_bd == 'mantencion':
            estado_final = 'mantencion'
        
        # PRIORIDAD 2: OCUPADO POR VISITA ACTIVA (Esto pinta el mapa)
        elif s_id in visitas_activas:
            v = visitas_activas[s_id]
            estado_final = 'ocupado'
            patente = v['patente']
            unidad = v['unidad']
            # Calcular tiempo
            minutos = int((datetime.now() - v['ingreso']).total_seconds() / 60)
            h, m = divmod(minutos, 60)
            tiempo = f"{h}h {m}m"
            
        # PRIORIDAD 3: OCUPADO MANUALMENTE (Sin visita, pero marcado ocupado)
        elif estado_bd == 'ocupado':
             estado_final = 'ocupado'
             patente = slot.get('patente', 'OCUPADO')

        mapa.append({
            'id': slot['id'], 
            'nombre': slot['nombre'], 
            'estado': estado_final, # 'libre', 'ocupado', 'mantencion'
            'patente': patente, 
            'tiempo': tiempo, 
            'unidad_numero': unidad
        })
        
    cur.close()
    conn.close()
    return mapa
# --- RUTAS DE GESTIÓN (AGREGAR Y ELIMINAR) ---

@app.route('/admin/parking/agregar', methods=['POST'])
def admin_parking_agregar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    nombre = request.form.get('nombre', '').upper()
    eid = session.get('edificio_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO estacionamientos_visita (edificio_id, nombre) VALUES (%s, %s)", (eid, nombre))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/parking/eliminar', methods=['POST'])
def admin_parking_eliminar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    pid = request.form.get('id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM estacionamientos_visita WHERE id = %s", (pid,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/parking/maintenance', methods=['POST'])
def parking_maintenance():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    sid = request.form.get('slot_id') 
    accion = request.form.get('accion') 
    
    # Actualizamos el estado en la BD real
    nuevo_estado = 'MANTENCION' if accion == 'activar' else 'LIBRE'
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE estacionamientos_visita SET estado = %s WHERE id = %s", (nuevo_estado, sid))
    conn.commit()
    cur.close()
    conn.close()
    return redirect(url_for('panel_admin'))



# ==========================================
# RUTAS CONSERJE
# ==========================================
@app.route('/panel-conserje')
def panel_conserje():
    if session.get('rol') != 'conserje': return redirect(url_for('home'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); edificio = cur.fetchone()
    
    # 1. PAQUETES PENDIENTES
    cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,))
    encomiendas = cur.fetchall()
    
    # 2. RESERVAS (SOLO HOY Y FUTURO)
    # La clave es: r.fecha_uso >= CURRENT_DATE
    cur.execute("""
        SELECT r.fecha_uso, r.hora_inicio, e.nombre as nombre_espacio, u.numero as numero_unidad 
        FROM reservas r 
        JOIN espacios e ON r.espacio_id = e.id 
        JOIN unidades u ON r.unidad_id = u.id 
        WHERE e.edificio_id = %s 
        AND r.fecha_uso >= CURRENT_DATE 
        AND r.estado = 'CONFIRMADA'
        ORDER BY r.fecha_uso ASC, r.hora_inicio ASC LIMIT 10
    """, (eid,))
    reservas_futuras = cur.fetchall()
    
    # 3. UNIDADES (ORDENADAS Y COMPLETAS PARA EDITAR)
    cur.execute("SELECT id, numero, owner_json, tenant_json FROM unidades WHERE edificio_id = %s ORDER BY LENGTH(numero), numero ASC", (eid,))
    raw = cur.fetchall()
    u_proc = []
    for u in raw:
        o = parse_json_field(u.get('owner_json')); t = parse_json_field(u.get('tenant_json'))
        nombre = t.get('nombre') or o.get('nombre','S/D')
        fono = t.get('fono') or o.get('fono','')
        email = t.get('email') or o.get('email','')
        rut = t.get('rut') or o.get('rut','')
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'residente': nombre, 'fono': fono, 'email': email, 'rut': rut, 'owner': o, 'tenant': t})
    
    cur.close(); conn.close()
    return render_template('dash_conserje.html', edificio=dict(edificio), parking=obtener_estado_parking_real(eid), encomiendas=encomiendas, unidades=u_proc, reservas_futuras=reservas_futuras)

@app.route('/conserje/parking/toggle', methods=['POST'])
def conserje_parking_toggle():
    sid = request.form.get('slot_id')
    acc = request.form.get('accion')
    pat = request.form.get('patente', 'VISITA').upper()
    uid = request.form.get('unidad_id') # <--- CAPTURAR EL ID DE UNIDAD
    eid = session.get('edificio_id')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if acc == 'ocupar':
            # 1. Registrar la visita INCLUYENDO la unidad_id (Obligatorio para que no aparezca libre)
            cur.execute("""
                INSERT INTO visitas (edificio_id, unidad_id, patente, estacionamiento_id, parking_id, ingreso) 
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (eid, uid, pat, sid, sid))
            
            # 2. Marcar el parking como OCUPADO
            cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s", (pat, sid))

        else: # Acción: LIBERAR
            cur.execute("""
                UPDATE visitas 
                SET salida = NOW() 
                WHERE edificio_id = %s 
                AND (parking_id = %s OR estacionamiento_id = %s) 
                AND salida IS NULL
            """, (eid, int(sid), sid))

            cur.execute("UPDATE estacionamientos_visita SET estado = 'libre', patente = NULL WHERE id = %s", (sid,))

        conn.commit()
        return jsonify({'status': 'success'})

    except Exception as e:
        conn.rollback()
        print(f"Error parking toggle: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

    finally:
        cur.close()
        conn.close()


@app.route('/conserje/encomiendas/guardar', methods=['POST'])
def conserje_guardar_encomienda():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("INSERT INTO encomiendas (edificio_id, unidad_id, remitente, recepcion) VALUES (%s, %s, %s, NOW())", (session.get('edificio_id'), request.form.get('unidad_id'), request.form.get('remitente'))); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_conserje'))

@app.route('/conserje/encomiendas/entregar', methods=['POST'])
def conserje_entregar_encomienda():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("UPDATE encomiendas SET entrega = NOW() WHERE id = %s", (request.form.get('encomienda_id'),)); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_conserje'))

@app.route('/conserje/incidencias/guardar', methods=['POST'])
def conserje_guardar_incidencia():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor) VALUES (%s, %s, %s, NOW(), %s)", (session.get('edificio_id'), request.form.get('titulo'), request.form.get('descripcion'), session.get('nombre'))); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_conserje'))

# ==========================================
# RUTAS RESIDENTE (APP MÓVIL)
# ==========================================
# EN: app.py

# EN: app.py

@app.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "HabitaPro",
        "short_name": "HabitaPro",
        "start_url": "/panel-residente",
        "display": "standalone",
        "background_color": "#1a1d24",
        "theme_color": "#0dcaf0",
        "icons": [
            {
                # URL OFICIAL del ícono exacto de tu login
                "src": "https://icons.getbootstrap.com/assets/icons/buildings-fill.svg",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    })


# EN: app.py

# EN: app.py


# EN: app.py

# IMPORTANTE: Fíjate que dice methods=['GET', 'POST']
# EN: app.py



@app.route('/residente/perfil/editar', methods=['POST'])
def residente_editar_perfil():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("UPDATE usuarios SET email=%s, telefono=%s WHERE rut=%s", (request.form.get('email'), request.form.get('telefono'), session.get('user_id'))); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_residente'))
# EN: app.py

@app.route('/panel-residente', methods=['GET', 'POST'])
def panel_residente():
    import json
    from datetime import date, datetime
    
 # 1. SEGURIDAD: Si faltan datos, CERRAMOS LA SESIÓN para romper el bucle
    if session.get('rol') != 'residente' or 'unidad_id_residente' not in session:
        flash("Error de sesión: No se pudo identificar tu departamento.", "error")
        return redirect(url_for('logout')) # <--- ESTO SOLUCIONA EL BUCLE
    
    uid = session.get('unidad_id_residente')
    eid = session.get('edificio_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # A. Datos Básicos (Unidad y Edificio)
    cur.execute("SELECT * FROM unidades WHERE id = %s", (uid,))
    u = cur.fetchone()
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,))
    edificio = cur.fetchone()
    
    # B. Perfil Usuario
    t = parse_json_field(u.get('tenant_json'))
    o = parse_json_field(u.get('owner_json'))
    user_data = {'email': t.get('email') or o.get('email') or '', 'telefono': t.get('fono') or o.get('fono') or ''}

    # C. NOTIFICACIONES ACTIVAS (Filtros Aplicados)
    
    # 1. Espacios Comunes (Para reservar)
    cur.execute("SELECT * FROM espacios WHERE edificio_id = %s AND activo = TRUE", (eid,))
    espacios = cur.fetchall()

    # 2. Encomiendas: Solo las que NO tienen fecha de entrega (Pendientes)
    cur.execute("SELECT * FROM encomiendas WHERE unidad_id = %s AND entrega IS NULL ORDER BY recepcion DESC", (uid,))
    encomiendas = cur.fetchall()
    
    # 3. Reservas Confirmadas Futuras
    cur.execute("SELECT r.*, e.nombre as nombre_espacio FROM reservas r JOIN espacios e ON r.espacio_id = e.id WHERE r.unidad_id = %s AND r.fecha_uso >= CURRENT_DATE AND r.estado = 'CONFIRMADA' ORDER BY r.fecha_uso ASC", (uid,))
    mis_reservas = cur.fetchall()

    # 4. Visitas Activas: Solo las que están DENTRO (salida IS NULL)
    # NOTA: Usamos 'salida' para que coincida con el botón del conserje
    cur.execute("""
        SELECT v.*, p.nombre as parking_nombre,
        EXTRACT(EPOCH FROM (NOW() - v.ingreso))/60 as minutos_transcurridos
        FROM visitas v
        LEFT JOIN estacionamientos_visita p ON v.parking_id = p.id
        WHERE v.unidad_id = %s AND v.salida IS NULL
        ORDER BY v.ingreso DESC
    """, (uid,))
    visitas_activas = cur.fetchall()

    # 5. Multas Impagas: Solo las que tienen pagada = FALSE
    try:
        # AQUÍ ESTÁ EL FILTRO QUE PEDISTE:
        cur.execute("SELECT * FROM multas WHERE unidad_id = %s AND pagada = FALSE ORDER BY fecha DESC", (uid,))
        multas = cur.fetchall()
    except Exception as e:
        # Si la columna 'pagada' aún no existe en tu BD, devolvemos lista vacía para no romper el sitio
        conn.rollback()
        multas = []

    cur.close()
    conn.close()
    
    return render_template('dash_residente.html', 
                         u=u, user=user_data, edificio=edificio, 
                         espacios=espacios, encomiendas=encomiendas, 
                         mis_reservas=mis_reservas, visitas_activas=visitas_activas, 
                         multas=multas, hoy=date.today())


@app.route('/residente/invitar/generar', methods=['POST'])
def generar_link_invitacion():
    import secrets
    
    # Verificación de sesión
    if session.get('rol') != 'residente': 
        return jsonify({'status': 'error', 'message': 'No autorizado'})
    
    eid = session.get('edificio_id')
    uid = session.get('unidad_id_residente')
    
    # Datos del formulario del modal
    tipo = request.form.get('tipo') # PEATON o VEHICULO
    pre_nombre = request.form.get('nombre') # Opcional
    
    # Generar Token único para el link
    t = secrets.token_urlsafe(16)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Guardar la pre-invitación en la base de datos
    cur.execute("""
        INSERT INTO invitaciones (token, edificio_id, unidad_id, tipo, pre_nombre, estado)
        VALUES (%s, %s, %s, %s, %s, 'PENDIENTE')
    """, (t, eid, uid, tipo, pre_nombre))
    
    conn.commit()
    cur.close()
    conn.close()
    
    # --- AQUÍ ESTABA EL ERROR ---
    # Cambiamos 'vista_invitado_form' por 'public_invitacion' que es el nombre real de la función
    link_final = url_for('public_invitacion', token=t, _external=True)
    
    return jsonify({'status': 'success', 'link': link_final})

    
    # Actualizar estado a LISTO
    cur.execute("""
        UPDATE invitaciones 
        SET nombre_visita = %s, rut_visita = %s, patente = %s, estado = 'LISTO' 
        WHERE token = %s
    """, (nombre, rut, patente, token))
    
    conn.commit()
    cur.close()
    conn.close()
    
    # Renderizar el HTML de éxito que acabamos de crear
    return render_template('public_qr_exito.html', 
                           token=token, 
                           nombre=nombre, 
                           rut=rut)
# ==========================================
# PANEL ADMIN
# ==========================================
# ==========================================
# FUNCIONES DE AYUDA (FECHAS Y NAVEGACIÓN)
# ==========================================
def get_safe_date_params():
    try:
        y_str = request.args.get('year', '')
        m_str = request.args.get('month', '')
        now = date.today()
        y = int(y_str) if y_str and y_str.isdigit() else now.year
        m = int(m_str) if m_str and m_str.isdigit() else now.month
        
        # Validaciones de seguridad
        if m < 1: m = 1
        if m > 12: m = 12
        if y < 2000: y = 2000
        if y > 2100: y = 2100
        
        return y, m
    except:
        now = date.today()
        return now.year, now.month

def calcular_navegacion(m, y):
    return {
        'prev_m': 12 if m == 1 else m - 1,
        'prev_y': y - 1 if m == 1 else y,
        'next_m': 1 if m == 12 else m + 1,
        'next_y': y + 1 if m == 12 else y
    }
    
@app.route('/panel-admin')
def panel_admin():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    
    eid = session.get('edificio_id')
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Datos del Edificio y Unidades
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,))
    ed = cur.fetchone()
    
    cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (eid,))
    units = cur.fetchall()
    
    deuda_aexon = ed.get('deuda_omnisoft', 0)
    
    # 2. Fechas y Navegación
    y, m = get_safe_date_params()
    mes_nombre = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"][m]
    nav = calcular_navegacion(m, y)
    
    # 3. Finanzas del Mes (Ingresos vs Egresos)
    cur.execute("SELECT SUM(monto) as t FROM gastos WHERE edificio_id=%s AND mes=%s AND anio=%s", (eid, m, y))
    g = cur.fetchone()
    tg = g['t'] if g and g['t'] else 0
    
    cur.execute("""SELECT SUM(monto) as t FROM historial_pagos WHERE edificio_id=%s AND ((mes_periodo=%s AND anio_periodo=%s) OR (mes_periodo IS NULL AND EXTRACT(MONTH FROM fecha)=%s AND EXTRACT(YEAR FROM fecha)=%s))""", (eid, m, y, m, y))
    i = cur.fetchone()
    ti = i['t'] if i and i['t'] else 0
    
    fin = {'ingresos': ti, 'egresos': tg, 'saldo': ti - tg}
    
    # 4. Encomiendas Pendientes
    cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,))
    enc = cur.fetchall()
    
    # 5. Activos y Calendario de Mantenciones
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s", (eid,))
    activos = cur.fetchall()
    eventos = {}
    f_ini = date(y, m, 1)
    f_fin = date(y, m, calendar.monthrange(y, m)[1])
    
    for a in activos:
        if a['ultimo_servicio']:
            f = a['ultimo_servicio']
            while f <= f_fin:
                if f >= f_ini: eventos.setdefault(f.day, []).append({'nombre': a['nombre'], 'costo': a['costo_estimado']})
                f += timedelta(days=a['periodicidad_dias'])
    
    # 6. Espacios Comunes y Reservas
    cur.execute("SELECT * FROM espacios WHERE edificio_id = %s AND activo = TRUE", (eid,))
    espacios = cur.fetchall()
    
    cur.execute("""
        SELECT r.*, e.nombre as nombre_espacio, u.numero as numero_unidad 
        FROM reservas r 
        JOIN espacios e ON r.espacio_id = e.id 
        JOIN unidades u ON r.unidad_id = u.id 
        WHERE e.edificio_id = %s 
        ORDER BY r.fecha_uso DESC LIMIT 20
    """, (eid,))
    reservas = cur.fetchall()
    
    cur.close()
    conn.close()
    
    # 7. Procesar Unidades (JSON)
    u_proc = []
    for u in units:
        o = parse_json_field(u.get('owner_json'))
        t = parse_json_field(u.get('tenant_json'))
        u_proc.append({
            'id': u['id'], 'numero': u['numero'], 'propietario': o.get('nombre'), 
            'owner': o, 'tenant': t, 'deuda_actual': u.get('deuda_monto', 0), 
            'metraje': u['metraje'], 'piso': u['piso'], 'prorrateo': u['prorrateo']
        })
        
    # --- 8. NUEVA LÓGICA: ALERTA DE DEUDA AEXON ---
    dias_restantes = 0
    alerta_deuda = None
    
    # Si hay deuda mayor a 0 y no está pagada
    if ed['deuda_omnisoft'] > 0 and ed['estado_pago'] != 'PAGADO':
        if ed['deuda_vencimiento']:
            hoy = date.today()
            delta = ed['deuda_vencimiento'] - hoy
            dias_restantes = delta.days
            
            # Definir nivel de alerta según los días
            if dias_restantes < 0: alerta_deuda = "VENCIDO"
            elif dias_restantes <= 3: alerta_deuda = "CRITICO"
            else: alerta_deuda = "NORMAL"

    return render_template('dash_admin.html', 
        edificio=dict(ed), 
        unidades=u_proc, 
        finanzas=fin, 
        stats={'unidades': len(u_proc)}, 
        calendario=calendar.monthcalendar(y, m), 
        eventos=eventos, 
        mes_actual=mes_nombre, 
        anio_actual=y, 
        nav=nav, 
        encomiendas=enc, 
        parking=obtener_estado_parking_real(eid), 
        deuda_aexon=deuda_aexon, 
        espacios=espacios, 
        reservas=reservas, 
        whatsapp_soporte="56912345678", 
        new_credentials=session.pop('new_credentials_unidad', None),
        # Variables nuevas para la alerta
        dias_restantes=dias_restantes,
        alerta_deuda=alerta_deuda,
        indicadores=obtener_indicadores()
        
    )
    
    
@app.route('/admin/gastos')
def admin_gastos():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    eid = session.get('edificio_id')
    y, m = get_safe_date_params()
    mes_nombre = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"][m]
    nav = calcular_navegacion(m, y)
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM gastos WHERE edificio_id = %s AND mes = %s AND anio = %s ORDER BY fecha DESC", (eid, m, y)); gastos = cur.fetchall()
    tg = sum(g['monto'] for g in gastos)
    cur.execute("""SELECT p.*, u.numero as unidad_numero, u.owner_json FROM historial_pagos p JOIN unidades u ON p.unidad_id = u.id WHERE p.edificio_id = %s AND ((p.mes_periodo = %s AND p.anio_periodo = %s) OR (p.mes_periodo IS NULL AND EXTRACT(MONTH FROM p.fecha) = %s AND EXTRACT(YEAR FROM p.fecha) = %s)) ORDER BY p.fecha DESC""", (eid, m, y, m, y))
    ing_raw = cur.fetchall(); ti = sum(i['monto'] for i in ing_raw); ing = []
    for i in ing_raw: i['owner'] = parse_json_field(i.get('owner_json')); ing.append(i)
    cur.execute("SELECT * FROM cierres_mes WHERE edificio_id = %s AND mes = %s AND anio = %s", (eid, m, y)); c = cur.fetchone()
    cur.close(); conn.close()
    return render_template('admin_gastos.html', gastos=gastos, ingresos=ing, total_gastos=tg, total_ingresos=ti, balance=ti-tg, mes_actual=m, anio_actual=y, mes_nombre=mes_nombre, mes_cerrado=bool(c), nav=nav)

@app.route('/admin/gastos/nuevo', methods=['POST'])
def nuevo_gasto():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    eid = session.get('edificio_id'); f = request.form.get('fecha'); dt = datetime.strptime(f, '%Y-%m-%d'); m, y = dt.month, dt.year
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id FROM cierres_mes WHERE edificio_id=%s AND mes=%s AND anio=%s", (eid, m, y))
    if cur.fetchone(): conn.close(); flash(f"⛔ Mes CERRADO."); return redirect(url_for('admin_gastos', month=m, year=y))
    cur.execute("INSERT INTO gastos (edificio_id, categoria, descripcion, monto, fecha, mes, anio, comprobante_url) VALUES (%s, %s, %s, %s, %s, %s, %s, 'demo.pdf')", (eid, request.form.get('categoria'), request.form.get('descripcion'), request.form.get('monto'), f, m, y))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('admin_gastos', month=m, year=y))

@app.route('/admin/gastos/cierre_mes', methods=['POST'])
def cierre_mes():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    eid = session.get('edificio_id'); m = int(request.form.get('mes')); y = int(request.form.get('anio'))
    tg = int(request.form.get('total_gastos')) if request.form.get('total_gastos') else 0
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT id, numero, prorrateo, deuda_monto FROM unidades WHERE edificio_id = %s", (eid,)); units = cur.fetchall()
    for u in units:
        if u['prorrateo']: cur.execute("UPDATE unidades SET deuda_monto = %s, estado_deuda = 'MOROSO' WHERE id = %s", ((u['deuda_monto'] or 0) + int(tg * (u['prorrateo'] / 100)), u['id']))
    cur.execute("INSERT INTO cierres_mes (edificio_id, mes, anio, total_gastos, admin_responsable) VALUES (%s, %s, %s, %s, %s)", (eid, m, y, tg, session.get('nombre')))
    cur.execute("UPDATE gastos SET cerrado = TRUE WHERE edificio_id = %s AND mes = %s AND anio = %s", (eid, m, y))
    conn.commit(); cur.close(); conn.close(); flash(f"🏆 Cierre Exitoso!"); return redirect(url_for('admin_gastos', month=m, year=y))

@app.route('/admin/conserjes/listar')
def listar_conserjes():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT rut, nombre, email, telefono, activo FROM usuarios WHERE edificio_id = %s AND rol = 'conserje'", (session.get('edificio_id'),))
    res = cur.fetchall(); cur.close(); conn.close(); return jsonify(res)

@app.route('/admin/conserjes/crear', methods=['POST'])
def crear_conserje():
    try:
        new_pass = f"Conserje{random.randint(1000,9999)}"
        hashed_pass = generate_password_hash(new_pass)
        rut = formatear_rut(request.form.get('rut'))
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO usuarios (rut, nombre, email, telefono, rol, password, edificio_id, activo) VALUES (%s, %s, %s, %s, 'conserje', %s, %s, TRUE) ON CONFLICT (rut) DO NOTHING RETURNING rut", (rut, request.form.get('nombre'), request.form.get('email'), '', hashed_pass, session.get('edificio_id')))
        msg = 'success' if cur.fetchone() else 'existe'; conn.commit(); cur.close(); conn.close(); return jsonify({'status': msg, 'password': new_pass})
    except: return jsonify({'status': 'error'})

@app.route('/admin/conserjes/eliminar', methods=['POST'])
def eliminar_conserje():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("DELETE FROM usuarios WHERE rut = %s", (request.form.get('rut'),)); conn.commit(); cur.close(); conn.close(); return jsonify({'status': 'success'})

@app.route('/admin/conserjes/reset_clave', methods=['POST'])
def reset_clave_conserje():
    np = f"Conserje{random.randint(1000,9999)}"
    hashed_np = generate_password_hash(np)
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE usuarios SET password = %s WHERE rut = %s", (hashed_np, request.form.get('rut'))); conn.commit(); cur.close(); conn.close(); return jsonify({'status': 'success', 'password': np})

@app.route('/admin/residentes/reset_clave', methods=['POST'])
@login_required
def reset_clave_residente():
    # Solo admin o superadmin pueden resetear
    if current_user.rol not in ['admin', 'superadmin']:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403
        
    unidad_id = request.form.get('unidad_id')
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Obtener datos del residente desde la unidad
    cur.execute("SELECT numero, tenant_json, owner_json, edificio_id FROM unidades WHERE id = %s", (unidad_id,))
    unidad = cur.fetchone()
    
    # Priorizamos al Arrendatario (tenant), si no hay, usamos al Propietario (owner)
    residente_data = parse_json_field(unidad['tenant_json'])
    if not residente_data.get('rut'):
        residente_data = parse_json_field(unidad['owner_json'])
    
    rut = residente_data.get('rut')
    nombre = residente_data.get('nombre')
    
    if not rut:
        return jsonify({'status': 'error', 'message': 'La unidad no tiene un RUT asignado'}), 400

    # 2. Generar nueva clave temporal
    nueva_pass = str(random.randint(1000, 9999))
    hashed_pass = generate_password_hash(nueva_pass)
    
    # 3. Upsert en la tabla usuarios (Crea o Actualiza)
    cur.execute("""
        INSERT INTO usuarios (rut, nombre, password, rol, edificio_id, activo)
        VALUES (%s, %s, %s, 'residente', %s, TRUE)
        ON CONFLICT (rut) DO UPDATE 
        SET password = EXCLUDED.password, activo = TRUE
    """, (rut, nombre, hashed_pass, unidad['edificio_id']))
    
    conn.commit()
    cur.close(); conn.close()
    
    return jsonify({
        'status': 'success', 
        'password': nueva_pass, 
        'residente_nombre': nombre
    })
    

@app.route('/admin/residentes/registrar_pago', methods=['POST'])
def registrar_pago_residente():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    
    uid = request.form.get('unidad_id')
    monto = int(request.form.get('monto_pago'))
    archivo = request.files.get('comprobante') # CAPTURAR FOTO
    
    filename = 'manual.jpg'
    if archivo:
        filename = f"pago_residente_{uid}_{random.randint(1000,9999)}.jpg"
        archivo.save(os.path.join(app.root_path, 'static', 'uploads', filename))

    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    
    # Lógica de mes (igual que antes)
    cur.execute("SELECT mes, anio FROM cierres_mes WHERE edificio_id = %s ORDER BY anio DESC, mes DESC LIMIT 1", (eid,)); uc = cur.fetchone()
    if uc: mp, ap = (uc['mes']+1, uc['anio']) if uc['mes'] < 12 else (1, uc['anio']+1)
    else: now = datetime.now(); mp, ap = now.month, now.year
    
    cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (monto, uid))
    # GUARDAMOS LA URL DE LA FOTO
    cur.execute("INSERT INTO historial_pagos (edificio_id, unidad_id, monto, metodo, comprobante_url, mes_periodo, anio_periodo) VALUES (%s, %s, %s, 'TRANSFERENCIA', %s, %s, %s)", (eid, uid, monto, filename, mp, ap))
    
    conn.commit(); cur.close(); conn.close()
    flash("Pago registrado con comprobante."); return redirect(url_for('panel_admin'))


@app.route('/admin/residentes/multar', methods=['POST'])
def multar_residente():
    uid = request.form.get('unidad_id'); m = int(request.form.get('monto_multa'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE unidades SET deuda_monto = deuda_monto + %s WHERE id = %s", (m, uid))
    cur.execute("INSERT INTO multas (edificio_id, unidad_id, monto, motivo, fecha) VALUES (%s, %s, %s, %s, NOW())", (session.get('edificio_id'), uid, m, request.form.get('motivo')))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_admin'))

@app.route('/admin/residentes/historial/<int:id>')
def historial_residente(id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT remitente, recepcion FROM encomiendas WHERE unidad_id = %s ORDER BY recepcion DESC LIMIT 5", (id,)); enc = cur.fetchall()
    cur.execute("SELECT fecha, monto, metodo FROM historial_pagos WHERE unidad_id = %s ORDER BY fecha DESC LIMIT 5", (id,)); pag = cur.fetchall()
    cur.close(); conn.close(); return jsonify({'encomiendas': enc, 'pagos': [{'fecha': p['fecha'].strftime('%d/%m'), 'monto': p['monto'], 'metodo': p['metodo']} for p in pag]})



@app.route('/admin/gestionar-estacionamiento', methods=['POST'])
def gestionar_estacionamiento():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    acc = request.form.get('accion'); uid = request.form.get('unidad_id'); conn = get_db_connection(); cur = conn.cursor()
    if acc == 'editar': cur.execute("UPDATE unidades SET estacionamiento=%s WHERE id=%s", (request.form.get('nuevo_nombre_parking').upper(), uid))
    elif acc == 'eliminar': cur.execute("UPDATE unidades SET estacionamiento=NULL WHERE id=%s", (uid,))
    conn.commit(); cur.close(); conn.close(); flash("Estacionamiento actualizado"); return redirect(url_for('panel_admin'))

@app.route('/admin/activos')
def admin_activos():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    
    eid = session.get('edificio_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s ORDER BY nombre", (eid,))
    activos = cur.fetchall()
    cur.close()
    conn.close()

    gasto_mes = 0
    hoy = date.today()
    
    # Obtenemos el último día del mes actual para saber el límite
    ultimo_dia_mes = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])

    for a in activos:
        if a['ultimo_servicio']:
            prox = a['ultimo_servicio']
            periodo = a['periodicidad_dias']
            costo = a['costo_estimado']

            # 1. Avanzamos la fecha hasta llegar al futuro (próxima mantención real)
            while prox < hoy:
                prox += timedelta(days=periodo)
            
            # Guardamos esta fecha para mostrarla en la tabla (la más próxima)
            a['prox_fecha'] = prox.strftime('%d/%m/%Y')
            a['dias_restantes'] = (prox - hoy).days

            # 2. CALCULO DE PROYECCIÓN (Aquí está la corrección)
            # Usamos una variable temporal para ver cuántas veces cae en ESTE mes
            temp_date = prox
            
            # Mientras la fecha caiga dentro de este mes y año...
            while temp_date <= ultimo_dia_mes:
                if temp_date.month == hoy.month and temp_date.year == hoy.year:
                    gasto_mes += costo # Sumamos el costo
                
                # Avanzamos al siguiente periodo para ver si cabe otra mantención
                temp_date += timedelta(days=periodo)

    return render_template('admin_activos.html', activos=activos, gasto_mes=gasto_mes)


@app.route('/admin/activos/guardar', methods=['POST'])
def guardar_activo():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO activos (edificio_id, nombre, periodicidad_dias, costo_estimado, ultimo_servicio) VALUES (%s, %s, %s, %s, %s)", (session.get('edificio_id'), request.form.get('nombre'), request.form.get('periodicidad'), request.form.get('costo'), request.form.get('ultimo_servicio')))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('admin_activos'))

@app.route('/admin/activos/eliminar/<int:id>')
def eliminar_activo(id):
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("DELETE FROM activos WHERE id=%s", (id,)); conn.commit(); cur.close(); conn.close(); return redirect(url_for('admin_activos'))

@app.route('/admin/residentes/guardar_edicion', methods=['POST'])
def guardar_edicion_residente():
    tenant_rut = formatear_rut(request.form.get('tenant_rut'))
    t_json = json.dumps({'rut': tenant_rut, 'nombre': request.form.get('tenant_nombre'), 'email': request.form.get('tenant_email'), 'fono': request.form.get('tenant_fono')})
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE unidades SET piso=%s, prorrateo=%s, tenant_json=%s WHERE id=%s", (request.form.get('piso'), request.form.get('prorrateo'), t_json, request.form.get('unidad_id')))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_admin'))

# --- CORREGIDO: GENERAR CLAVE Y GUARDAR EN BD ---
@app.route('/admin/residentes/reset_clave', methods=['POST'])
def generar_clave_residente():
    unidad_id = request.form.get('unidad_id')
    new_pass = f"Habita{random.randint(10000,99999)}"
    hashed_pass = generate_password_hash(new_pass)
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT edificio_id, owner_json, tenant_json FROM unidades WHERE id = %s", (unidad_id,))
        u = cur.fetchone()
        
        if not u: return jsonify({'status': 'error', 'message': 'Unidad no encontrada'})

        t = parse_json_field(u.get('tenant_json'))
        o = parse_json_field(u.get('owner_json'))
        target = t if t.get('rut') else o
        
        if not target or not target.get('rut'):
            return jsonify({'status': 'error', 'message': 'Sin residente asignado'})

        rut_limpio = formatear_rut(target.get('rut'))
        nombre = target.get('nombre', 'Residente')
        eid = u['edificio_id']

        cur.execute("""
            INSERT INTO usuarios (rut, nombre, email, telefono, password, rol, edificio_id, activo)
            VALUES (%s, %s, %s, %s, %s, 'residente', %s, TRUE)
            ON CONFLICT (rut) 
            DO UPDATE SET password = %s, activo = TRUE, edificio_id = %s;
        """, (rut_limpio, nombre, target.get('email',''), target.get('fono',''), hashed_pass, eid, hashed_pass, eid))
        conn.commit()
        
        return jsonify({'status': 'success', 'password': new_pass, 'residente_nombre': nombre, 'residente_fono': target.get('fono','')})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        cur.close(); conn.close()

@app.route('/admin/auditoria')
def admin_auditoria(): return render_template('base.html')
@app.route('/admin/difusion', methods=['POST'])
def admin_difusion(): flash(f"📢 Alerta: {request.form.get('mensaje')}"); return redirect(url_for('panel_admin'))

# --- MÓDULO ESPACIOS COMUNES (NUEVO) ---
@app.route('/admin/espacios/guardar', methods=['POST'])
def admin_guardar_espacio():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        eid = session.get('edificio_id')
        cur.execute("INSERT INTO espacios (edificio_id, nombre, capacidad, precio) VALUES (%s, %s, %s, %s)", 
                   (eid, request.form.get('nombre').upper(), request.form.get('capacidad'), int(request.form.get('precio') or 0)))
        conn.commit(); flash("✅ Espacio creado.")
    except: flash("❌ Error al guardar.")
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/espacios/eliminar/<int:id>', methods=['GET'])
def admin_eliminar_espacio(id):
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE espacios SET activo = FALSE WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/reservas/cancelar', methods=['POST'])
def admin_cancelar_reserva():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    rid = request.form.get('reserva_id'); reembolsar = request.form.get('reembolsar') == 'on'
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE reservas SET estado = 'CANCELADA' WHERE id = %s RETURNING unidad_id, espacio_id", (rid,))
        res = cur.fetchone()
        if res and reembolsar:
            cur.execute("SELECT precio FROM espacios WHERE id = %s", (res['espacio_id'],))
            espacio = cur.fetchone()
            if espacio['precio'] > 0:
                cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (espacio['precio'], res['unidad_id']))
        conn.commit(); flash("✅ Reserva cancelada.")
    except: flash("❌ Error.")
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/residente/reservar', methods=['POST'])
def residente_crear_reserva():
    if session.get('rol') != 'residente': return redirect(url_for('home'))
    uid = session.get('unidad_id_residente'); eid = session.get('edificio_id')
    espacio_id = request.form.get('espacio_id')
    fecha = request.form.get('fecha')
    hora = request.form.get('hora') # <--- CAPTURAMOS LA HORA
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Validar disponibilidad
        cur.execute("SELECT id FROM reservas WHERE espacio_id=%s AND fecha_uso=%s AND hora_inicio=%s AND estado='CONFIRMADA'", (espacio_id, fecha, hora))
        if cur.fetchone(): flash("⛔ Horario no disponible."); return redirect(url_for('panel_residente'))

        cur.execute("SELECT precio, nombre FROM espacios WHERE id=%s", (espacio_id,))
        espacio = cur.fetchone()
        
        # INSERTAMOS CON LA HORA
        cur.execute("INSERT INTO reservas (espacio_id, unidad_id, fecha_uso, hora_inicio, estado) VALUES (%s, %s, %s, %s, 'CONFIRMADA')", (espacio_id, uid, fecha, hora))
        
        if espacio['precio'] > 0:
            cur.execute("UPDATE unidades SET deuda_monto = deuda_monto + %s WHERE id = %s", (espacio['precio'], uid))
            
        conn.commit(); flash(f"✅ Reserva: {espacio['nombre']} a las {hora}")
    except Exception as e: print(e); flash("❌ Error al reservar")
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_residente'))


@app.route('/panel-superadmin')
@login_required
def panel_superadmin():
    if current_user.rol != 'superadmin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. ESTADO DE LA BASE DE DATOS (db_info) - ¡ESTO FALTABA!
    try:
        cur.execute("SELECT version()")
        # Convertimos a string por si acaso viene en formato raro
        ver_raw = cur.fetchone()
        db_ver = ver_raw['version'] if ver_raw else "Unknown"
        # Limpiamos el string para que no sea tan largo
        db_ver = str(db_ver).split(',')[0]
        
        db_info = {'status': "ONLINE", 'version': db_ver, 'color': 'text-success'}
    except Exception as e:
        print(f"Error DB Info: {e}")
        db_info = {'status': "OFFLINE", 'version': "Error conexión", 'color': 'text-danger'}

    # 2. ESTADÍSTICAS GENERALES
    cur.execute("""
        SELECT e.*, COUNT(u.rut) as cantidad_usuarios 
        FROM edificios e 
        LEFT JOIN usuarios u ON e.id = u.edificio_id AND u.activo = TRUE 
        WHERE e.activo = TRUE 
        GROUP BY e.id 
        ORDER BY e.id ASC
    """)
    edificios_stats = cur.fetchall()
    
    total_edificios = len(edificios_stats)
    total_usuarios = sum(e['cantidad_usuarios'] for e in edificios_stats)

    # 3. LOGS DEL SISTEMA (Ojo de Dios)
    query_logs = """
        (SELECT 'VISITA' as tipo, v.ingreso as fecha, e.nombre as edificio, CONCAT('Visita: ', v.nombre_visita) as detalle, 'Conserjería' as actor FROM visitas v JOIN edificios e ON v.edificio_id = e.id) 
        UNION ALL 
        (SELECT 'INCIDENCIA', i.fecha, e.nombre, i.titulo as detalle, i.autor as actor FROM incidencias i JOIN edificios e ON i.edificio_id = e.id) 
        UNION ALL 
        (SELECT 'PAGO', h.fecha, e.nombre, CONCAT('Pago GC: $', h.monto) as detalle, 'App' as actor FROM historial_pagos h JOIN edificios e ON h.edificio_id = e.id) 
        UNION ALL 
        (SELECT 'ENCOMIENDA', enc.recepcion, e.nombre, CONCAT('Paquete: ', enc.remitente) as detalle, 'Conserjería' as actor FROM encomiendas enc JOIN edificios e ON enc.edificio_id = e.id) 
        ORDER BY fecha DESC LIMIT 30
    """
    try:
        cur.execute(query_logs)
        logs = cur.fetchall()
    except:
        logs = []

    # Procesar fechas de logs
    logs_proc = []
    now = datetime.now()
    for l in logs:
        # Calcular tiempo transcurrido (hace X min)
        if l['fecha']:
            delta = now - l['fecha']
            if delta.days == 0:
                minutos = delta.seconds // 60
                tiempo = f"Hace {minutos}m"
            else:
                tiempo = f"Hace {delta.days}d"
            
            fecha_str = l['fecha'].strftime('%d/%m %H:%M')
        else:
            tiempo = "-"
            fecha_str = "-"

        logs_proc.append({
            'tipo': l['tipo'],
            'fecha_full': fecha_str,
            'tiempo': tiempo,
            'edificio': l['edificio'],
            'detalle': l['detalle'],
            'actor': l['actor']
        })

    cur.close()
    conn.close()

    # 4. RENDERIZAR CON TODO (Incluyendo indicadores y db_info)
    return render_template('dash_super.html', 
                           stats={'edificios': total_edificios, 'users': total_usuarios}, 
                           edificios=edificios_stats, 
                           global_logs=logs_proc, 
                           db_info=db_info,               # <--- AQUÍ ESTÁ LA SOLUCIÓN
                           indicadores=obtener_indicadores()) # <--- Y TUS INDICADORES NUEVOS
    
    
# 1. REEMPLAZAR ESTA RUTA (SUPER ADMIN ENVÍA COBRO)
@app.route('/superadmin/enviar_cobro', methods=['POST'])
def enviar_cobro():
    if session.get('rol') != 'superadmin': return redirect(url_for('home'))
    
    eid = request.form.get('edificio_id')
    monto_raw = request.form.get('monto') # Recibe "100.000"
    desc = request.form.get('descripcion')
    vence = request.form.get('vencimiento')
    
    # --- CORRECCIÓN: LIMPIEZA DE MONTO ---
    # Eliminamos puntos y comas para dejar solo el número puro
    try:
        if monto_raw:
            monto = int(str(monto_raw).replace('.', '').replace(',', ''))
        else:
            monto = 0
    except ValueError:
        flash("Error: El monto ingresado no es válido.")
        return redirect(url_for('super_detalle_edificio', id=eid))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        UPDATE edificios 
        SET deuda_omnisoft = %s, 
            deuda_descripcion = %s, 
            deuda_vencimiento = %s, 
            estado_pago = 'PENDIENTE',
            deuda_comprobante_url = NULL 
        WHERE id = %s
    """, (monto, desc, vence, eid))
    
    conn.commit(); cur.close(); conn.close()
    flash("Cobro enviado al edificio.")
    return redirect(url_for('super_detalle_edificio', id=eid))


# --- REEMPLAZAR ESTA FUNCIÓN EN APP.PY ---
@app.route('/admin/pagar_servicio', methods=['POST'])
def admin_pagar_servicio():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    
    eid = session.get('edificio_id')
    file = request.files.get('comprobante')
    
    if not file:
        flash("Debes subir una foto del comprobante.")
        return redirect(url_for('panel_admin'))
    
    # 1. Definir nombre y ruta (Guardamos en static/uploads)
    filename = f"pago_{eid}_{random.randint(1000,9999)}.jpg"
    
    # Asegúrate de que esta carpeta exista o créala
    upload_folder = os.path.join(app.root_path, 'static', 'uploads')
    os.makedirs(upload_folder, exist_ok=True) # Crea la carpeta si no existe
    
    # 2. Guardar el archivo físicamente
    try:
        file.save(os.path.join(upload_folder, filename))
    except Exception as e:
        flash(f"Error al guardar imagen: {e}")
        return redirect(url_for('panel_admin'))
    
    # 3. Guardar referencia en BD
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        UPDATE edificios 
        SET estado_pago = 'REVISION', 
            deuda_comprobante_url = %s 
        WHERE id = %s
    """, (filename, eid))
    conn.commit(); cur.close(); conn.close()
    
    flash("Comprobante enviado. Esperando validación de Aexon.")
    return redirect(url_for('panel_admin'))
# EN: app.py - SECCIÓN SUPER ADMIN





@app.route('/superadmin/registrar_pago_edificio', methods=['POST'])
def registrar_pago_edificio():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE edificios SET deuda_omnisoft = 0, estado_pago = 'PAGADO' WHERE id = %s", (request.form.get('edificio_id'),))
    conn.commit(); cur.close(); conn.close(); flash("Pago registrado"); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))

@app.route('/superadmin/crear_unidad', methods=['POST'])
def crear_unidad():
    o = json.dumps({'rut': formatear_rut(request.form.get('owner_rut')), 'nombre': request.form.get('owner_nombre'), 'email': request.form.get('owner_email'), 'fono': request.form.get('owner_fono')})
    t = json.dumps({'rut': formatear_rut(request.form.get('tenant_rut')), 'nombre': request.form.get('tenant_nombre'), 'email': request.form.get('tenant_email'), 'fono': request.form.get('tenant_fono')})
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO unidades (edificio_id, numero, piso, metraje, prorrateo, owner_json, tenant_json) VALUES (%s, %s, %s, %s, %s, %s, %s)", (request.form.get('edificio_id'), request.form.get('numero'), request.form.get('piso'), request.form.get('metraje'), request.form.get('prorrateo'), o, t))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))

@app.route('/superadmin/crear_admin_rapido', methods=['POST'])
@login_required
def crear_admin_rapido():
    rut = formatear_rut(request.form.get('rut'))
    nombre = request.form.get('nombre')
    email = request.form.get('email')
    edificio_id = request.form.get('edificio_id')
    
    # Generar Password Aleatoria Segura
    new_pass = f"Habita{random.randint(1000,9999)}$"
    hashed_pass = generate_password_hash(new_pass)
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO usuarios (rut, nombre, email, password, rol, edificio_id, activo) 
            VALUES (%s, %s, %s, %s, 'admin', %s, TRUE) 
            ON CONFLICT (rut) 
            DO UPDATE SET rol='admin', edificio_id=%s, activo=TRUE, password=%s
        """, (rut, nombre, email, hashed_pass, edificio_id, edificio_id, hashed_pass))
        conn.commit()
        
        # AQUÍ ESTÁ EL TRUCO: Enviamos los datos separados por una barra vertical "|"
        # Formato: NOMBRE | RUT | PASSWORD
        flash(f"{nombre}|{rut}|{new_pass}", "credenciales_new_admin")
        
    except Exception as e:
        conn.rollback()
        flash(f"Error al crear admin: {str(e)}", "error")
    finally:
        cur.close(); conn.close()

    return redirect(url_for('super_detalle_edificio', id=edificio_id))

@app.route('/superadmin/reset_pass_admin', methods=['POST'])
@login_required
def reset_pass_admin():
    rut_admin = request.form.get('rut_admin')
    edificio_id = request.form.get('edificio_id')
    
    # Generar Password Aleatoria
    new_pass = f"Reset{random.randint(1000,9999)}$"
    hashed_pass = generate_password_hash(new_pass)
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Recuperamos el nombre para mostrarlo bonito en la alerta
        cur.execute("SELECT nombre FROM usuarios WHERE rut = %s", (rut_admin,))
        u = cur.fetchone()
        nombre = u['nombre'] if u else 'Administrador'

        cur.execute("UPDATE usuarios SET password=%s WHERE rut=%s", (hashed_pass, rut_admin))
        conn.commit()
        
        # ENVIAMOS LA SEÑAL ESPECIAL
        flash(f"{nombre}|{rut_admin}|{new_pass}", "credenciales_reset")
        
    except Exception as e:
        flash("Error al resetear clave", "error")
    finally:
        cur.close(); conn.close()
        
    return redirect(url_for('super_detalle_edificio', id=edificio_id))


@app.route('/superadmin/editar_admin', methods=['POST'])
def superadmin_editar_admin():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE usuarios SET nombre=%s, email=%s, telefono=%s WHERE rut=%s", (request.form.get('nombre'), request.form.get('email'), request.form.get('telefono'), request.form.get('rut')))
    conn.commit(); cur.close(); conn.close(); flash("Datos actualizados"); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))



# ==========================================
# RUTA LOGIN UNIFICADA Y CORREGIDA
# ==========================================
# ==========================================
# RUTA LOGIN UNIFICADA Y CORREGIDA
# ==========================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    # 1. GET: Mostrar formulario
    if request.method == 'GET':
        if current_user.is_authenticated:
            if session.get('rol') == 'superadmin': return redirect(url_for('panel_superadmin'))
            if session.get('rol') == 'admin': return redirect(url_for('panel_admin'))
            if session.get('rol') == 'conserje': return redirect(url_for('panel_conserje'))
            if session.get('rol') == 'residente': return redirect(url_for('panel_residente'))
        return render_template('login.html')

    # 2. POST: Procesar login
    usuario_input = request.form.get('email') or request.form.get('username') or request.form.get('rut')
    password = request.form.get('password')
    pass_input = str(password).strip()
    
    # Limpieza inteligente: Si parece RUT (tiene números), lo formateamos. Si es texto, lo dejamos quieto.
    import re
    if re.search(r'\d', usuario_input): # Si tiene números, asumimos que es RUT
        rut_busqueda = formatear_rut(usuario_input)
    else:
        rut_busqueda = usuario_input # Si es solo letras (ej: ALEXIS), lo dejamos tal cual

    print(f"🔍 INTENTO LOGIN: Buscando '{usuario_input}' (RUT Normalizado: {rut_busqueda})")

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # BÚSQUEDA INSENSIBLE A MAYÚSCULAS (ILIKE / LOWER)
        cur.execute("""
            SELECT * FROM usuarios 
            WHERE LOWER(email) = LOWER(%s) 
               OR rut = %s 
               OR LOWER(nombre) LIKE LOWER(%s)
        """, (usuario_input, rut_busqueda, f"%{usuario_input}%"))
        
        user_data = cur.fetchone()
        
        if user_data:
            print(f"✅ USUARIO ENCONTRADO: {user_data['nombre']} (Rol: {user_data['rol']})")
            
            # Verificación de contraseña
            pass_db = str(user_data.get('password', '')).strip()
            
            # Verificamos si es un hash (empieza con método de hash) o texto plano (legacy)
            is_valid = check_password_hash(pass_db, pass_input) if pass_db.startswith(('scrypt:', 'pbkdf2:')) else (pass_db == pass_input)
            
            if is_valid:
                
                # --- LOGIN EXITOSO ---
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

                # --- LÓGICA RESIDENTE (ANTI-BUCLE) ---
                if user_data['rol'] == 'residente':
                    print("   🏠 Buscando unidad del residente...")
                    cur.execute("""
                        SELECT id, numero FROM unidades 
                        WHERE edificio_id = %s 
                        AND (owner_json::text LIKE %s OR tenant_json::text LIKE %s)
                    """, (user_data['edificio_id'], f"%{user_data['rut']}%", f"%{user_data['rut']}%"))
                    
                    unidad = cur.fetchone()
                    
                    if unidad:
                        print(f"   📍 Unidad encontrada: {unidad['numero']}")
                        session['unidad_id_residente'] = unidad['id']
                        session['numero_unidad'] = unidad['numero']
                        return redirect(url_for('panel_residente'))
                    else:
                        print("   ❌ ERROR: Usuario residente sin unidad asignada.")
                        flash("Usuario válido, pero sin departamento asignado.", "warning")
                        return redirect(url_for('login'))

                # Redirecciones Staff
                if user_data['rol'] == 'superadmin': return redirect(url_for('panel_superadmin'))
                if user_data['rol'] == 'admin': return redirect(url_for('panel_admin'))
                if user_data['rol'] == 'conserje': return redirect(url_for('panel_conserje'))
                
                return redirect(url_for('home'))
            else:
                print(f"   ❌ CONTRASEÑA INCORRECTA. (DB: '{pass_db}' vs Input: '{pass_input}')")
                flash("Contraseña incorrecta.", "error")
                return redirect(url_for('login'))
        else:
            print("   ❌ USUARIO NO ENCONTRADO EN BD")
            flash("Usuario no encontrado.", "error")
            return redirect(url_for('login'))

    except Exception as e:
        print(f"🔥 ERROR CRÍTICO LOGIN: {e}")
        flash(f"Error de sistema: {e}", "error")
        return redirect(url_for('login'))
    finally:
        cur.close()
        conn.close()                
                        
# --- KILL SWITCH: ACTIVAR / DESACTIVAR EDIFICIO ---
@app.route('/superadmin/toggle_edificio/<int:edificio_id>', methods=['POST'])
def superadmin_toggle_edificio(edificio_id):
    if session.get('rol') != 'superadmin': return jsonify({'status': 'error'})
    
    data = request.get_json()
    nuevo_estado = data.get('activo') # True (Encendido) o False (Apagado)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Actualizamos el estado
    cur.execute("UPDATE edificios SET activo = %s WHERE id = %s", (nuevo_estado, edificio_id))
    conn.commit()
    
    cur.close(); conn.close()
    return jsonify({'status': 'success'})

@app.route('/superadmin/eliminar_edificio', methods=['POST'])
def eliminar_edificio():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE edificios SET activo = FALSE WHERE id = %s", (request.form.get('edificio_id'),))
    cur.execute("UPDATE usuarios SET activo = FALSE WHERE edificio_id = %s", (request.form.get('edificio_id'),))
    conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_superadmin'))

@app.route('/superadmin/toggle_acceso', methods=['POST'])
def toggle_acceso():
    conn=get_db_connection();cur=conn.cursor();cur.execute("UPDATE usuarios SET activo=%s WHERE rut=%s",(request.form.get('nuevo_estado')=='true',request.form.get('rut_admin')));conn.commit();cur.close();conn.close(); return redirect(url_for('super_detalle_edificio', id=int(request.form.get('edificio_id'))))

@app.route('/superadmin/carga_masiva_csv', methods=['POST'])
def carga_masiva_csv():
    try:
        ed=int(request.form.get('edificio_id_retorno')); f=request.files['archivo_csv']; s=io.TextIOWrapper(f.stream._file,"utf-8",newline=""); r=csv.DictReader(s); conn=get_db_connection(); cur=conn.cursor()
        for x in r:
            o=json.dumps({'rut':formatear_rut(x.get('owner_rut')),'nombre':x.get('owner_nombre'),'email':x.get('owner_email')}); t=json.dumps({'rut':formatear_rut(x.get('tenant_rut')),'nombre':x.get('tenant_nombre'),'email':x.get('tenant_email')})
            cur.execute("INSERT INTO unidades (edificio_id,numero,piso,metraje,prorrateo,owner_json,tenant_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",(ed,x['numero'],x.get('piso',1),x['metraje'],x['prorrateo'],o,t))
        conn.commit(); cur.close(); conn.close(); return redirect(url_for('super_detalle_edificio', id=ed))
    except: return redirect(url_for('super_detalle_edificio', id=ed))

@app.route('/fix-residente')
def fix_residente_demo():
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM unidades LIMIT 1"); u = cur.fetchone()
        if not u: return "Error"
        uid = u['id']; eid = session.get('edificio_id') or 1
        rut_demo = formatear_rut("1-9") # 1-9
        hashed_demo = generate_password_hash("1234")
        o = json.dumps({"rut": rut_demo, "nombre": "Residente Test", "email": "test@habita.cl"})
        cur.execute("UPDATE unidades SET owner_json = %s WHERE id = %s", (o, uid))
        cur.execute("INSERT INTO usuarios (rut, nombre, email, password, rol, edificio_id, activo) VALUES (%s, 'Residente Test', 'test@habita.cl', %s, 'residente', %s, TRUE) ON CONFLICT (rut) DO UPDATE SET rol='residente', edificio_id=%s, activo=TRUE", (rut_demo, hashed_demo, eid, eid))
        conn.commit(); return f"OK: {rut_demo}"
    except Exception as e: return f"Error {e}"
    finally: cur.close(); conn.close()
# --- NUEVA RUTA PARA BITÁCORA / AUDITORÍA ---
@app.route('/admin/logs/listar')
def admin_logs_listar():
    if session.get('rol') != 'admin': return jsonify([])
    
    eid = session.get('edificio_id')
    # Capturamos filtros del frontend
    filtro_fecha = request.args.get('fecha')
    filtro_unidad = request.args.get('unidad') # Opcional

    # Si no envían fecha, usamos HOY por defecto
    if not filtro_fecha:
        filtro_fecha = date.today().strftime('%Y-%m-%d')

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Usamos una subconsulta (CTE) para filtrar limpiamente sobre el resultado unido
    query = """
        SELECT * FROM (
            (SELECT 'VISITA' as tipo, v.ingreso as fecha_full, v.ingreso::date as fecha_dia, CONCAT('Entrada: ', v.nombre_visita) as detalle, v.patente as extra, u.numero as unidad 
             FROM visitas v LEFT JOIN unidades u ON v.unidad_id = u.id WHERE v.edificio_id = %s)
            UNION ALL
            (SELECT 'INCIDENCIA', fecha, fecha::date, titulo, autor, '-' FROM incidencias WHERE edificio_id = %s)
            UNION ALL
            (SELECT 'PAGO', h.fecha, h.fecha::date, CONCAT('Abono $', h.monto), h.metodo, u.numero 
             FROM historial_pagos h JOIN unidades u ON h.unidad_id = u.id WHERE h.edificio_id = %s)
            UNION ALL
            (SELECT 'ENCOMIENDA', enc.recepcion, enc.recepcion::date, CONCAT('Paquete: ', enc.remitente), 'Conserjería', u.numero 
             FROM encomiendas enc JOIN unidades u ON enc.unidad_id = u.id WHERE enc.edificio_id = %s)
        ) as master_log
        WHERE fecha_dia = %s
    """
    params = [eid, eid, eid, eid, filtro_fecha]

    # Si el usuario escribió un depto, agregamos ese filtro extra
    if filtro_unidad:
        query += " AND unidad = %s"
        params.append(filtro_unidad)

    query += " ORDER BY fecha_full DESC"

    cur.execute(query, tuple(params))
    logs = cur.fetchall()
    cur.close()
    conn.close()
    
    data = []
    for l in logs:
        data.append({
            'tipo': l['tipo'],
            'fecha': l['fecha_full'].strftime('%H:%M') if l['fecha_full'] else '-', # Solo mostramos hora porque ya filtramos por día
            'detalle': l['detalle'],
            'extra': l['extra'] or '-',
            'unidad': l['unidad'] or '-'
        })
    return jsonify(data)

# --- RUTA PÚBLICA PARA VER LA INVITACIÓN (CÓDIGO QR / ACCESO) ---
# EN: app.py

# EN: app.py

# EN: app.py

# EN: app.py

@app.route('/invitacion/<token>', methods=['GET', 'POST'])
def public_invitacion(token):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Buscamos la invitación y datos del edificio
    cur.execute("""
        SELECT i.*, u.numero as unidad_numero, e.nombre as edificio_nombre, e.direccion as edificio_direccion
        FROM invitaciones i
        JOIN unidades u ON i.unidad_id = u.id
        JOIN edificios e ON i.edificio_id = e.id 
        WHERE i.token = %s
    """, (token,))
    inv = cur.fetchone()

    # --- CANDADO 1: SI NO EXISTE ---
    if not inv:
        cur.close(); conn.close()
        return render_template('public_qr_exito.html', error="Invitación no encontrada o enlace roto.")

    # --- CANDADO 2: SI YA FUE USADO (SEGURIDAD TOTAL) ---
    if inv['estado'] == 'USADO':
        cur.close(); conn.close()
        # Renderizamos la plantilla de éxito pero en modo "Error/Caducado"
        return render_template('public_qr_exito.html', 
                             error="⛔ ESTE ENLACE YA CADUCÓ", 
                             mensaje="El pase ya fue utilizado para ingresar al recinto.")

    # --- LOGICA POST: GENERAR EL QR ---
    if request.method == 'POST':
        try:
            patente = request.form.get('patente', '').upper()
            
            # Si es vehículo, guardamos la patente
            if inv['tipo'] == 'VEHICULO' and patente:
                cur.execute("UPDATE invitaciones SET patente = %s WHERE id = %s", (patente, inv['id']))
                conn.commit()
                inv['patente'] = patente 

            cur.close(); conn.close()
            
            # Al enviar el formulario, mostramos el QR exitoso
            return render_template('public_qr_exito.html', inv=inv, token=token)

        except Exception as e:
            print(f"Error QR: {e}")
            return "Error procesando solicitud"

    # --- LOGICA GET: MOSTRAR FORMULARIO ---
    cur.close(); conn.close()
    return render_template('public_visita.html', inv=inv)

# --- 1. VALIDAR QR Y DEVOLVER OPCIONES (MODIFICADO) ---

           

# --- 2. NUEVA RUTA: CONFIRMAR INGRESO VEHICULAR CON PARKING ELEGIDO ---

@app.route('/conserje/visitas/confirmar_vehiculo', methods=['POST'])
def confirmar_ingreso_vehiculo():
    token = request.form.get('token')
    parking_id = request.form.get('parking_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM invitaciones WHERE token = %s", (token,))
    inv = cur.fetchone()
    
    parking_nombre = "Sin Asignar"
    if parking_id:
        # ACTUALIZAMOS A 'ocupado' (minúscula)
        cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s RETURNING nombre", (inv['patente'], parking_id))
        res_park = cur.fetchone()
        if res_park: parking_nombre = res_park['nombre']

    # INSERTAMOS USANDO parking_id (CORRECTO)
    cur.execute("""
        INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, parking_id, ingreso)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
    """, (inv['edificio_id'], inv['unidad_id'], inv['rut_visita'], inv['nombre_visita'], inv['patente'], parking_id))
    
    cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],))
    
    conn.commit()
    cur.close(); conn.close()
    
    return jsonify({'status': 'success', 'parking_nombre': parking_nombre, 'visita': inv['nombre_visita']})

# --- RUTA PARA GUARDAR LOS DATOS DE LA VISITA Y GENERAR EL QR FINAL ---
@app.route('/invitacion/guardar', methods=['POST'])
def guardar_invitacion_visita():
    token = request.form.get('token')
    nombre = request.form.get('nombre')
    rut = request.form.get('rut')
    
    # Capturamos la patente, quitamos espacios y convertimos a mayúsculas
    raw_patente = request.form.get('patente', '')
    patente = raw_patente.strip().upper()
    
    # DEBUG: Ver qué llega del formulario
    print(f"--- GUARDANDO VISITA ---")
    print(f"Nombre: {nombre}")
    print(f"Patente recibida (Raw): '{raw_patente}'")
    print(f"Patente a guardar: '{patente}'")
    
    # Si la patente es muy corta (ej: usuario puso un espacio), la dejamos nula
    if len(patente) < 2:
        patente = None

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Actualizamos asegurando que la patente se escriba
    cur.execute("""
        UPDATE invitaciones 
        SET nombre_visita = %s, rut_visita = %s, patente = %s, estado = 'LISTO' 
        WHERE token = %s
    """, (nombre, rut, patente, token))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return render_template('public_qr_exito.html', 
                           token=token, 
                           nombre=nombre, 
                           rut=rut)

# --- VERSIÓN DEFINITIVA Y FORZADA ---
# --- VERSIÓN DEFINITIVA Y FORZADA ---
@app.route('/conserje/visitas/validar_qr', methods=['POST'])
def validar_qr_visita():
    codigo = request.form.get('codigo_qr')
    print(f"\n⚡ VALIDANDO QR: {codigo}") 
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. BUSCAMOS LA INVITACIÓN
    cur.execute("""
        SELECT i.*, u.numero as unidad_numero 
        FROM invitaciones i 
        JOIN unidades u ON i.unidad_id = u.id 
        WHERE i.token = %s
    """, (codigo,))
    inv = cur.fetchone()
    
    if not inv:
        cur.close(); conn.close()
        return jsonify({'status': 'error', 'message': 'QR No existe 🚫'})

    if inv['estado'] == 'USADO':
        cur.close(); conn.close()
        return jsonify({'status': 'error', 'message': 'Este pase YA FUE USADO ⚠️'})

    # 2. ANÁLISIS DE PATENTE
    patente_db = inv.get('patente')
    patente_limpia = str(patente_db if patente_db else '').strip().upper()
    
    # LÓGICA: Si tiene más de 2 caracteres, ES AUTO.
    es_vehiculo = len(patente_limpia) > 2
    
    if es_vehiculo:
        print("   ✅ ES VEHÍCULO -> Buscando parking libres...")
        
        # AQUÍ ESTÁ EL FILTRO CORREGIDO: Buscamos 'libre' O 'LIBRE'
        # Y cruzamos con visitas para asegurarnos que NO esté ocupado realmente
        cur.execute("""
            SELECT e.id, e.nombre 
            FROM estacionamientos_visita e
            LEFT JOIN visitas v ON e.id = v.parking_id AND v.salida IS NULL
            WHERE e.edificio_id = %s 
            AND (e.estado = 'LIBRE' OR e.estado = 'libre')
            AND v.id IS NULL -- Asegura que no haya visita activa ahí
            ORDER BY e.id ASC
        """, (inv['edificio_id'],))
        
        slots_libres = cur.fetchall()
        
        cur.close(); conn.close()
        
        return jsonify({
            'status': 'parking_selection',
            'token_invitacion': codigo,
            'visita': inv['nombre_visita'],
            'patente': patente_limpia,
            'slots': slots_libres # Solo enviará los realmente vacíos
        })
        
    else:
        print("   🚶 ES PEATÓN -> Ingreso directo.")
        cur.execute("""
            INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, ingreso)
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (inv['edificio_id'], inv['unidad_id'], inv['rut_visita'], inv['nombre_visita'], 'PEATON'))
        
        cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],))
        conn.commit()
        cur.close(); conn.close()
        
        return jsonify({
            'status': 'success', 
            'tipo': 'PEATON',
            'visita': inv['nombre_visita'],
            'unidad': inv['unidad_numero']
        })

# EN: app.py (Pégalo al final o reemplaza la ruta existente)

@app.route('/conserje/qr/validar', methods=['POST'])
def conserje_qr_validar():
    token = request.form.get('token')
    
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Buscar Datos de la Invitación
    cur.execute("""
        SELECT i.*, u.numero as unidad, u.edificio_id 
        FROM invitaciones i
        JOIN unidades u ON i.unidad_id = u.id
        WHERE i.token = %s
    """, (token,))
    inv = cur.fetchone()

    if not inv:
        cur.close(); conn.close()
        return jsonify({'status': 'error', 'message': 'Código QR no existe en el sistema.'})

    # --- CANDADO DE SEGURIDAD: SI YA SE USÓ, BLOQUEAR ---
    if inv['estado'] == 'USADO':
        cur.close(); conn.close()
        return jsonify({
            'status': 'error', 
            'message': '⛔ ALERTA DE SEGURIDAD: ESTE QR YA FUE UTILIZADO.'
        })
    # ----------------------------------------------------

    # Si pasa el candado, procedemos
    if inv['tipo'] == 'VEHICULO':
        # Lógica para Autos: Mostrar estacionamientos libres
        cur.execute("""
            SELECT e.id, e.nombre 
            FROM estacionamientos_visita e
            LEFT JOIN visitas v ON e.id = v.parking_id AND v.salida IS NULL
            WHERE e.edificio_id = %s 
            AND (e.estado = 'libre' OR e.estado = 'LIBRE')
            AND v.id IS NULL
            ORDER BY e.id ASC
        """, (inv['edificio_id'],))
        slots = cur.fetchall()
        cur.close(); conn.close()

        return jsonify({
            'status': 'parking_selection',
            'token_invitacion': token,
            'visita': inv['nombre_visita'],
            'patente': inv['patente'] or 'SIN-PATENTE',
            'unidad': inv['unidad'],
            'slots': slots
        })

    else:
        # Lógica para Peatones: Ingreso Directo y QUEMAR TOKEN
        print(f"🚶 INGRESO PEATÓN: {inv['nombre_visita']}")
        
        cur.execute("""
            INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, ingreso)
            VALUES (%s, %s, %s, %s, 'PEATON', NOW())
        """, (inv['edificio_id'], inv['unidad_id'], inv.get('rut_visita',''), inv['nombre_visita']))
        
        # QUEMAR EL TOKEN PARA QUE NO SE USE DE NUEVO
        cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],))
        
        conn.commit()
        cur.close(); conn.close()

        return jsonify({
            'status': 'success',
            'visita': inv['nombre_visita'],
            'unidad': inv['unidad'],
            'tipo': 'PEATON'
        })
    
# EN: app.py

@app.route('/conserje/qr/asignar_parking', methods=['POST'])
def conserje_qr_asignar_parking():
    token = request.form.get('token')
    sid = request.form.get('slot_id')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # 1. Recuperar datos de la invitación usando el token
        cur.execute("SELECT * FROM invitaciones WHERE token = %s", (token,))
        inv = cur.fetchone()

        if not inv:
            return jsonify({'status': 'error', 'message': 'Invitación no válida'})

        # 2. Registrar la Visita (Ocupando el parking)
        cur.execute("""
            INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, parking_id, estacionamiento_id, ingreso)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        """, (inv['edificio_id'], inv['unidad_id'], inv.get('rut_visita',''), inv['nombre_visita'], inv['patente'], sid, sid))

        # 3. Marcar el Estacionamiento como OCUPADO
        cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s", (inv['patente'], sid))

        # ==============================================================================
        # 4. ¡AQUÍ VA LA LÍNEA! QUEMAMOS EL TOKEN PARA QUE NO SE USE MÁS
        # ==============================================================================
        cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE token = %s", (token,))
        
        conn.commit()
        return jsonify({'status': 'success'})

    except Exception as e:
        conn.rollback()
        print(f"Error Asignar QR: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

    finally:
        cur.close()
        conn.close()



# EN: app.py (Pégalo al final, antes del if __name__ == '__main__':)

@app.route('/fix-multas')
def fix_multas_fantasma():
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Asegurar que la columna 'pagada' exista
    try:
        cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS pagada BOOLEAN DEFAULT FALSE")
    except:
        conn.rollback()

    # 2. LOGICA MAESTRA: Si la unidad tiene deuda 0 (o negativa), marcar TODAS sus multas como PAGADAS.
    cur.execute("""
        UPDATE multas 
        SET pagada = TRUE 
        FROM unidades 
        WHERE multas.unidad_id = unidades.id 
        AND unidades.deuda_monto <= 0
    """)
    
    filas = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    
    return f"<h3>✅ LIMPIEZA COMPLETADA: Se arreglaron {filas} multas de unidades sin deuda. <br> <a href='/panel-residente'>Volver al Panel</a></h3>"

    




# ==========================================
# MODO FANTASMA: BÚSQUEDA Y LOGIN (UNIFICADO)
# ==========================================

@app.route('/superadmin/buscar_usuarios_global')
@login_required
def superadmin_buscar_global():
    if current_user.rol != 'superadmin': return jsonify([])
    q = request.args.get('q', '').lower()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # LEFT JOIN es vital para que aparezcan usuarios sin edificio asignado
    cur.execute("""
        SELECT u.rut as id, u.rut, u.nombre, u.rol, COALESCE(e.nombre, 'Sin Edificio') as edificio 
        FROM usuarios u
        LEFT JOIN edificios e ON u.edificio_id = e.id
        WHERE (LOWER(u.nombre) LIKE %s OR LOWER(u.rut) LIKE %s)
        AND u.activo = TRUE
        LIMIT 10
    """, (f'%{q}%', f'%{q}%'))
    
    results = cur.fetchall()
    cur.close(); conn.close()
    
    # Convertimos los resultados a una lista de diccionarios limpia
    return jsonify([dict(r) for r in results])


# ==========================================
# MODO FANTASMA: BÚSQUEDA Y LOGIN (UNIFICADO)
# ==========================================


@app.route('/superadmin/ghost_login/<user_rut>', methods=['POST'])
@login_required
def superadmin_ghost_login(user_rut):
    # Seguridad de acceso
    if current_user.rol not in ['superadmin', 'admin']:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT * FROM usuarios WHERE rut = %s", (user_rut,))
        user_data = cur.fetchone()

        if user_data:
            # Marcamos el origen para poder salir después
            session['god_mode_origin'] = current_user.rut 
            
            # Cargamos los datos en la sesión
            session['user_id'] = user_data['rut']
            session['nombre'] = user_data['nombre']
            session['rol'] = user_data['rol']
            session['edificio_id'] = user_data.get('edificio_id')

            # Si es residente, buscamos su departamento para evitar el error de redirección
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

            # Login oficial en el sistema
            user_obj = Usuario()
            user_obj.rut = user_data['rut']
            user_obj.nombre = user_data['nombre']
            user_obj.rol = user_data['rol']
            user_obj.edificio_id = user_data.get('edificio_id')
            login_user(user_obj)

            flash(f"Modo Fantasma: {user_data['nombre']}", "info")
            
            # Redirección inteligente según el rol suplantado
            if user_data['rol'] == 'residente': return redirect(url_for('panel_residente'))
            if user_data['rol'] == 'admin': return redirect(url_for('panel_admin'))
            if user_data['rol'] == 'conserje': return redirect(url_for('panel_conserje'))

    except Exception as e:
        print(f"Error Ghost Login: {e}")
        flash("Error al procesar el acceso fantasma", "error")
    finally:
        cur.close(); conn.close()
    
    return redirect(url_for('panel_superadmin'))

@app.route('/superadmin/exit_ghost')
@login_required
def superadmin_exit_ghost():
    # 1. Recuperamos el RUT original del Superadmin guardado en la sesión
    origin_rut = session.get('god_mode_origin')
    
    if not origin_rut:
        flash("No se detectó una sesión de origen válida.", "error")
        return redirect(url_for('home'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 2. Buscamos los datos reales del Superadmin en la base de datos
        cur.execute("SELECT * FROM usuarios WHERE rut = %s", (origin_rut,))
        god_user = cur.fetchone()
        
        if god_user:
            # 3. Limpiamos TODA la sesión actual para eliminar rastros del usuario suplantado
            session.clear()
            
            # 4. Restauramos las variables de sesión del Superadmin
            session['user_id'] = god_user['rut']
            session['nombre'] = god_user['nombre']
            session['rol'] = 'superadmin'
            session['edificio_id'] = None # El Superadmin no pertenece a un edificio fijo
            
            # 5. RE-AUTENTICACIÓN OFICIAL: Le avisamos a Flask-Login quién manda
            god_obj = Usuario()
            god_obj.rut = god_user['rut']
            god_obj.nombre = god_user['nombre']
            god_obj.rol = 'superadmin'
            login_user(god_obj) # <--- ESTO ROMPE EL BUCLE DE REDIRECCIONES
            
            flash(f"Saliendo del Modo Fantasma. Bienvenido de vuelta, {god_user['nombre']}", "info")
            return redirect(url_for('panel_superadmin')) # Redirección limpia al panel central
        
    except Exception as e:
        print(f"Error al salir del modo fantasma: {e}")
        flash("Error crítico al restaurar la sesión.", "error")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('login'))
# --- RUTAS SUPERADMIN: GESTIÓN DE EDIFICIOS ---

# Busca esta ruta y reemplázala completa:
@app.route('/superadmin/crear_edificio', methods=['POST'])
@login_required
def super_crear_edificio():
    if current_user.rol != 'superadmin':
        return redirect(url_for('login'))
        
    # 1. Capturar datos del formulario
    nombre = request.form.get('nombre')
    direccion = request.form.get('direccion')
    lat = request.form.get('lat')
    lon = request.form.get('lon')
    
    # 2. Limpieza de datos (evitar que "" rompa el SQL)
    try:
        lat_val = float(lat) if lat and lat.strip() else None
        lon_val = float(lon) if lon and lon.strip() else None
    except ValueError:
        lat_val = None
        lon_val = None

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 3. Insertar con SQL Directo (Asegurando coincidencia con la tabla)
        cur.execute("""
            INSERT INTO edificios (nombre, direccion, latitud, longitud, activo, deuda_omnisoft, estado_pago)
            VALUES (%s, %s, %s, %s, TRUE, 0, 'PENDIENTE')
        """, (nombre, direccion, lat_val, lon_val))
        
        conn.commit()
        flash(f'¡Éxito! El edificio "{nombre}" ya está en el sistema.', 'success')
        
    except Exception as e:
        conn.rollback()
        print(f"🔥 Error SQL Crear Edificio: {e}")
        flash(f'Error al crear el edificio en la base de datos.', 'error')
        
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('panel_superadmin'))

@app.route('/superadmin/eliminar_edificio', methods=['POST'])
@login_required
def super_eliminar_edificio():
    if current_user.rol != 'superadmin':
        return redirect(url_for('login'))
        
    edificio_id = request.form.get('edificio_id')
    edificio = Edificio.query.get(edificio_id)
    
    if edificio:
        # OPCIÓN A: Borrado Lógico (Recomendado para no romper historiales)
        edificio.activo = False 
        flash(f'Edificio {edificio.nombre} desactivado y archivado.', 'warning')
        
        # OPCIÓN B: Borrado Físico (Descomentar si prefieres borrarlo de verdad)
        # db.session.delete(edificio)
        # flash('Edificio eliminado permanentemente.', 'error')
        
        db.session.commit()
    
    return redirect(url_for('super_dashboard'))



# --- RUTA DE DETALLE ---
@app.route('/superadmin/detalle_edificio/<int:id>')
@login_required
def super_detalle_edificio(id):
    if current_user.rol != 'superadmin': return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. Traer Edificio
    cur.execute("SELECT * FROM edificios WHERE id = %s", (id,))
    edificio = cur.fetchone()
    
    if not edificio:
        cur.close(); conn.close()
        return "Edificio no encontrado", 404

    # 2. Traer Admins
    cur.execute("SELECT * FROM usuarios WHERE edificio_id = %s AND rol = 'admin' AND activo = TRUE", (id,))
    admins = cur.fetchall()
    
    # 3. Traer Unidades y procesar JSONs (Owner/Tenant)
    cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (id,))
    unidades_raw = cur.fetchall()
    
    unidades_procesadas = []
    for u in unidades_raw:
        # Usamos tu función parse_json_field que ya tienes en app.py
        u['owner'] = parse_json_field(u.get('owner_json'))
        u['tenant'] = parse_json_field(u.get('tenant_json'))
        unidades_procesadas.append(u)

    cur.close()
    conn.close()

    # 4. Renderizar pasando INDICADORES y nombre de archivo correcto
    return render_template('super_detalle_edificio.html', 
                           e=edificio, 
                           admins=admins, 
                           unidades=unidades_procesadas,
                           indicadores=obtener_indicadores()) # <--- ESTO ES LO QUE FALTABA
if __name__ == '__main__':
    app.run(debug=True, port=5004)
```

## database.py
```python
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

# --- CONFIGURACIÓN NEON (POSTGRESQL) ---
DB_URI = os.environ.get('DB_URI')

def get_db_connection():
    try:
        conn = psycopg2.connect(DB_URI, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"❌ Error crítico conectando a la BD: {e}")
        return None

def inicializar_tablas():
    conn = get_db_connection()
    if not conn:
        return
    
    cur = None
    try:
        cur = conn.cursor()
        print("🔄 Verificando estructura de la Base de Datos...")

        # 1. USUARIOS (Staff: Admin, Conserjes, Superadmin)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                rut VARCHAR(20) PRIMARY KEY,
                nombre VARCHAR(100),
                email VARCHAR(100),
                telefono VARCHAR(20),
                password VARCHAR(100),
                rol VARCHAR(20),
                edificio_id INT,
                activo BOOLEAN DEFAULT TRUE
            );
        """)

        # 2. EDIFICIOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS edificios (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100),
                direccion VARCHAR(200),
                lat FLOAT,
                lon FLOAT,
                deuda_omnisoft INT DEFAULT 0,
                estado_pago VARCHAR(20) DEFAULT 'PENDIENTE',
                activo BOOLEAN DEFAULT TRUE,
                deuda_descripcion TEXT,
                deuda_vencimiento DATE,
                deuda_comprobante_url TEXT
            );
        """)

        # 3. UNIDADES (Residentes)
        # Nota: Incluimos 'password' aquí para nuevas instalaciones
        cur.execute("""
            CREATE TABLE IF NOT EXISTS unidades (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                numero VARCHAR(10),
                piso INT,
                metraje INT,
                prorrateo FLOAT,
                estacionamiento VARCHAR(20),
                bodega VARCHAR(20),
                owner_json TEXT,
                tenant_json TEXT,
                broker_json TEXT,
                deuda_monto INT DEFAULT 0,
                estado_deuda VARCHAR(20) DEFAULT 'AL_DIA',
                password VARCHAR(50) DEFAULT '1234'
            );
        """)

        # 4. GASTOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gastos (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                categoria VARCHAR(50),
                descripcion TEXT,
                monto INT,
                fecha DATE,
                mes INT,
                anio INT,
                comprobante_url TEXT,
                cerrado BOOLEAN DEFAULT FALSE
            );
        """)

        # 5. HISTORIAL PAGOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS historial_pagos (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                unidad_id INT,
                monto INT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metodo VARCHAR(50),
                comprobante_url TEXT,
                mes_periodo INT,
                anio_periodo INT
            );
        """)

        # 6. MULTAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS multas (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                unidad_id INT,
                monto INT,
                motivo TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                pagada BOOLEAN DEFAULT FALSE
            );
        """)

        # 7. ENCOMIENDAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS encomiendas (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                unidad_id INT,
                remitente VARCHAR(100),
                recepcion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                entrega TIMESTAMP
            );
        """)

        # 8. VISITAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS visitas (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                unidad_id INT,
                rut VARCHAR(20),
                nombre_visita VARCHAR(100),
                patente VARCHAR(20),
                estacionamiento_id VARCHAR(10),
                parking_id INT,
                ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                salida TIMESTAMP,
                egreso TIMESTAMP
            );
        """)

        # 9. INCIDENCIAS (BITÁCORA)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS incidencias (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                titulo VARCHAR(100),
                descripcion TEXT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                autor VARCHAR(100)
            );
        """)

        # 10. ACTIVOS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS activos (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                nombre VARCHAR(100),
                periodicidad_dias INT,
                costo_estimado INT,
                ultimo_servicio DATE
            );
        """)

        # 11. CIERRES DE MES
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cierres_mes (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                mes INT,
                anio INT,
                total_gastos INT,
                admin_responsable VARCHAR(100),
                fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 12. ESTACIONAMIENTOS VISITA (PARKING)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS estacionamientos_visita (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                nombre VARCHAR(50),
                estado VARCHAR(20) DEFAULT 'LIBRE' 
            );
        """)
        # Alias para compatibilidad con código antiguo que busque la tabla 'parking'
        try:
            cur.execute("CREATE VIEW parking AS SELECT * FROM estacionamientos_visita;")
            conn.commit()
        except: conn.rollback()

        # 13. INVITACIONES (QR)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS invitaciones (
                id SERIAL PRIMARY KEY,
                token VARCHAR(64) UNIQUE NOT NULL,
                edificio_id INT,
                unidad_id INT,
                nombre_visita VARCHAR(100),
                rut_visita VARCHAR(20),
                patente VARCHAR(20),
                tipo VARCHAR(20) DEFAULT 'PEATON',
                pre_nombre VARCHAR(100),
                estado VARCHAR(20) DEFAULT 'PENDIENTE',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_uso TIMESTAMP
            );
        """)

        # 14. ESPACIOS COMUNES (AMENITIES)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS espacios (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                nombre VARCHAR(100),
                capacidad INT,
                precio INT DEFAULT 0,
                foto_url TEXT,
                activo BOOLEAN DEFAULT TRUE
            );
        """)

        # 15. RESERVAS
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservas (
                id SERIAL PRIMARY KEY,
                espacio_id INT,
                unidad_id INT,
                fecha_uso DATE,
                hora_inicio VARCHAR(10),
                estado VARCHAR(20) DEFAULT 'CONFIRMADA', 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # ==========================================
        # ZONA DE PARCHES (ALTER TABLES)
        # Para corregir bases de datos ya creadas
        # ==========================================

        # Parche 1: Contraseña en Unidades
        try:
            cur.execute("ALTER TABLE unidades ADD COLUMN IF NOT EXISTS password VARCHAR(50) DEFAULT '1234';")
            conn.commit()
            print("✅ Parche: Columna 'password' verificada en Unidades.")
        except: conn.rollback()

        # Parche 2: Hora Inicio en Reservas
        try:
            cur.execute("ALTER TABLE reservas ADD COLUMN IF NOT EXISTS hora_inicio VARCHAR(10);")
            conn.commit()
            print("✅ Parche: Columna 'hora_inicio' verificada en Reservas.")
        except: conn.rollback()

        # Parche 3: Tipo y Nombre en Invitaciones
        try:
            cur.execute("ALTER TABLE invitaciones ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'PEATON';")
            cur.execute("ALTER TABLE invitaciones ADD COLUMN IF NOT EXISTS pre_nombre VARCHAR(100);")
            conn.commit()
        except: conn.rollback()

        # Parche 4: Multas Pagadas
        try:
            cur.execute("ALTER TABLE multas ADD COLUMN IF NOT EXISTS pagada BOOLEAN DEFAULT FALSE;")
            conn.commit()
        except: conn.rollback()

        # Parche 5: Visitas (Parking ID integer)
        try:
            cur.execute("ALTER TABLE visitas ADD COLUMN IF NOT EXISTS parking_id INT;")
            cur.execute("ALTER TABLE visitas ADD COLUMN IF NOT EXISTS egreso TIMESTAMP;")
            conn.commit()
        except: conn.rollback()

        # Parche 6: Seguridad Roles Check
        try:
            cur.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_rol_check;")
            conn.commit()
        except: conn.rollback()

        print("✅ Base de datos inicializada y actualizada correctamente.")

        try:
            cur.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_rol_check;")
            conn.commit()
        except: conn.rollback()

        # --- AGREGA ESTO AL FINAL DE LOS PARCHES ---
        # Parche 7: Agregar columna patente a la tabla de estacionamientos
        try:
            cur.execute("ALTER TABLE estacionamientos_visita ADD COLUMN IF NOT EXISTS patente VARCHAR(20);")
            conn.commit()
            print("✅ Parche: Columna 'patente' agregada a Parking.")
        except Exception as e:
            conn.rollback()
            print(f"Info (Parking Patch): {e}")
        # -------------------------------------------

        print("✅ Base de datos inicializada y actualizada correctamente.")
        
    except Exception as e:

        
   
        print(f"❌ Error en inicializar_tablas: {e}")
        if conn: conn.rollback()
    finally:
        if cur: cur.close()
        if conn: conn.close()

if __name__ == "__main__":
    inicializar_tablas()
```

## setup.py
```python
from database import inicializar_tablas

if __name__ == "__main__":
    print("⚠️ INICIANDO CONFIGURACIÓN DE BASE DE DATOS NEON...")
    inicializar_tablas()
    print("🚀 PROCESO FINALIZADO. AHORA PUEDES EJECUTAR APP.PY")
```

## generar_datos.py
```python
import json
import random

def generar_mock_data():
    print("🏭 Generando 150 Departamentos con datos detallados...")
    
    unidades = []
    apellidos = ["Silva", "Gomez", "Perez", "Gonzalez", "Muñoz", "Rojas", "Diaz", "Vasquez", "Castro"]
    nombres = ["Ana", "Carlos", "Roberto", "Maria", "Jose", "Luis", "Elena", "Sofia", "Miguel"]
    corredoras = ["Propiedades Pro", "Gestión Inmobiliaria", "Corredora Santiago", "Tu Casa OK"]

    # Generar 15 Pisos
    for piso in range(1, 16):
        # 10 Departamentos por piso
        for d in range(1, 11):
            numero_depto = f"{piso}{d:02d}" # Ej: 101, 102... 1510
            
            # Datos Aleatorios
            es_arrendado = random.choice([True, False])
            tiene_corredora = es_arrendado and random.choice([True, False])
            
            metraje = random.choice([45.5, 60.0, 85.5, 120.0])
            prorrateo = round(metraje * 0.015, 3) # Cálculo simple de prorrateo
            
            # Generar Dueño
            nom_owner = f"{random.choice(nombres)} {random.choice(apellidos)}"
            rut_owner = f"{random.randint(10,25)}.{random.randint(100,999)}.{random.randint(100,999)}-{random.randint(0,9)}"
            
            owner_data = {
                "rut": rut_owner,
                "nombre": nom_owner,
                "email": f"{nom_owner.split()[0].lower()}@mail.com",
                "fono": f"+569{random.randint(10000000, 99999999)}"
            }

            # Generar Residente (Si es arrendado es otro, si no, es el dueño)
            if es_arrendado:
                nom_tenant = f"{random.choice(nombres)} {random.choice(apellidos)}"
                rut_tenant = f"{random.randint(15,30)}.{random.randint(100,999)}.{random.randint(100,999)}-{random.randint(0,9)}"
                tenant_data = {
                    "rut": rut_tenant,
                    "nombre": nom_tenant,
                    "email": f"{nom_tenant.split()[0].lower()}@live.cl",
                    "fono": f"+569{random.randint(10000000, 99999999)}"
                }
            else:
                tenant_data = owner_data # El dueño vive ahí

            # Generar Corredora
            if tiene_corredora:
                broker_data = {
                    "rut": "77.000.000-K",
                    "nombre": random.choice(corredoras),
                    "email": "contacto@corredora.cl",
                    "fono": "+56222222222"
                }
            else:
                broker_data = {"rut": "", "nombre": "No aplica", "email": "", "fono": ""}

            unidad = {
                "numero": numero_depto,
                "piso": piso,
                "metraje": metraje,
                "prorrateo": prorrateo,
                "estacionamiento": f"E-{random.randint(1,200)}",
                "bodega": f"B-{random.randint(1,150)}",
                "owner": owner_data,
                "tenant": tenant_data,
                "broker": broker_data
            }
            
            unidades.append(unidad)

    # Guardar en archivo JSON
    with open('carga_masiva.json', 'w', encoding='utf-8') as f:
        json.dump(unidades, f, indent=4, ensure_ascii=False)
        
    print(f"✅ Archivo 'carga_masiva.json' creado con {len(unidades)} unidades.")

if __name__ == "__main__":
    generar_mock_data()
```

## templates/base.html
```html
<!DOCTYPE html>
<html lang="es" data-bs-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>HabitaPro {% block title %}{% endblock %}</title>
    
    <link rel="icon" type="image/svg+xml" href="https://icons.getbootstrap.com/assets/icons/buildings-fill.svg">
    <link rel="apple-touch-icon" href="https://icons.getbootstrap.com/assets/icons/buildings-fill.svg">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
    
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>
    <style>
        /* =========================================
           ESTILO MAESTRO: CYBER-GLASS DARK
           ========================================= */
        
        :root {
            --neon-blue: #0dcaf0;
            --neon-purple: #6f42c1;
            --glass-bg: rgba(20, 20, 30, 0.65);
            --glass-border: rgba(255, 255, 255, 0.1);
        }

        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #050505;
            /* Fondo Degradado Aurora (Estilo Invitación) */
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(60, 10, 80, 0.25) 0%, transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(10, 50, 80, 0.25) 0%, transparent 40%);
            background-attachment: fixed;
            background-size: cover;
            color: #ffffff;
            min-height: 100vh;
        }

        /* --- TARJETAS DE VIDRIO (GLOBAL) --- */
        .card, .modal-content, .list-group-item, .card-glass, .card-app, .card-dark-solid {
            background: var(--glass-bg) !important;
            backdrop-filter: blur(25px) !important;
            -webkit-backdrop-filter: blur(25px) !important;
            border: 1px solid var(--glass-border) !important;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4) !important;
            border-radius: 20px !important;
            color: white !important;
        }

        /* --- INPUTS & FORMS --- */
        .form-control, .form-select, input, select, textarea {
            background-color: rgba(0, 0, 0, 0.4) !important;
            border: 1px solid rgba(255, 255, 255, 0.15) !important;
            color: #ffffff !important;
            border-radius: 12px !important;
            padding: 12px 15px;
        }
        .form-control:focus, .form-select:focus {
            border-color: var(--neon-blue) !important;
            box-shadow: 0 0 0 3px rgba(13, 202, 240, 0.15) !important;
            background-color: rgba(0, 0, 0, 0.6) !important;
        }

        /* --- BOTONES NEÓN --- */
        .btn-primary, .btn-info, .btn-success, .btn-warning, .btn-danger {
            border: none !important;
            font-weight: 700 !important;
            border-radius: 12px !important;
            padding: 10px 20px !important;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            transition: all 0.3s ease;
        }
        
        .btn-primary { background: linear-gradient(135deg, #0dcaf0, #0d6efd) !important; color: white !important; }
        .btn-success { background: linear-gradient(135deg, #2ecc71, #27ae60) !important; color: white !important; }
        .btn-danger  { background: linear-gradient(135deg, #e74c3c, #c0392b) !important; color: white !important; }
        .btn-warning { background: linear-gradient(135deg, #f1c40f, #f39c12) !important; color: black !important; }

        .btn-outline-light, .btn-secondary, .btn-glass {
            background: rgba(255,255,255,0.05) !important;
            border: 1px solid rgba(255,255,255,0.2) !important;
            color: white !important;
            backdrop-filter: blur(5px);
        }

        /* --- NAVBAR --- */
        .navbar-glass {
            background: rgba(5, 5, 10, 0.85) !important;
            backdrop-filter: blur(20px);
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }

        /* --- TABLAS --- */
        .table { --bs-table-bg: transparent; color: white !important; }
        .table td, .table th { border-bottom: 1px solid rgba(255,255,255,0.1); padding: 15px 10px; vertical-align: middle; }
        .table-hover tbody tr:hover td { background-color: rgba(255, 255, 255, 0.05); }

        /* --- UTILIDADES --- */
        .text-muted { color: rgba(255,255,255,0.5) !important; }
        .badge { padding: 8px 12px; border-radius: 8px; font-weight: 600; }
        
        /* SweetAlert al estilo Glass */
        div:where(.swal2-container) div:where(.swal2-popup) {
            background: #151520 !important;
            border: 1px solid rgba(255,255,255,0.1);
        }
        .swal2-title, .swal2-html-container { color: white !important; }
    </style>
    {% block styles %}{% endblock %}
</head>
<body>
    {% block content %}{% endblock %}
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        setTimeout(() => {
            document.querySelectorAll('.alert').forEach(a => new bootstrap.Alert(a).close());
        }, 4000);
    </script>
    {% if session.get('god_mode_origin') %}
<a href="/superadmin/exit_ghost" 
   style="position: fixed; bottom: 20px; right: 20px; z-index: 99999; 
          background: linear-gradient(45deg, #d63384, #dc3545); 
          color: white; padding: 15px 25px; border-radius: 50px; 
          font-weight: bold; text-decoration: none; 
          box-shadow: 0 0 30px rgba(220, 53, 69, 0.6); 
          border: 2px solid white; animation: pulse 2s infinite;">
    <i class="bi bi-eye-slash-fill me-2"></i> SALIR MODO FANTASMA
</a>

<style>
@keyframes pulse {
    0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(220, 53, 69, 0.7); }
    70% { transform: scale(1.05); box-shadow: 0 0 0 15px rgba(220, 53, 69, 0); }
    100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(220, 53, 69, 0); }
}
</style>
{% endif %}

</body>
</html>
```

## templates/login.html
```html
{% extends "base.html" %}
{% block title %}| Bienvenido{% endblock %}

{% block content %}
<div class="d-flex align-items-center justify-content-center" style="min-height: 80vh;">
    <div class="card card-glass p-4" style="max-width: 400px; width: 100%;">
        <div class="text-center mb-4">
            <i class="bi bi-buildings-fill text-primary" style="font-size: 3rem;"></i>
            <h3 class="fw-bold text-white mt-2">HabitaPro</h3>
            <p class="text-white-50">Acceso a Residentes y Staff</p>
        </div>

        {% with messages = get_flashed_messages() %}
            {% if messages %}
                {% for msg in messages %}
                    <div class="alert alert-danger text-center border-0 small py-2 mb-3">
                        <i class="bi bi-exclamation-circle me-1"></i> {{ msg }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}

        <form action="/login" method="POST">
            <div class="mb-3">
                <label class="form-label text-white-50 small">Usuario</label>
                <input type="text" name="email" class="form-control" placeholder="RUT (12345678-9) o Depto" required>
            </div>
            
            <div class="mb-4">
                <label class="form-label text-white-50 small">Contraseña</label>
                <input type="password" name="password" class="form-control" placeholder="••••" required>
            </div>

            <button type="submit" class="btn btn-primary w-100 fw-bold py-2">
                INGRESAR <i class="bi bi-arrow-right-short"></i>
            </button>
        </form>
        
        <div class="text-center mt-4">
            <small class="text-white-50">¿Olvidaste tu clave? Pídela en conserjería.</small>
        </div>
    </div>
</div>
{% endblock %}
```

## templates/dash_admin.html
```html
{% extends "base.html" %}
{% block title %}| Mando Admin{% endblock %}

{% block styles %}
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
    /* ESTILOS DARK GENERALES */
    .card, .modal-content, .card-glass, .list-group-item {
        background-color: #1a1d24 !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
        color: #ffffff !important;
        border-radius: 16px !important;
    }
    h1, h2, h3, h4, h5, h6, strong, span, p, div { color: #ffffff !important; }
    .text-white-50, .small { color: rgba(255, 255, 255, 0.6) !important; }

    .form-control, .form-select {
        background-color: rgba(0, 0, 0, 0.3) !important;
        border: 1px solid rgba(255, 255, 255, 0.2) !important;
        color: white !important;
    }
    .form-control:focus { border-color: #0dcaf0 !important; box-shadow: 0 0 0 3px rgba(13, 202, 240, 0.25) !important; }

    /* PARKING & MAPA */
    .hero-management { position: relative; min-height: 350px; border-radius: 20px; overflow: hidden; margin-bottom: 30px; border: 1px solid rgba(255,255,255,0.2); }
    #miniMap { width: 100%; height: 100%; position: absolute; top: 0; left: 0; filter: grayscale(100%) invert(100%) opacity(0.3); z-index: 0; }
    .hero-content { position: relative; z-index: 2; padding: 40px; display: flex; flex-direction: column; justify-content: center; background: linear-gradient(90deg, rgba(20,25,35,0.95) 40%, rgba(20,25,35,0.4) 100%); height: 100%; }
    
    .parking-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 12px; }
    .park-slot { height: 100px; border-radius: 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.2); display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 0.85rem; position: relative; transition: 0.2s; }
    .park-ocupado { border-color: #f1c40f !important; background: rgba(241, 196, 15, 0.15) !important; }
    .park-ocupado .park-icon { color: #f1c40f; }
    .park-libre { border-color: #2ecc71 !important; }
    .park-libre .park-icon { color: #2ecc71; }
    .park-mantencion { border-color: #0dcaf0 !important; background: rgba(13, 202, 240, 0.1) !important; opacity: 0.8; }
    .park-mantencion .park-icon { color: #0dcaf0; }
    .btn-maint { position: absolute; top: 2px; right: 2px; font-size: 0.8rem; color: rgba(255,255,255,0.3); cursor: pointer; background: none; border: none; }

    /* BOTONES ACCIONES */
    .btn-action-group .btn { padding: 4px 8px; font-size: 0.85rem; }
    .cal-day { height: 45px; border-radius: 8px; background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.1); display: flex; align-items: center; justify-content: center; cursor: default; }
    .cal-active { background: #0dcaf0 !important; color: #000 !important; font-weight: 800; cursor: pointer !important; }
    .cal-badge { position: absolute; top: -5px; right: -5px; width: 10px; height: 10px; background: #e74c3c; border-radius: 50%; }
    
    .btn-quick { height: 110px; border-radius: 20px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.2); display: flex; flex-direction: column; align-items: center; justify-content: center; text-decoration: none; color: white; transition: 0.3s; }
    .btn-quick:hover { background: rgba(255,255,255,0.15); border-color: #0dcaf0; transform: translateY(-5px); }
    .btn-quick i { font-size: 2.5rem; margin-bottom: 10px; }
</style>
{% endblock %}

{% block content %}
<nav class="navbar navbar-glass fixed-top">
    <div class="container-fluid px-4">
        <a class="navbar-brand fw-bold text-white" href="#"><i class="bi bi-building me-2 text-info"></i>Admin Panel</a>
        <div class="d-flex align-items-center gap-3">
            <span class="text-white small fw-bold">{{ session.nombre }}</span>
            <a href="/logout" class="btn btn-sm btn-danger border-0 shadow"><i class="bi bi-power"></i></a>
        </div>
    </div>
</nav>
<div class="bg-black border-bottom border-secondary py-1" style="margin-top: 56px;">
    <div class="container-fluid d-flex justify-content-end gap-4 text-white-50" style="font-size: 0.75rem;">
        <div class="d-flex align-items-center">
            <i class="bi bi-graph-up-arrow text-warning me-1"></i> 
            UF: <span class="text-white fw-bold ms-1">${{ "{:,.2f}".format(indicadores.uf) }}</span>
        </div>
        <div class="d-flex align-items-center">
            <i class="bi bi-bank text-success me-1"></i> 
            Dólar: <span class="text-white fw-bold ms-1">${{ "{:,.2f}".format(indicadores.dolar) }}</span>
        </div>
        <div class="d-flex align-items-center d-none d-md-flex">
            <i class="bi bi-file-earmark-text text-info me-1"></i> 
            UTM: <span class="text-white fw-bold ms-1">${{ "{:,.0f}".format(indicadores.utm) }}</span>
        </div>
    </div>
</div>

<div class="container px-4" style="margin-top: 100px; padding-bottom: 50px;">
    {% with messages = get_flashed_messages() %}{% if messages %}{% for msg in messages %}<div class="alert alert-info border-0 text-center mb-4 fw-bold shadow">{{ msg }}</div>{% endfor %}{% endif %}{% endwith %}

    <div class="row g-4 mb-4">
        <div class="col-lg-6">
            <div class="hero-management card p-0 border-0 h-100">
                <div id="miniMap"></div>
                <div class="hero-content">
                    <span class="badge bg-success w-auto align-self-start mb-2 shadow">SISTEMA ONLINE</span>
                    <h1 class="fw-bold mb-0">{{ edificio.nombre }}</h1>
                    <p class="text-white-50 fs-5 mb-2">{{ edificio.direccion }}</p>
                    
                    {% if edificio.deuda_omnisoft > 0 and edificio.estado_pago != 'PAGADO' %}
                        <div class="mt-3 p-3 rounded border" style="background: rgba(220, 53, 69, 0.15); border-color: #dc3545 !important; backdrop-filter: blur(5px);">
                            <div class="d-flex justify-content-between align-items-start">
                                <div>
                                    <div class="text-danger fw-bold text-uppercase mb-1"><i class="bi bi-exclamation-circle-fill me-2"></i>Pago Pendiente</div>
                                    <h5 class="text-white m-0">{{ edificio.deuda_descripcion }}</h5>
                                    <div class="fs-4 fw-bold text-white mt-1">${{ "{:,.0f}".format(edificio.deuda_omnisoft) }}</div>
                                </div>
                                <div class="text-end">
                                    {% if alerta_deuda == 'VENCIDO' %}
                                        <div class="badge bg-danger fs-6 mb-2">¡VENCIDO!</div>
                                        <div class="text-white small">Hace {{ dias_restantes|abs }} días</div>
                                    {% else %}
                                        <div class="text-white-50 small text-uppercase">Vence en</div>
                                        <div class="display-6 fw-bold {{ 'text-warning' if dias_restantes <= 3 else 'text-success' }}">{{ dias_restantes }}</div>
                                        <div class="text-white-50 small">Días</div>
                                    {% endif %}
                                </div>
                            </div>
                            <div class="mt-3 border-top border-danger border-opacity-25 pt-2">
                                {% if edificio.estado_pago == 'REVISION' %}
                                    <div class="text-warning fw-bold small"><i class="bi bi-hourglass-split"></i> Comprobante enviado. Esperando confirmación...</div>
                                {% else %}
                                    <button class="btn btn-sm btn-danger w-100 fw-bold shadow" data-bs-toggle="modal" data-bs-target="#modalPagarServicio"><i class="bi bi-upload me-2"></i> SUBIR COMPROBANTE PAGO</button>
                                {% endif %}
                            </div>
                        </div>
                    {% else %}
                        <div class="mt-4 d-flex gap-5">
                            <div><div class="display-6 fw-bold">{{ stats.unidades }}</div><small class="text-white-50 text-uppercase fw-bold">Unidades</small></div>
                            <div><div class="display-6 fw-bold text-success">${{ "{:,.0f}".format(finanzas.saldo) }}</div><small class="text-white-50 text-uppercase fw-bold">Caja Chica</small></div>
                        </div>
                    {% endif %}
                </div>
            </div>
        </div>

        <div class="col-lg-6">
            <div class="card p-4 h-100">
                <div class="d-flex justify-content-between align-items-center mb-4">
                    <div class="d-flex align-items-center gap-3">
                        <a href="?month={{ nav.prev_m }}&year={{ nav.prev_y }}" class="btn btn-sm btn-outline-light"><i class="bi bi-chevron-left"></i></a>
                        <h4 class="fw-bold m-0">{{ mes_actual }} {{ anio_actual }}</h4>
                        <a href="?month={{ nav.next_m }}&year={{ nav.next_y }}" class="btn btn-sm btn-outline-light"><i class="bi bi-chevron-right"></i></a>
                    </div>
                    <a href="/admin/activos" class="btn btn-sm btn-info fw-bold text-dark"><i class="bi bi-gear-fill me-1"></i> Activos</a>
                </div>
                <div class="d-flex flex-wrap gap-2 justify-content-center">
                    {% for semana in calendario %}
                        {% for dia in semana %}
                            {% if dia > 0 %}
                                {% set count = eventos.get(dia, [])|length %}
                                <div class="cal-day {{ 'cal-active' if count > 0 }}" style="width: 13%;" {% if count > 0 %} onclick="verDetalleDia({{ dia }}, {{ eventos.get(dia, []) | tojson }})" {% endif %}>
                                    {{ dia }} {% if count > 0 %}<div class="cal-badge"></div>{% endif %}
                                </div>
                            {% else %}<div style="width: 13%;"></div>{% endif %}
                        {% endfor %}
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>

    <div class="row g-4 mb-4">
        <div class="col-lg-4">
            <div class="card p-4 h-100">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h5 class="fw-bold m-0"><i class="bi bi-stars text-warning me-2"></i> Amenities</h5>
                    <button class="btn btn-sm btn-primary" type="button" data-bs-toggle="collapse" data-bs-target="#formEspacio"><i class="bi bi-plus-lg"></i></button>
                </div>
                <div class="collapse mb-3" id="formEspacio">
                    <form action="/admin/espacios/guardar" method="POST" class="bg-black bg-opacity-25 p-3 rounded border border-secondary">
                        <input type="text" name="nombre" class="form-control mb-2" placeholder="Nombre (Ej: Quincho)" required>
                        <div class="row g-2 mb-2">
                            <div class="col"><input type="number" name="capacidad" class="form-control" placeholder="Cap." required></div>
                            <div class="col"><input type="number" name="precio" class="form-control" placeholder="$" value="0"></div>
                        </div>
                        <button class="btn btn-success w-100 btn-sm fw-bold">Guardar</button>
                    </form>
                </div>
                <div class="list-group list-group-flush">
                    {% for e in espacios %}
                    <div class="list-group-item d-flex justify-content-between align-items-center bg-transparent px-0 border-bottom border-white border-opacity-10">
                        <div>
                            <div class="fw-bold">{{ e.nombre }}</div>
                            <small class="text-white-50">Cap: {{ e.capacidad }} | <span class="text-success">${{ "{:,.0f}".format(e.precio) }}</span></small>
                        </div>
                        <a href="/admin/espacios/eliminar/{{ e.id }}" class="btn btn-sm btn-outline-danger border-0" onclick="return confirm('¿Eliminar?')"><i class="bi bi-trash-fill"></i></a>
                    </div>
                    {% else %}
                    <div class="text-center text-white-50 py-3">Sin espacios creados.</div>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="col-lg-8">
            <div class="card p-0 h-100 overflow-hidden">
                <div class="p-4 border-bottom border-white border-opacity-10 bg-black bg-opacity-25">
                    <h5 class="m-0 fw-bold"><i class="bi bi-calendar-check text-info me-2"></i> Reservas Recientes</h5>
                </div>
                <div class="table-responsive">
                    <table class="table table-hover mb-0 align-middle">
                        <thead><tr class="text-white-50 small"><th>Fecha / Hora</th><th>Espacio</th><th>Unidad</th><th>Estado</th><th>Acción</th></tr></thead>
                        <tbody>
                            {% for r in reservas %}
                            <tr>
                                <td class="fw-bold">
                                    {{ r.fecha_uso }} 
                                    <span class="text-info ms-1">{{ r.hora_inicio or '' }}</span>
                                </td>
                                <td class="text-info fw-bold">{{ r.nombre_espacio }}</td>
                                <td><span class="badge bg-light text-dark border border-white">U. {{ r.numero_unidad }}</span></td>
                                <td>
                                    {% if r.estado == 'CONFIRMADA' %}<span class="badge bg-success shadow-sm">OK</span>
                                    {% else %}<span class="badge bg-danger shadow-sm">CANCEL</span>{% endif %}
                                </td>
                                <td>
                                    {% if r.estado == 'CONFIRMADA' %}
                                    <form action="/admin/reservas/cancelar" method="POST" class="d-inline" onsubmit="return confirm('¿Cancelar y devolver dinero?');">
                                        <input type="hidden" name="reserva_id" value="{{ r.id }}">
                                        <input type="hidden" name="reembolsar" value="on">
                                        <button class="btn btn-sm btn-danger py-0 fw-bold" title="Cancelar"><i class="bi bi-x-lg"></i></button>
                                    </form>
                                    {% endif %}
                                </td>
                            </tr>
                            {% else %}
                            <tr><td colspan="5" class="text-center py-5 text-white-50">Sin reservas activas.</td></tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="card p-4 mb-4">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h5 class="fw-bold m-0">🅿️ Estacionamientos Visita</h5>
            <button class="btn btn-sm btn-outline-info" data-bs-toggle="modal" data-bs-target="#modalConfigParking">
                <i class="bi bi-gear-fill me-1"></i> Configurar
            </button>
        </div>
    <div class="parking-grid">
            {% for p in parking %}
            <div class="park-slot park-{{ p.estado }}">
                <form action="/admin/parking/maintenance" method="POST">
                    <input type="hidden" name="slot_id" value="{{ p.id }}">
                    <input type="hidden" name="accion" value="{{ 'desactivar' if p.estado == 'mantencion' else 'activar' }}">
                    <button class="btn-maint" title="Activar/Desactivar Mantención"><i class="bi bi-cone-striped"></i></button>
                </form>

                <div class="fw-bold">{{ p.nombre }}</div>

                {% if p.estado == 'ocupado' %}
                    <i class="bi bi-car-front-fill park-icon fs-4 my-1"></i>
                    
                    <span class="badge bg-danger mb-1" style="font-size: 0.6rem;">U. {{ p.unidad_numero }}</span>
                    
                    <div class="text-warning fw-bold small">{{ p.patente }}</div>
                    <small class="text-white-50" style="font-size: 0.65rem;">{{ p.tiempo }}</small>

                {% elif p.estado == 'libre' %}
                    <i class="bi bi-check-circle park-icon fs-3 my-1"></i>
                    <small class="text-success fw-bold" style="font-size: 0.7rem;">LIBRE</small>

                {% elif p.estado == 'mantencion' %}
                    <i class="bi bi-tools park-icon fs-4 my-1"></i>
                    <small class="text-info fw-bold" style="font-size: 0.7rem;">MANT.</small>

                {% else %}
                    <i class="bi bi-lock-fill park-icon fs-4 my-1"></i>
                    <small class="text-white-50" style="font-size: 0.7rem;">U. {{ p.unidad_numero }}</small>
                {% endif %}
            </div>
            {% endfor %}
        </div>    
    </div>

    <div class="row g-3 mb-4">
        <div class="col-6 col-md-3"><a href="/admin/gastos" class="btn-quick"><i class="bi bi-wallet2 text-success"></i><span>Gastos</span></a></div>
        <div class="col-6 col-md-3"><a href="#" onclick="abrirModalConserjes()" class="btn-quick"><i class="bi bi-person-badge-fill text-info"></i><span>Conserjes</span></a></div>
        <div class="col-6 col-md-3"><a href="#" onclick="abrirModalLogs()" class="btn-quick"><i class="bi bi-journal-text text-warning"></i><span>Bitácora</span></a></div>
        <div class="col-6 col-md-3"><a href="https://wa.me/{{ whatsapp_soporte }}" target="_blank" class="btn-quick"><i class="bi bi-whatsapp text-success"></i><span>Soporte</span></a></div>
    </div>

    <div class="card p-0 overflow-hidden">
        <div class="p-4 border-bottom border-white border-opacity-10 bg-black bg-opacity-25 d-flex justify-content-between align-items-center">
            <h5 class="m-0 fw-bold">Directorio Residentes</h5>
            <input type="text" id="buscador" class="form-control form-control-sm w-auto bg-black bg-opacity-25" placeholder="Buscar unidad...">
        </div>
        <div class="table-responsive" style="max-height: 400px;">
            <table class="table table-hover mb-0 align-middle" id="tablaResidentes">
                <thead class="bg-black bg-opacity-50 text-white-50 small text-uppercase">
                    <tr>
                        <th class="ps-4">Unidad</th>
                        <th>Residente / Arrendatario</th>
                        <th>RUT (Usuario)</th>
                        <th>Contacto</th>
                        <th class="text-end pe-4">Acciones Rápidas</th>
                    </tr>
                </thead>
                <tbody>
                    {% for u in unidades %}
                    <tr>
                        <td class="ps-4"><span class="badge bg-primary fs-6 shadow-sm">{{ u.numero }}</span></td>
                        <td>
                            <div class="fw-bold">{{ u.propietario }}</div>
                            <small class="text-info fw-bold">{{ u.tenant.nombre or '' }}</small>
                        </td>
                        <td class="font-monospace text-info small">
                            {{ u.tenant.rut if u.tenant.rut else u.owner.rut }}
                        </td>
                        <td class="small text-white-50">{{ u.owner.email }}</td>
                        <td class="pe-4 text-end">
                            <div class="btn-group btn-action-group" role="group">
                                <a href="https://wa.me/{{ u.tenant.fono or u.owner.fono }}" target="_blank" class="btn btn-outline-success" title="WhatsApp">
                                    <i class="bi bi-whatsapp"></i>
                                </a>
                                
                                <button type="button" class="btn btn-outline-light" title="Registrar Pago" onclick="abrirPago({{ u.id }}, '{{ u.numero }}')">
                                    <i class="bi bi-cash-coin"></i>
                                </button>

                                <button type="button" class="btn btn-outline-danger" title="Multar" onclick="abrirMulta({{ u.id }}, '{{ u.numero }}')">
                                    <i class="bi bi-receipt"></i>
                                </button>

                                <button type="button" class="btn btn-outline-warning" title="Editar Completo" onclick='abrirEditar({{ u | tojson }})'>
                                    <i class="bi bi-pencil-fill"></i>
                                </button>

                                <button type="button" class="btn btn-outline-info" title="Reset Clave" onclick="resetClave({{ u.id }}, '{{ u.tenant.rut if u.tenant.rut else u.owner.rut }}')">
                                    <i class="bi bi-key-fill"></i>
                                </button>
                            </div>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</div>

<div class="modal fade" id="modalRegistrarPago" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-success">
            <div class="modal-header border-0 bg-success bg-opacity-25">
                <h5 class="modal-title fw-bold text-white">Registrar Pago Depto <span id="pagoUnitNum"></span></h5>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <form action="/admin/residentes/registrar_pago" method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="unidad_id" id="pagoUnitId">
                    <div class="mb-3">
                        <label class="small text-white-50">Monto Transferencia ($)</label>
                        <input type="number" name="monto_pago" class="form-control" required>
                    </div>
                    <div class="mb-3">
                        <label class="small text-white-50">Comprobante (Foto/Captura)</label>
                        <input type="file" name="comprobante" class="form-control" required accept="image/*">
                    </div>
                    <button class="btn btn-success w-100 fw-bold">Registrar Abono</button>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="modalMultar" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-danger">
            <div class="modal-header border-0 bg-danger bg-opacity-25">
                <h5 class="modal-title fw-bold text-white">Multar Depto <span id="multaUnitNum"></span></h5>
                <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <form action="/admin/residentes/multar" method="POST">
                    <input type="hidden" name="unidad_id" id="multaUnitId">
                    <div class="mb-3">
                        <label class="small text-white-50">Motivo</label>
                        <input type="text" name="motivo" class="form-control" placeholder="Ej: Ruidos Molestos" required>
                    </div>
                    <div class="mb-3">
                        <label class="small text-white-50">Monto Multa ($)</label>
                        <input type="number" name="monto_multa" class="form-control" required>
                    </div>
                    <button class="btn btn-danger w-100 fw-bold">Aplicar Multa</button>
                </form>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="modalEditarUnidad" tabindex="-1"><div class="modal-dialog modal-dialog-centered"><div class="modal-content"><div class="modal-header border-0"><h5 class="modal-title fw-bold">Editar Ficha Unidad</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body"><form action="/admin/residentes/guardar_edicion" method="POST"><input type="hidden" name="unidad_id" id="editUnitId"><div class="row g-2 mb-3"><div class="col"><label class="text-white-50 small">Piso</label><input type="number" name="piso" id="editPiso" class="form-control"></div><div class="col"><label class="text-white-50 small">Prorrateo %</label><input type="text" name="prorrateo" id="editProrrateo" class="form-control"></div></div><h6 class="text-info border-bottom border-white border-opacity-10 pb-2 mb-3">Datos Residente (Arrendatario)</h6><div class="mb-2"><label class="small text-muted">Nombre</label><input type="text" name="tenant_nombre" id="editTenantNombre" class="form-control"></div><div class="mb-2"><label class="small text-muted">RUT</label><input type="text" name="tenant_rut" id="editTenantRut" class="form-control"></div><div class="mb-2"><label class="small text-muted">Email</label><input type="email" name="tenant_email" id="editTenantEmail" class="form-control"></div><div class="mb-3"><label class="small text-muted">Teléfono</label><input type="text" name="tenant_fono" id="editTenantFono" class="form-control"></div><button class="btn btn-warning w-100 mt-2 fw-bold">Guardar Ficha</button></form></div></div></div></div>

<div class="modal fade" id="modalPagarServicio" tabindex="-1"><div class="modal-dialog modal-dialog-centered"><div class="modal-content card-glass" style="border-color: #dc3545;"><div class="modal-header border-0 bg-danger bg-opacity-25"><h5 class="modal-title fw-bold text-white">Reportar Pago AEXON</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body"><p class="text-white-50 small">Sube comprobante del servicio de software.</p><form action="/admin/pagar_servicio" method="POST" enctype="multipart/form-data"><div class="mb-3"><input type="file" name="comprobante" class="form-control" required accept="image/*,.pdf"></div><button class="btn btn-danger w-100 fw-bold">ENVIAR</button></form></div></div></div></div>
<div class="modal fade" id="modalConfigParking" tabindex="-1"><div class="modal-dialog modal-sm modal-dialog-centered"><div class="modal-content card-glass"><div class="modal-header border-bottom border-white border-opacity-10"><h6 class="modal-title fw-bold text-white">Gestionar Espacios</h6><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body"><form action="/admin/parking/agregar" method="POST" class="mb-4"><div class="input-group"><input type="text" name="nombre" class="form-control" placeholder="Ej: V-10" required><button class="btn btn-success fw-bold"><i class="bi bi-plus-lg"></i></button></div></form><div style="max-height: 200px; overflow-y: auto;">{% for p in parking %}<div class="d-flex justify-content-between align-items-center mb-2 p-2 rounded bg-black bg-opacity-25"><span class="fw-bold text-white small">{{ p.nombre }}</span>{% if p.estado == 'libre' or p.estado == 'mantencion' %}<form action="/admin/parking/eliminar" method="POST" onsubmit="return confirm('¿Eliminar?')"><input type="hidden" name="id" value="{{ p.id }}"><button class="btn btn-sm btn-danger py-0 px-2"><i class="bi bi-trash"></i></button></form>{% else %}<span class="badge bg-warning text-dark" style="font-size: 0.6rem;">OCUPADO</span>{% endif %}</div>{% endfor %}</div></div></div></div></div>
<div class="modal fade" id="modalConserjes" tabindex="-1"><div class="modal-dialog modal-lg modal-dialog-centered"><div class="modal-content"><div class="modal-header border-0"><h5 class="modal-title fw-bold">Equipo de Conserjería</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="bg-black bg-opacity-25 p-3 rounded mb-4 border border-secondary"><div class="mb-2 text-info small fw-bold"><i class="bi bi-info-circle"></i> Nuevo Conserje: El "Usuario" será su RUT.</div><div class="row g-2"><div class="col-md-4"><input id="newCRut" class="form-control" placeholder="RUT (Sin puntos)"></div><div class="col-md-5"><input id="newCNombre" class="form-control" placeholder="Nombre Completo"></div><div class="col-md-3"><button class="btn btn-success fw-bold w-100" onclick="crearConserje()"><i class="bi bi-plus-lg"></i> Agregar</button></div></div></div><h6 class="text-white-50 border-bottom border-white border-opacity-10 pb-2 mb-3">Personal Activo</h6><div id="listaConserjes"><div class="text-center text-white-50 py-3"><div class="spinner-border spinner-border-sm text-info"></div> Cargando...</div></div></div></div></div></div>
<div class="modal fade" id="modalLogs" tabindex="-1"><div class="modal-dialog modal-xl modal-dialog-centered modal-dialog-scrollable"><div class="modal-content"><div class="modal-header border-0 bg-black bg-opacity-25"><h5 class="modal-title fw-bold"><i class="bi bi-journal-text text-warning me-2"></i>Bitácora Diaria</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="p-3 bg-black bg-opacity-50 border-bottom border-secondary d-flex gap-2 align-items-center"><div class="input-group input-group-sm" style="max-width: 200px;"><span class="input-group-text bg-dark text-white border-secondary"><i class="bi bi-calendar"></i></span><input type="date" id="filtroFecha" class="form-control bg-dark text-white border-secondary"></div><div class="input-group input-group-sm" style="max-width: 150px;"><span class="input-group-text bg-dark text-white border-secondary">U.</span><input type="text" id="filtroUnidad" class="form-control bg-dark text-white border-secondary" placeholder="Depto..."></div><button class="btn btn-sm btn-info fw-bold text-dark" onclick="cargarLogs()"><i class="bi bi-search me-1"></i> Filtrar</button><button class="btn btn-sm btn-outline-secondary ms-auto" onclick="setHoy()">Hoy</button></div><div class="modal-body p-0"><div class="table-responsive"><table class="table table-hover align-middle mb-0 text-white"><thead class="bg-black bg-opacity-50 text-white-50 small text-uppercase"><tr><th class="ps-4">Hora</th><th>Unidad</th><th>Tipo</th><th>Detalle</th><th>Info Extra</th></tr></thead><tbody id="tablaLogsBody"></tbody></table></div></div><div class="modal-footer border-0 bg-black bg-opacity-25"><small class="text-white-50 ms-auto">Historial de seguridad.</small></div></div></div></div>

<script>
    document.addEventListener("DOMContentLoaded", function() {
        var map = L.map('miniMap', {zoomControl: false}).setView([{{ edificio.lat | default(-33.4) }}, {{ edificio.lon | default(-70.6) }}], 15);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png').addTo(map);
    });

    function verDetalleDia(dia, eventos) {
        let html = '';
        if(eventos.length === 0) { html = '<p class="text-white-50">Sin eventos.</p>'; } 
        else {
            eventos.forEach(e => {
                html += `<div class="text-start border-bottom border-white border-opacity-10 py-2"><strong class="text-info">${e.nombre}</strong><div class="d-flex justify-content-between"><span class="text-white-50 small">Costo</span><span class="text-success fw-bold">$${parseInt(e.costo).toLocaleString()}</span></div></div>`;
            });
        }
        Swal.fire({ title: `Agenda Día ${dia}`, html: html, background: '#1a1d24', color: '#fff' });
    }

    // --- FUNCIONES NUEVAS PARA RESIDENTES ---
    function abrirEditar(u) {
        document.getElementById('editUnitId').value = u.id;
        document.getElementById('editPiso').value = u.piso;
        document.getElementById('editProrrateo').value = u.prorrateo;
        document.getElementById('editTenantNombre').value = u.tenant.nombre || '';
        document.getElementById('editTenantRut').value = u.tenant.rut || '';
        document.getElementById('editTenantEmail').value = u.tenant.email || '';
        document.getElementById('editTenantFono').value = u.tenant.fono || '';
        new bootstrap.Modal(document.getElementById('modalEditarUnidad')).show();
    }

    function abrirPago(uid, numero) {
        document.getElementById('pagoUnitId').value = uid;
        document.getElementById('pagoUnitNum').innerText = numero;
        new bootstrap.Modal(document.getElementById('modalRegistrarPago')).show();
    }

    function abrirMulta(uid, numero) {
        document.getElementById('multaUnitId').value = uid;
        document.getElementById('multaUnitNum').innerText = numero;
        new bootstrap.Modal(document.getElementById('modalMultar')).show();
    }

    function resetClave(uid, rutUsuario) {
        if(!confirm('¿Resetear clave?')) return;
        fetch('/admin/residentes/reset_clave', {
            method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `unidad_id=${uid}`
        }).then(r => r.json()).then(d => {
            if(d.status === 'success') {
                Swal.fire({ 
                    title: '¡Clave Generada!', 
                    html: `
                        <div class="text-start bg-dark p-3 rounded border border-secondary">
                            <p class="mb-2 text-white-50">Acceso para el residente:</p>
                            <div class="mb-2">Usuario (RUT): <strong class="text-info fs-5">${rutUsuario}</strong></div>
                            <div>Contraseña: <strong class="text-warning fs-4">${d.password}</strong></div>
                        </div>
                    `, 
                    icon: 'success', background: '#1a1d24', color: '#fff' 
                });
            } else { Swal.fire('Error', d.message, 'error'); }
        });
    }

    // --- CONSERJES ---
    function abrirModalConserjes() { new bootstrap.Modal(document.getElementById('modalConserjes')).show(); cargarConserjes(); }
    function cargarConserjes() {
        fetch('/admin/conserjes/listar').then(r => r.json()).then(data => {
            let html = '<ul class="list-group list-group-flush">';
            data.forEach(c => {
                html += `<li class="list-group-item bg-transparent d-flex justify-content-between text-white px-0 border-bottom border-white border-opacity-10"><div><span class="fw-bold">${c.nombre}</span><div class="small text-white-50">User: ${c.rut}</div></div><div><button class="btn btn-sm btn-outline-warning me-2" onclick="resetearClaveConserje('${c.rut}','${c.nombre}')"><i class="bi bi-key-fill"></i></button><button class="btn btn-sm btn-outline-danger border-0" onclick="eliminarConserje('${c.rut}')"><i class="bi bi-trash-fill"></i></button></div></li>`;
            });
            document.getElementById('listaConserjes').innerHTML = html + '</ul>';
        });
    }
    function crearConserje() {
        let r = document.getElementById('newCRut').value, n = document.getElementById('newCNombre').value;
        if(!r || !n) return Swal.fire('Faltan datos','','warning');
        fetch('/admin/conserjes/crear', { method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `rut=${r}&nombre=${n}&email=staff@habita.cl` })
        .then(r=>r.json()).then(d => {
            if(d.status==='success') { Swal.fire({title:'Creado', html:`Clave: <h2 class="text-warning">${d.password}</h2>`, icon:'success', background:'#1a1d24', color:'#fff'}); cargarConserjes(); }
            else Swal.fire('Error','No se pudo crear','error');
        });
    }
    function resetearClaveConserje(rut, nombre) {
        if(confirm(`¿Nueva clave para ${nombre}?`)) fetch('/admin/conserjes/reset_clave', { method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `rut=${rut}` }).then(r=>r.json()).then(d=> Swal.fire({title:'Nueva Clave', html:`<h2 class="text-warning">${d.password}</h2>`, background:'#1a1d24', color:'#fff'}));
    }
    function eliminarConserje(rut) { if(confirm('¿Eliminar?')) fetch('/admin/conserjes/eliminar', { method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: `rut=${rut}` }).then(()=>cargarConserjes()); }

    // --- BITÁCORA ---
    function abrirModalLogs() { setHoy(false); new bootstrap.Modal(document.getElementById('modalLogs')).show(); cargarLogs(); }
    function setHoy(recargar=true) { document.getElementById('filtroFecha').value = new Date().toISOString().split('T')[0]; document.getElementById('filtroUnidad').value=''; if(recargar) cargarLogs(); }
    function cargarLogs() {
        let f = document.getElementById('filtroFecha').value, u = document.getElementById('filtroUnidad').value;
        document.getElementById('tablaLogsBody').innerHTML = '<tr><td colspan="5" class="text-center py-4"><div class="spinner-border spinner-border-sm"></div></td></tr>';
        fetch(`/admin/logs/listar?fecha=${f}&unidad=${u}`).then(r=>r.json()).then(d => {
            let html = '';
            if(d.length===0) html='<tr><td colspan="5" class="text-center py-4 text-white-50">Sin registros.</td></tr>';
            d.forEach(l => {
                let color='secondary', icon='circle';
                if(l.tipo==='VISITA'){color='primary';icon='person-walking';} else if(l.tipo==='PAGO'){color='success';icon='cash-coin';} else if(l.tipo==='INCIDENCIA'){color='danger';icon='exclamation-triangle';}
                html += `<tr><td class="ps-4 text-info font-monospace">${l.fecha}</td><td>${l.unidad!=='-'?`<span class="badge bg-light text-dark">U. ${l.unidad}</span>`:'-'}</td><td><span class="badge bg-${color}"><i class="bi bi-${icon}"></i> ${l.tipo}</span></td><td>${l.detalle}</td><td class="small text-white-50">${l.extra}</td></tr>`;
            });
            document.getElementById('tablaLogsBody').innerHTML = html;
        });
    }

    document.getElementById('buscador').addEventListener('keyup', function() {
        let val = this.value.toLowerCase();
        document.querySelectorAll('#tablaResidentes tbody tr').forEach(r => r.style.display = r.innerText.toLowerCase().includes(val) ? '' : 'none');
    });
</script>
{% endblock %}
```

## templates/dash_residente.html
```html
{% extends "base.html" %}
{% block title %}| Mi Hogar{% endblock %}

{% block styles %}
<meta name="theme-color" content="#0dcaf0">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="https://cdn-icons-png.flaticon.com/512/555/555545.png">



<style>
    /* Diseño Base */
    body { padding-bottom: 90px; background: #0a0a0f; font-family: 'Segoe UI', sans-serif; }
    
    .app-header { background: linear-gradient(180deg, rgba(15,15,20,0.98) 0%, rgba(10,10,15,0) 100%); padding: 20px 15px; position: sticky; top: 0; z-index: 1000; }
    .card-app { background: #151820; border-radius: 18px; border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 8px 20px rgba(0,0,0,0.4); margin-bottom: 15px; overflow: hidden; }
    
    /* TARJETA PENDIENTES (Dashboard) */
    .dashboard-scroll { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 5px; scrollbar-width: none; }
    .dash-card { min-width: 140px; border-radius: 14px; padding: 12px; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(255,255,255,0.1); position: relative; overflow: hidden; }
    .dash-card h3 { font-size: 1.8rem; font-weight: 800; margin: 0; }
    .dash-card small { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.8; }
    
    .card-package { background: linear-gradient(135deg, #ffc107, #d35400); color: #000; }
    .card-alert { background: linear-gradient(135deg, #dc3545, #a71d2a); color: white; }
    .card-visit { background: linear-gradient(135deg, #0dcaf0, #0d6efd); color: white; }
    .card-empty { background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.3); border-style: dashed; justify-content: center; align-items: center; }

    /* BOTONES */
    .btn-invite { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 20px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: white; height: 100%; width: 100%; transition: 0.2s; }
    .btn-invite:active { transform: scale(0.96); background: rgba(255,255,255,0.1); }
    .btn-pay { background: linear-gradient(90deg, #00dbde 0%, #fc00ff 100%); border: none; border-radius: 12px; color: white; font-weight: 800; padding: 12px; width: 100%; text-shadow: 0 1px 2px rgba(0,0,0,0.3); }

    /* MODAL BRANDING */
    .brand-header {
        background: radial-gradient(circle at center, #1a2a6c, #b21f1f, #fdbb2d); /* Estilo Tecnológico/Energético */
        background-size: 200% 200%;
        animation: gradientBG 5s ease infinite;
        padding: 30px 20px;
        text-align: center;
        border-bottom: 1px solid rgba(255,255,255,0.2);
    }
    @keyframes gradientBG { 0% {background-position: 0% 50%;} 50% {background-position: 100% 50%;} 100% {background-position: 0% 50%;} }
    .brand-logo { font-family: 'Courier New', monospace; letter-spacing: -1px; font-weight: 800; font-size: 1.5rem; color: white; text-shadow: 0 2px 10px rgba(0,0,0,0.5); }
    .brand-tag { font-size: 0.7rem; letter-spacing: 2px; text-transform: uppercase; color: rgba(255,255,255,0.8); margin-top: 5px; display: block; }
    
    .bottom-nav { position: fixed; bottom: 0; left: 0; width: 100%; background: #0f1014; border-top: 1px solid rgba(255,255,255,0.1); display: flex; justify-content: space-around; padding: 12px 0; z-index: 1000; }
    .nav-item-app { color: rgba(255,255,255,0.4); text-align: center; text-decoration: none; font-size: 0.7rem; }
    .nav-item-app.active { color: #0dcaf0; text-shadow: 0 0 10px rgba(13, 202, 240, 0.5); }
    .nav-item-app i { font-size: 1.4rem; display: block; margin-bottom: 2px; }
</style>
{% endblock %}

{% block content %}
<div class="app-header">
    <div class="d-flex justify-content-between align-items-center mb-3">
        <div class="d-flex align-items-center gap-2">
            <div class="bg-primary rounded-circle d-flex align-items-center justify-content-center text-white fw-bold shadow" style="width: 42px; height: 42px;">{{ u.numero }}</div>
            <div>
                <h6 class="m-0 text-white fw-bold">{{ session.nombre.split()[0] }}</h6>
                <span class="badge bg-white bg-opacity-10 text-white border border-white border-opacity-25" style="font-size: 0.65rem;">Residente</span>
            </div>
        </div>
        <a href="/logout" class="text-white-50"><i class="bi bi-power fs-4"></i></a>
    </div>
    <div class="building-info p-2 rounded border border-secondary bg-black bg-opacity-25 d-flex align-items-center justify-content-between">
        <span class="text-white small fw-bold"><i class="bi bi-building me-1 text-info"></i> {{ edificio.nombre }}</span>
        <span class="text-success small fw-bold"><i class="bi bi-wifi"></i> Online</span>
    </div>
</div>

<div class="container px-3">
    
    <h6 class="text-white-50 text-uppercase small fw-bold mb-2 ps-1">Resumen de Hoy</h6>
    <div class="dashboard-scroll mb-4">
        {% if encomiendas %}
        <div class="dash-card card-package animate__animated animate__pulse animate__infinite">
            <div><i class="bi bi-box-seam-fill fs-4"></i></div>
            <div>
                <h3>{{ encomiendas|length }}</h3>
                <small>Paquetes</small>
            </div>
        </div>
        {% endif %}

        {% if multas %}
        <div class="dash-card card-alert">
            <div><i class="bi bi-exclamation-triangle-fill fs-4"></i></div>
            <div>
                <h3>{{ multas|length }}</h3>
                <small>Multas</small>
            </div>
        </div>
        {% endif %}

        {% if visitas_activas %}
            {% for v in visitas_activas %}
            <div class="dash-card card-visit">
                <div class="d-flex justify-content-between">
                    <i class="bi bi-clock-history fs-4"></i>
                    {% if v.parking_nombre %}<span class="badge bg-white text-primary">{{ v.parking_nombre }}</span>{% endif %}
                </div>
                <div>
                    {% set restantes = 300 - v.minutos_transcurridos|int %}
                    {% if restantes > 0 %}
                        <h4 class="m-0">{{ (restantes/60)|int }}h {{ (restantes%60)|int }}m</h4>
                        <small>Restantes</small>
                    {% else %}
                        <h5 class="m-0">VENCIDO</h5>
                        <small>Debe salir</small>
                    {% endif %}
                </div>
            </div>
            {% endfor %}
        {% endif %}

        {% if not encomiendas and not multas and not visitas_activas %}
        <div class="dash-card card-empty" style="width: 100%;">
            <i class="bi bi-check-circle fs-1 mb-2"></i>
            <small>Sin pendientes</small>
        </div>
        {% endif %}
    </div>

    <h6 class="text-white-50 text-uppercase small fw-bold mb-2 ps-1">Gestionar Accesos</h6>
    <div class="row g-3 mb-4">
        <div class="col-6">
            <button class="btn-invite" onclick="abrirModalInvitacion('PEATON')">
                <i class="bi bi-person-walking text-success mb-2"></i>
                <span class="fw-bold">A Pie</span>
                <span class="text-white-50 small" style="font-size: 0.65rem;">Código QR</span>
            </button>
        </div>
        <div class="col-6">
            <button class="btn-invite" onclick="abrirModalInvitacion('VEHICULO')">
                <i class="bi bi-car-front-fill text-warning mb-2"></i>
                <span class="fw-bold">En Auto</span>
                <span class="text-white-50 small" style="font-size: 0.65rem;">Asigna Parking</span>
            </button>
        </div>
    </div>

    <div class="card-app p-4 text-center">
        <div class="d-flex justify-content-between align-items-center mb-2">
            <span class="text-white-50 small text-uppercase fw-bold">Total a Pagar</span>
            <span class="badge {{ 'bg-danger' if u.deuda_monto > 0 else 'bg-success' }}">
                {{ 'VENCIDO' if u.deuda_monto > 0 else 'AL DÍA' }}
            </span>
        </div>
        <h1 class="display-5 fw-bold text-white mb-3">${{ "{:,.0f}".format(u.deuda_monto) }}</h1>
        
        {% if multas %}
            <div class="bg-black bg-opacity-25 p-2 rounded mb-3 text-start">
                <small class="text-danger fw-bold d-block mb-1"><i class="bi bi-info-circle"></i> Detalle Multas:</small>
                {% for m in multas %}
                    <div class="d-flex justify-content-between text-white-50 small border-bottom border-secondary pb-1 mb-1">
                        <span>{{ m.motivo }}</span>
                        <span class="text-white">${{ "{:,.0f}".format(m.monto) }}</span>
                    </div>
                {% endfor %}
            </div>
        {% endif %}

        {% if u.deuda_monto > 0 %}
        <form action="/residente/pagar_deuda" method="POST">
            <input type="hidden" name="unidad_id" value="{{ u.id }}">
            <input type="hidden" name="monto" value="{{ u.deuda_monto }}">
            <button class="btn-pay shadow">PAGAR AHORA <i class="bi bi-chevron-right"></i></button>
        </form>
        {% endif %}
    </div>

    <div class="card-app p-3">
        <h6 class="text-white fw-bold mb-3"><i class="bi bi-stars text-warning me-2"></i> Amenities</h6>
        <form action="/residente/reservar" method="POST">
            <div class="input-group mb-2">
                <select name="espacio_id" class="form-select bg-dark text-white border-secondary" required onchange="actualizarPrecio(this)">
                    <option value="" data-precio="0">Seleccionar...</option>
                    {% for e in espacios %}
                    <option value="{{ e.id }}" data-precio="{{ e.precio }}">{{ e.nombre }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="row g-2 mb-3">
                <div class="col-7"><input type="date" name="fecha" class="form-control bg-dark text-white border-secondary" required min="{{ hoy }}"></div>
                <div class="col-5"><input type="time" name="hora" class="form-control bg-dark text-white border-secondary" required></div>
            </div>
            <div class="d-flex justify-content-between align-items-center mb-2">
                <span class="text-white-50 small">Valor:</span>
                <span class="text-info fw-bold" id="precioLabel">$0</span>
            </div>
            <button class="btn btn-outline-info w-100 fw-bold btn-sm">Reservar</button>
        </form>
        
        {% if mis_reservas %}
        <div class="mt-3 pt-3 border-top border-secondary">
            {% for r in mis_reservas %}
            <div class="d-flex justify-content-between align-items-center bg-black bg-opacity-25 p-2 rounded mb-1">
                <div>
                    <span class="text-white small fw-bold d-block">{{ r.nombre_espacio }}</span>
                    <span class="text-warning small" style="font-size: 0.7rem;"><i class="bi bi-clock"></i> {{ r.hora_inicio or '--:--' }}</span>
                </div>
                <span class="badge bg-primary">{{ r.fecha_uso }}</span>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>
    
    <div class="text-center mt-4 mb-3">
        <button class="btn btn-link text-white-50 text-decoration-none btn-sm" data-bs-toggle="modal" data-bs-target="#modalPerfil">
            <i class="bi bi-gear-fill me-1"></i> Configurar Perfil
        </button>
    </div>
</div>

<div class="bottom-nav">
    <a href="#" class="nav-item-app active"><i class="bi bi-house-door-fill"></i>Inicio</a>
    <a href="#" class="nav-item-app"><i class="bi bi-receipt"></i>Historial</a>
    <a href="#" class="nav-item-app"><i class="bi bi-chat-dots-fill"></i>Ayuda</a>
</div>

<div class="modal fade" id="modalGenerarInv" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content border-0 overflow-hidden" style="background:#1a1d24; color:white;">
            
            <div class="brand-header">
                <div class="brand-logo">HabitaPro</div>
                <span class="brand-tag">TECNOLOGÍA PURA</span>
                <small class="d-block mt-2 text-white-50" style="font-size: 0.65rem;">Powered by Aexon • www.habitapro.cl</small>
            </div>

            <div class="modal-body p-4">
                <h5 class="text-center fw-bold mb-1 text-white">Nueva Invitación <span id="tipoInvTitulo" class="text-info"></span></h5>
                <p class="text-center text-white-50 small mb-4">Genera un acceso seguro y rápido para tus visitas.</p>
                
                <input type="hidden" id="tipoInvInput">
                <div class="form-floating mb-4">
                    <input type="text" class="form-control bg-dark text-white border-secondary" id="nombreVisitaInput" placeholder="Nombre (Opcional)">
                    <label class="text-white-50">Nombre Visita (Opcional)</label>
                </div>
                
                <button class="btn btn-primary w-100 fw-bold py-3 shadow-lg" onclick="generarLink()" style="border-radius: 12px; background: linear-gradient(90deg, #0dcaf0, #0d6efd);">
                    <i class="bi bi-qr-code-scan me-2"></i> GENERAR ACCESO DIGITAL
                </button>
                
                <div class="text-center mt-3">
                    <small class="text-white-50" style="font-size: 0.6rem;"><i class="bi bi-shield-lock-fill me-1"></i> Acceso encriptado y monitoreado</small>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="modalPerfil" tabindex="-1"><div class="modal-dialog modal-dialog-centered"><div class="modal-content" style="background:#1a1d24; color:white;"><div class="modal-header border-0"><h5 class="modal-title">Mis Datos</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body"><form action="/residente/perfil/editar" method="POST"><div class="mb-3"><label>Email</label><input type="email" name="email" value="{{ user.email }}" class="form-control bg-dark text-white"></div><div class="mb-3"><label>Teléfono</label><input type="text" name="telefono" value="{{ user.telefono }}" class="form-control bg-dark text-white"></div><button class="btn btn-primary w-100">Guardar</button></form></div></div></div></div>

<script>
    function actualizarPrecio(select) {
        let precio = select.options[select.selectedIndex].getAttribute('data-precio');
        document.getElementById('precioLabel').textContent = "$" + parseInt(precio).toLocaleString();
    }

    function abrirModalInvitacion(tipo) {
        document.getElementById('tipoInvInput').value = tipo;
        document.getElementById('tipoInvTitulo').textContent = tipo === 'PEATON' ? 'Peatonal' : 'Vehicular';
        new bootstrap.Modal(document.getElementById('modalGenerarInv')).show();
    }

    function generarLink() {
        let tipo = document.getElementById('tipoInvInput').value;
        let nombre = document.getElementById('nombreVisitaInput').value;
        let btn = document.querySelector('#modalGenerarInv .btn-primary');
        
        btn.disabled = true; btn.innerHTML = '<div class="spinner-border spinner-border-sm"></div> Encriptando...';

        fetch('/residente/invitar/generar', {
            method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: `tipo=${tipo}&nombre=${nombre}`
        })
        .then(r => r.json())
        .then(d => {
            bootstrap.Modal.getInstance(document.getElementById('modalGenerarInv')).hide();
            Swal.fire({
                title: '¡Acceso Creado!',
                html: `<div class="text-center">
                        <i class="bi bi-check-circle-fill text-success display-1 mb-3"></i>
                        <p class="text-white-50">Comparte este enlace seguro:</p>
                        <div class="input-group mb-3">
                           <input type="text" class="form-control bg-dark text-white border-secondary" value="${d.link}" id="linkInv" readonly>
                           <button class="btn btn-success" onclick="copiarLink()"><i class="bi bi-whatsapp"></i></button>
                       </div>
                       </div>`,
                background: '#1a1d24', color: '#fff', showConfirmButton: false
            });
            btn.disabled = false; btn.innerHTML = '<i class="bi bi-qr-code-scan me-2"></i> GENERAR ACCESO DIGITAL';
        });
    }
    
    function copiarLink() {
        let copyText = document.getElementById("linkInv");
        copyText.select();
        document.execCommand("copy");
        window.open(`https://wa.me/?text=Hola, te envío un acceso seguro de HabitaPro para ingresar al edificio: ${copyText.value}`, '_blank');
    }
</script>
{% endblock %}
```

## templates/dash_conserje.html
```html
{% extends "base.html" %}
{% block title %}| Conserjería{% endblock %}

{% block styles %}
<script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>

<style>
    /* Estilos Conserjería */
    .card-glass {
        background: rgba(20, 20, 30, 0.85);
        backdrop-filter: blur(15px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 16px;
        box-shadow: 0 4px 30px rgba(0, 0, 0, 0.5);
        color: white;
    }

    /* Parking Grid */
    .parking-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
        gap: 12px;
    }

    .park-slot {
        height: 140px;
        border-radius: 12px;
        position: relative;
        cursor: pointer;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        align-items: center;
        padding: 8px;
        border: 2px solid rgba(255, 255, 255, 0.1);
        background: rgba(255, 255, 255, 0.05);
        transition: 0.2s;
        overflow: hidden;
    }

    .park-slot:hover {
        transform: scale(1.05);
        z-index: 10;
        border-color: white;
    }

    .slot-name {
        font-size: 1.5rem;
        font-weight: 800;
        text-transform: uppercase;
        width: 100%;
        text-align: center;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .slot-icon {
        font-size: 2.5rem;
        line-height: 1;
    }

    .slot-timer {
        font-family: 'Courier New', monospace;
        background: rgba(0, 0, 0, 0.6);
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 0.7rem;
        font-weight: bold;
        color: #ffc107;
        width: 100%;
        text-align: center;
    }

    /* Estados Parking */
    .park-libre {
        border-color: #2ecc71;
        color: #2ecc71;
    }

    .park-libre .slot-icon {
        color: #2ecc71;
    }

    .park-ocupado {
        background: rgba(241, 196, 15, 0.15);
        border-color: #f1c40f;
        color: white;
    }

    .park-ocupado .slot-icon {
        color: #f1c40f;
    }

    .park-mantencion {
        background: rgba(52, 152, 219, 0.15);
        border-color: #3498db;
        opacity: 0.7;
        color: #3498db;
    }

    /* Botones Grandes */
    .btn-action-lg {
        height: 120px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
        border-radius: 16px;
        font-size: 1.1rem;
        font-weight: bold;
        text-transform: uppercase;
        border: none;
        transition: 0.3s;
        color: white;
    }

    .btn-action-lg i {
        font-size: 3rem;
        margin-bottom: 10px;
    }

    .bg-qr {
        background: linear-gradient(135deg, #6f42c1, #59359a);
    }

    .bg-box {
        background: linear-gradient(135deg, #fd7e14, #d35400);
    }

    .bg-log {
        background: linear-gradient(135deg, #20c997, #198754);
    }

    .bg-sos {
        background: linear-gradient(135deg, #dc3545, #a71d2a);
    }

    .bg-shift {
        background: linear-gradient(135deg, #0dcaf0, #0d6efd);
    }

    .table {
        color: white;
    }

    .table tbody td {
        border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        vertical-align: middle;
    }

    .form-control,
    .form-select {
        background: rgba(0, 0, 0, 0.4);
        border: 1px solid rgba(255, 255, 255, 0.2);
        color: white;
    }

    #reader {
        width: 100%;
        border-radius: 12px;
        border: 2px solid #0dcaf0;
    }
</style>
{% endblock %}

{% block content %}
<nav class="navbar navbar-glass fixed-top">
    <div class="container-fluid px-4">
        <a class="navbar-brand d-flex align-items-center gap-2 text-white" href="#">
            <i class="bi bi-shield-lock-fill text-info fs-3"></i>
            <span class="fw-bold fs-4">Conserjería <span class="fw-light opacity-50">{{ edificio.nombre }}</span></span>
        </a>
        <div class="d-flex align-items-center gap-3">
            <span class="text-white me-2">{{ session.nombre }}</span>
            <a href="/logout" class="btn btn-outline-danger border-0"><i class="bi bi-power fs-4"></i></a>
        </div>
    </div>
</nav>

<div class="container-fluid px-4" style="margin-top: 100px; padding-bottom: 50px;">
    {% with msgs = get_flashed_messages() %}
    {% if msgs %}
    <div
        style="position: fixed; top: 80px; left: 50%; transform: translateX(-50%); z-index: 2000; width: 80%; max-width: 900px;">
        {% for m in msgs %}
        <div class="alert alert-success bg-success bg-opacity-75 text-white border-0 text-center shadow-lg rounded-pill"
            role="alert">{{ m }}</div>
        {% endfor %}
    </div>
    {% endif %}
    {% endwith %}

    <!-- BOTONES DE ACCIÓN RÁPIDA -->
    <div class="row g-3 mb-5">
        <div class="col-6 col-md-3"><button class="btn-action-lg bg-qr w-100" data-bs-toggle="modal"
                data-bs-target="#modalQR"><i class="bi bi-qr-code-scan"></i> Escanear Pase</button></div>
        <div class="col-6 col-md-3"><button class="btn-action-lg bg-box w-100" data-bs-toggle="modal"
                data-bs-target="#modalEncomienda"><i class="bi bi-box-seam"></i> Recepción</button></div>
        <div class="col-6 col-md-3"><button class="btn-action-lg bg-log w-100" data-bs-toggle="modal"
                data-bs-target="#modalBitacora"><i class="bi bi-journal-text"></i> Bitácora</button></div>
        <div class="col-6 col-md-3"><button class="btn-action-lg bg-sos w-100" onclick="enviarAlerta()"><i
                    class="bi bi-megaphone"></i> Alerta SOS</button></div>
    </div>

    <div class="card-glass p-4 mb-5">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <h4 class="m-0 fw-bold"><i class="bi bi-p-square-fill me-2 text-primary"></i> Control de Estacionamiento
            </h4>
        </div>

        <!-- LEYENDA SEPARADA (MEJORA SOLICITADA) -->
        <div
            class="d-flex gap-4 mb-4 p-2 rounded bg-black bg-opacity-25 border border-secondary justify-content-center text-white-50 small">
            <span class="d-flex align-items-center"><i class="bi bi-circle-fill text-success me-2"></i> Libre</span>
            <span class="d-flex align-items-center"><i class="bi bi-circle-fill text-warning me-2"></i> Ocupado</span>
            <span class="d-flex align-items-center"><i class="bi bi-circle-fill text-info me-2"></i> Mantención</span>
        </div>

        <div class="parking-grid">
            {% for p in parking %}
            <div class="park-slot park-{{ p.estado }}"
                onclick="gestionarParking('{{ p.id }}', '{{ p.nombre }}', '{{ p.estado }}', '{{ p.patente }}')">

                <div class="slot-name">{{ p.nombre }}</div>

                <div class="slot-icon">
                    {% if p.estado == 'libre' %}<i class="bi bi-check-circle"></i>
                    {% elif p.estado == 'mantencion' %}<i class="bi bi-cone-striped"></i>
                    {% elif p.estado == 'asignado' %}<i class="bi bi-house-lock"></i>{% endif %}
                </div>

                {% if p.estado == 'ocupado' %}
                <div class="lh-1 text-center w-100">
                    <div class="badge bg-white text-danger mb-1" style="font-size: 0.7rem;">
                        Depto {{ p.unidad_numero }}
                    </div>

                    <div class="fw-bold text-warning small mb-1">{{ p.patente }}</div>
                    <div class="slot-timer" id="timer-{{ p.id }}" data-elapsed="{{ p.tiempo }}">Calculando...</div>
                </div>

                {% elif p.estado == 'libre' %}
                <small class="fw-bold">LIBRE</small>

                {% elif p.estado == 'mantencion' %}
                <small class="fw-bold" style="font-size:0.6rem">MANTENCIÓN</small>

                {% else %}
                <small class="text-info fw-bold">U. {{ p.unidad_numero }}</small>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>

    <div class="row g-4">
        <div class="col-lg-12">
            <div class="card-glass p-3 mb-10">
                <h5 class="fw-bold mb-3"><i class="bi bi-calendar-check text-success me-2"></i> Agenda Amenities</h5>
                <div class="d-flex gap-2 overflow-auto pb-2">
                    {% for r in reservas_futuras %}
                    <div class="bg-dark border border-secondary p-2 rounded text-center" style="min-width: 160px;">
                        <div class="text-white fw-bold">{{ r.nombre_espacio }}</div>
                        <div class="text-info fw-bold small my-1">
                            {{ r.fecha_uso }}
                            <br>
                            <span class="badge bg-warning text-dark">
                                <i class="bi bi-clock"></i> {{ r.hora_inicio or '--:--' }}
                            </span>
                        </div>
                        <div class="small text-white-50">U. {{ r.numero_unidad }}</div>
                    </div>
                    {% else %}
                    <div class="text-white-50 small">No hay reservas próximas.</div>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="col-lg-6">
            <div class="card-glass p-3 h-100">
                <h5 class="fw-bold mb-3"><i class="bi bi-box2-fill text-warning me-2"></i> Por Entregar</h5>
                <div class="table-responsive" style="max-height: 400px;">
                    <table class="table table-hover align-middle">
                        <thead>
                            <tr class="text-white-50 small">
                                <th>U.</th>
                                <th>Remitente</th>
                                <th>Hora</th>
                                <th>Acción</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for e in encomiendas %}
                            <tr>
                                <td><span class="badge bg-warning text-dark">{{ e.unidad }}</span></td>
                                <td class="fw-bold">{{ e.remitente }}</td>
                                <td class="small text-white-50">{{ e.recepcion.strftime('%H:%M') }}</td>
                                <td>
                                    <form action="/conserje/encomiendas/entregar" method="POST">
                                        <input type="hidden" name="encomienda_id" value="{{ e.id }}">
                                        <button class="btn btn-sm btn-outline-success"><i
                                                class="bi bi-check-lg"></i></button>
                                    </form>
                                </td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="4" class="text-center text-white-50 py-4">No hay paquetes.</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div class="col-lg-6">
            <div class="card-glass p-3 h-100">
                <h5 class="fw-bold mb-3"><i class="bi bi-people-fill text-info me-2"></i> Directorio Residentes</h5>
                <input type="text" id="buscadorResidentes" class="form-control mb-3"
                    placeholder="Buscar unidad o nombre...">
                <div class="table-responsive" style="max-height: 400px;">
                    <table class="table table-hover align-middle" id="tablaResidentes">
                        <thead>
                            <tr class="text-white-50 small">
                                <th>Depto</th>
                                <th>Residente</th>
                                <th>RUT (Usuario)</th>
                                <th>Rol</th>
                                <th class="text-end">Acciones</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for u in unidades %}
                            <tr>
                                <td><span class="fs-6 fw-bold text-info">{{ u.numero }}</span></td>
                                <td>
                                    <div class="small fw-bold">{{ u.residente }}</div>
                                    <div class="small text-white-50" style="font-size: 0.75rem;">{{ u.email }}</div>
                                </td>
                                <td class="font-monospace small">
                                    {{ u.tenant.rut if u.tenant.rut else u.owner.rut }}
                                </td>
                                <td>
                                    {% if u.tenant.nombre %}
                                    <span class="badge bg-info text-dark"
                                        style="font-size: 0.65rem;">ARRENDATARIO</span>
                                    {% else %}
                                    <span class="badge bg-primary bg-opacity-50"
                                        style="font-size: 0.65rem;">PROPIETARIO</span>
                                    {% endif %}
                                </td>
                                <td class="text-end">
                                    <div class="d-flex justify-content-end gap-1">
                                        {% if u.fono %}
                                        <a href="https://wa.me/{{ u.fono | replace('+','') | replace(' ','') }}"
                                            target="_blank" class="btn btn-sm btn-success" title="WhatsApp">
                                            <i class="bi bi-whatsapp"></i>
                                        </a>
                                        {% endif %}

                                        <button class="btn btn-sm btn-outline-warning"
                                            onclick='abrirEditarCompleto({{ u | tojson }})' title="Editar Ficha">
                                            <i class="bi bi-pencil-square"></i>
                                        </button>

                                        <button class="btn btn-sm btn-outline-danger"
                                            onclick="abrirMulta({{ u.id }}, '{{ u.numero }}')" title="Multar">
                                            <i class="bi bi-receipt"></i>
                                        </button>

                                        <button class="btn btn-sm btn-outline-info"
                                            onclick="resetClave({{ u.id }}, '{{ u.tenant.rut if u.tenant.rut else u.owner.rut }}')"
                                            title="Reset Clave">
                                            <i class="bi bi-key-fill"></i>
                                        </button>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
</div>

<div class="modal fade" id="modalEditarCompleto" tabindex="-1">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content" style="background:#1a1d24; color:white;">
            <div class="modal-header border-0">
                <h5 class="modal-title fw-bold">Editar Ficha Residente</h5><button type="button"
                    class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
            </div>
            <div class="modal-body">
                <form action="/admin/residentes/guardar_edicion" method="POST">
                    <input type="hidden" name="unidad_id" id="editFullId">
                    <div class="row g-2 mb-3">
                        <div class="col"><label class="small text-white-50">Piso</label><input type="number" name="piso"
                                id="editFullPiso" class="form-control"></div>
                        <div class="col"><label class="small text-white-50">Prorrateo %</label><input type="text"
                                name="prorrateo" id="editFullProrrateo" class="form-control"></div>
                    </div>
                    <hr class="border-secondary">
                    <h6 class="text-info small mb-3">Datos de Contacto</h6>
                    <div class="mb-2"><label class="small text-muted">Nombre</label><input type="text"
                            name="tenant_nombre" id="editFullNombre" class="form-control"></div>
                    <div class="mb-2"><label class="small text-muted">RUT</label><input type="text" name="tenant_rut"
                            id="editFullRut" class="form-control"></div>
                    <div class="mb-2"><label class="small text-muted">Email</label><input type="email"
                            name="tenant_email" id="editFullEmail" class="form-control"></div>
                    <div class="mb-3"><label class="small text-muted">Teléfono / WhatsApp</label><input type="text"
                            name="tenant_fono" id="editFullFono" class="form-control"></div>
                    <button class="btn btn-warning w-100 fw-bold">Guardar Cambios</button>
                </form>
            </div>
        