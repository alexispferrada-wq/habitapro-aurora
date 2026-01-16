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

# Importar modelos para registrar el user_loader de Flask-Login
from app import models

# ==========================================
# 2. UTILIDADES DE APOYO
# ==========================================

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
    
def obtener_estado_parking_real(edificio_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM estacionamientos_visita WHERE edificio_id = %s ORDER BY id ASC", (edificio_id,))
    slots_db = cur.fetchall()
    
    cur.execute("""
        SELECT v.parking_id, v.patente, v.ingreso, u.numero as unidad
        FROM visitas v 
        LEFT JOIN unidades u ON v.unidad_id = u.id 
        WHERE v.edificio_id = %s AND v.salida IS NULL AND v.parking_id IS NOT NULL
    """, (edificio_id,))
    visitas_activas = {str(v['parking_id']): v for v in cur.fetchall()}
    
    mapa = []
    for slot in slots_db:
        s_id = str(slot['id'])
        estado_bd = str(slot['estado']).lower()
        estado_final = 'libre'
        patente, tiempo, unidad = None, '', ''

        if estado_bd == 'mantencion': estado_final = 'mantencion'
        elif s_id in visitas_activas:
            v = visitas_activas[s_id]
            estado_final = 'ocupado'
            patente, unidad = v['patente'], v['unidad']
            minutos = int((datetime.now() - v['ingreso']).total_seconds() / 60)
            h, m = divmod(minutos, 60)
            tiempo = f"{h}h {m}m"
        elif estado_bd == 'ocupado':
             estado_final = 'ocupado'
             patente = slot.get('patente', 'OCUPADO')

        mapa.append({'id': slot['id'], 'nombre': slot['nombre'], 'estado': estado_final, 'patente': patente, 'tiempo': tiempo, 'unidad_numero': unidad})
    cur.close(); conn.close()
    return mapa

# ==========================================
# 3. RUTAS DE PANELES PRINCIPALES
# ==========================================

@app.route('/panel-superadmin')
@login_required
def panel_superadmin():
    if current_user.rol != 'superadmin': return redirect(url_for('auth.login'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT version()"); db_ver = str(cur.fetchone()['version']).split(',')[0]
        db_info = {'status': "ONLINE", 'version': db_ver, 'color': 'text-success'}
    except: db_info = {'status': "OFFLINE", 'version': "Error", 'color': 'text-danger'}

    cur.execute("SELECT e.*, COUNT(u.rut) as cantidad_usuarios FROM edificios e LEFT JOIN usuarios u ON e.id = u.edificio_id AND u.activo = TRUE WHERE e.activo = TRUE GROUP BY e.id ORDER BY e.id ASC")
    edificios_stats = cur.fetchall()
    cur.close(); conn.close()
    return render_template('dash_super.html', stats={'edificios': len(edificios_stats), 'users': sum(e['cantidad_usuarios'] for e in edificios_stats)}, edificios=edificios_stats, db_info=db_info, indicadores=obtener_indicadores())

@app.route('/panel-admin')
@login_required
def panel_admin():
    if session.get('rol') != 'admin': return redirect(url_for('auth.home'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); ed = cur.fetchone()
    cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (eid,)); units = cur.fetchall()
    
    y, m = get_safe_date_params()
    nav = calcular_navegacion(m, y)
    
    cur.execute("SELECT SUM(monto) as t FROM gastos WHERE edificio_id=%s AND mes=%s AND anio=%s", (eid, m, y))
    tg = cur.fetchone()['t'] or 0
    cur.execute("SELECT SUM(monto) as t FROM historial_pagos WHERE edificio_id=%s AND ((mes_periodo=%s AND anio_periodo=%s) OR (mes_periodo IS NULL AND EXTRACT(MONTH FROM fecha)=%s AND EXTRACT(YEAR FROM fecha)=%s))", (eid, m, y, m, y))
    ti = cur.fetchone()['t'] or 0

    u_proc = []
    for u in units:
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'owner': parse_json_field(u.get('owner_json')), 'tenant': parse_json_field(u.get('tenant_json')), 'deuda_actual': u.get('deuda_monto', 0)})

    # Lógica del Calendario de Activos
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s", (eid,))
    activos = cur.fetchall()
    eventos = {}
    f_ini = date(y, m, 1)
    f_fin = date(y, m, calendar.monthrange(y, m)[1])
    
    for a in activos:
        if a.get('ultimo_servicio') and a.get('periodicidad_dias') and a['periodicidad_dias'] > 0:
            f = a['ultimo_servicio']
            p = a['periodicidad_dias']
            
            if f < f_ini:
                dias_diff = (f_ini - f).days
                ciclos = dias_diff // p
                f += timedelta(days=ciclos * p)
                if f < f_ini: f += timedelta(days=p)

            while f <= f_fin:
                if f >= f_ini: eventos.setdefault(f.day, []).append({'nombre': a['nombre'], 'costo': a.get('costo_estimado', 0)})
                f += timedelta(days=p)

    dias_restantes = 0
    alerta_deuda = None
    if ed and ed.get('deuda_omnisoft', 0) > 0 and ed.get('estado_pago') != 'PAGADO':
        if ed.get('deuda_vencimiento'):
            delta = ed['deuda_vencimiento'] - date.today()
            dias_restantes = delta.days
            if dias_restantes < 0: alerta_deuda = "VENCIDO"
            elif dias_restantes <= 3: alerta_deuda = "CRITICO"
            else: alerta_deuda = "NORMAL"

    calendario = calendar.monthcalendar(y, m)
    mes_nombre = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"][m]

    cur.close(); conn.close()
    return render_template('dash_admin.html', edificio=dict(ed), unidades=u_proc, finanzas={'ingresos': ti, 'egresos': tg, 'saldo': ti - tg}, mes_actual=mes_nombre, anio_actual=y, nav=nav, parking=obtener_estado_parking_real(eid), indicadores=obtener_indicadores(), dias_restantes=dias_restantes, alerta_deuda=alerta_deuda, calendario=calendario, eventos=eventos, m=m, y=y)

@app.route('/admin/parking/agregar', methods=['POST'])
@login_required
def admin_parking_agregar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    nombre = request.form.get('nombre', '').upper()
    eid = session.get('edificio_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO estacionamientos_visita (edificio_id, nombre) VALUES (%s, %s)", (eid, nombre))
        conn.commit()
    except: conn.rollback()
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/parking/eliminar', methods=['POST'])
@login_required
def admin_parking_eliminar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    pid = request.form.get('id')
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM estacionamientos_visita WHERE id = %s", (pid,))
        conn.commit()
    except: conn.rollback()
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/parking/maintenance', methods=['POST'])
@login_required
def parking_maintenance():
    if session.get('rol') != 'admin': return redirect(url_for('auth.home'))
    sid = request.form.get('slot_id') 
    accion = request.form.get('accion') 
    nuevo_estado = 'MANTENCION' if accion == 'activar' else 'LIBRE'
    conn = get_db_connection(); cur = conn.cursor()
    try: cur.execute("UPDATE estacionamientos_visita SET estado = %s WHERE id = %s", (nuevo_estado, sid)); conn.commit()
    except: conn.rollback()
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_admin'))

@app.route('/admin/activos')
@login_required
def admin_activos():
    if session.get('rol') != 'admin': return redirect(url_for('auth.home'))
    
    eid = session.get('edificio_id')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s ORDER BY nombre", (eid,))
    activos = cur.fetchall()
    cur.close()
    conn.close()

    gasto_mes = 0
    hoy = date.today()
    inicio_mes = date(hoy.year, hoy.month, 1)
    fin_mes = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])

    for a in activos:
        a['costo_mes'] = 0
        a['prox_fecha'] = 'N/A'
        a['dias_restantes'] = 0

        if a.get('ultimo_servicio') and a.get('periodicidad_dias') and a['periodicidad_dias'] > 0:
            periodo = a['periodicidad_dias']
            costo = a.get('costo_estimado') or 0
            ultimo = a['ultimo_servicio']

            temp_date = ultimo
            if temp_date < inicio_mes:
                dias_diff = (inicio_mes - temp_date).days
                ciclos = dias_diff // periodo
                temp_date += timedelta(days=ciclos * periodo)
                if temp_date < inicio_mes: temp_date += timedelta(days=periodo)
            
            while temp_date <= fin_mes:
                if temp_date >= inicio_mes: a['costo_mes'] += costo
                temp_date += timedelta(days=periodo)
            gasto_mes += a['costo_mes']

            prox = ultimo
            if prox < hoy:
                dias_diff = (hoy - prox).days
                ciclos = dias_diff // periodo
                prox += timedelta(days=ciclos * periodo)
                if prox < hoy: prox += timedelta(days=periodo)
            
            a['prox_fecha'] = prox.strftime('%d/%m/%Y')
            a['dias_restantes'] = (prox - hoy).days

    return render_template('admin_activos.html', activos=activos, gasto_mes=gasto_mes)

@app.route('/admin/activos/guardar', methods=['POST'])
@login_required
def guardar_activo():
    if session.get('rol') != 'admin': return redirect(url_for('auth.home'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO activos (edificio_id, nombre, periodicidad_dias, costo_estimado, ultimo_servicio) VALUES (%s, %s, %s, %s, %s)", (session.get('edificio_id'), request.form.get('nombre'), request.form.get('periodicidad'), request.form.get('costo'), request.form.get('ultimo_servicio')))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin_activos'))

@app.route('/admin/activos/eliminar/<int:id>')
@login_required
def eliminar_activo(id):
    if session.get('rol') != 'admin': return redirect(url_for('auth.home'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM activos WHERE id=%s", (id,))
    conn.commit(); cur.close(); conn.close()
    return redirect(url_for('admin_activos'))

@app.route('/panel-conserje')
@login_required
def panel_conserje():
    if session.get('rol') != 'conserje': return redirect(url_for('auth.home'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); edificio = cur.fetchone()
    cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,))
    encomiendas = cur.fetchall()
    cur.execute("SELECT id, numero, owner_json, tenant_json, deuda_monto FROM unidades WHERE edificio_id = %s ORDER BY LENGTH(numero), numero ASC", (eid,))
    raw_unidades = cur.fetchall()

    unidades = []
    for u in raw_unidades:
        unidades.append({
            'id': u['id'], 'numero': u['numero'],
            'owner': parse_json_field(u.get('owner_json')),
            'tenant': parse_json_field(u.get('tenant_json')),
            'deuda_actual': u.get('deuda_monto', 0)
        })
    cur.close(); conn.close()
    return render_template('dash_conserje.html', edificio=dict(edificio), parking=obtener_estado_parking_real(eid), encomiendas=encomiendas, unidades=unidades)

@app.route('/conserje/parking/toggle', methods=['POST'])
@login_required
def conserje_parking_toggle():
    if session.get('rol') != 'conserje': return jsonify({'status': 'error', 'message': 'No autorizado'})
    
    sid = request.form.get('slot_id')
    acc = request.form.get('accion')
    pat = request.form.get('patente', 'VISITA').upper()
    uid = request.form.get('unidad_id')
    eid = session.get('edificio_id')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if acc == 'ocupar':
            # 1. Registrar la visita (uid puede ser None si no se seleccionó unidad)
            cur.execute("""
                INSERT INTO visitas (edificio_id, unidad_id, patente, estacionamiento_id, parking_id, ingreso) 
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (eid, uid if uid else None, pat, sid, sid))
            
            # 2. Marcar el parking como OCUPADO
            cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s", (pat, sid))

        else: # Acción: LIBERAR
            cur.execute("""
                UPDATE visitas SET salida = NOW() 
                WHERE edificio_id = %s AND (parking_id = %s OR estacionamiento_id = %s) AND salida IS NULL
            """, (eid, int(sid), sid))

            cur.execute("UPDATE estacionamientos_visita SET estado = 'libre', patente = NULL WHERE id = %s", (sid,))

        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)})
    finally:
        cur.close(); conn.close()

@app.route('/conserje/reservas/cambiar_estado', methods=['POST'])
@login_required
def conserje_reservas_estado():
    if session.get('rol') != 'conserje': return jsonify({'status': 'error'})
    
    rid = request.form.get('reserva_id')
    nuevo_estado = request.form.get('nuevo_estado') # EN_USO, FINALIZADA, ENTREGADO
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("UPDATE reservas SET estado = %s WHERE id = %s", (nuevo_estado, rid))
        conn.commit()
        return jsonify({'status': 'success'})
    except Exception as e: conn.rollback(); return jsonify({'status': 'error', 'message': str(e)})
    finally: cur.close(); conn.close()

@app.route('/conserje/turno/guardar', methods=['POST'])
@login_required
def conserje_guardar_turno():
    if session.get('rol') != 'conserje': return redirect(url_for('auth.home'))
    
    novedades = request.form.get('novedades')
    caja = request.form.get('caja', 0)
    eid = session.get('edificio_id')
    nombre = session.get('nombre')
    
    detalle = f"ENTREGA DE TURNO\nConserje: {nombre}\nCaja: ${'{:,.0f}'.format(int(caja)).replace(',', '.')}\nNovedades: {novedades}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor) VALUES (%s, %s, %s, NOW(), %s)", (eid, "ENTREGA DE TURNO", detalle, nombre))
        conn.commit()
        flash("✅ Turno finalizado y registrado en bitácora.")
    except Exception as e: conn.rollback(); flash(f"Error: {e}")
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_conserje'))

@app.route('/conserje/encomiendas/guardar', methods=['POST'])
@login_required
def conserje_guardar_encomienda():
    if session.get('rol') != 'conserje': return redirect(url_for('auth.home'))
    
    eid = session.get('edificio_id')
    uid = request.form.get('unidad_id')
    remitente = request.form.get('remitente')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO encomiendas (edificio_id, unidad_id, remitente, recepcion) VALUES (%s, %s, %s, NOW())", (eid, uid, remitente))
        conn.commit()
        flash("✅ Encomienda registrada.")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {e}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('panel_conserje'))

@app.route('/conserje/encomiendas/entregar', methods=['POST'])
@login_required
def conserje_entregar_encomienda():
    if session.get('rol') != 'conserje': return redirect(url_for('auth.home'))
    
    encomienda_id = request.form.get('encomienda_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("UPDATE encomiendas SET entrega = NOW() WHERE id = %s", (encomienda_id,))
        conn.commit()
        flash("✅ Encomienda entregada.")
    except Exception as e:
        conn.rollback()
        flash(f"Error: {e}")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('panel_conserje'))

@app.route('/panel-residente')
@login_required
def panel_residente():
    if session.get('rol') != 'residente' or 'unidad_id_residente' not in session:
        return redirect(url_for('auth.logout'))
    uid = session.get('unidad_id_residente'); eid = session.get('edificio_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT * FROM unidades WHERE id = %s", (uid,)); raw_u = cur.fetchone()
    u = dict(raw_u) # Convert RealDictRow to dict
    u['owner'] = parse_json_field(raw_u.get('owner_json'))
    u['tenant'] = parse_json_field(raw_u.get('tenant_json'))

    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); raw_edificio = cur.fetchone()
    edificio = dict(raw_edificio) # Convert RealDictRow to dict

    cur.execute("SELECT * FROM encomiendas WHERE unidad_id = %s AND entrega IS NULL ORDER BY recepcion DESC", (uid,))
    encomiendas = cur.fetchall()
    cur.execute("SELECT v.*, p.nombre as parking_nombre FROM visitas v LEFT JOIN estacionamientos_visita p ON v.parking_id = p.id WHERE v.unidad_id = %s AND v.salida IS NULL ORDER BY ingreso DESC", (uid,))
    visitas = cur.fetchall()
    cur.close(); conn.close()
    return render_template('dash_residente.html', u=u, edificio=edificio, encomiendas=encomiendas, visitas_activas=visitas, hoy=date.today())

# ==========================================
# 4. RUTAS DE SISTEMA
# ==========================================

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