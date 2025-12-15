from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import json
import random
import os
import io
import csv
import calendar 
from dotenv import load_dotenv
from datetime import datetime, timedelta, date
from database import get_db_connection, inicializar_tablas

app = Flask(__name__)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'clave_dev_por_defecto')
app.config['SESSION_COOKIE_NAME'] = 'aexon_session'

MANTENCION_DB = set(['E-115', 'E-118']) 
PASSWORD_TEMP_DB = {} 

# Inicializar tablas al arrancar
with app.app_context():
    inicializar_tablas()

# --- UTILIDADES ---
def parse_json_field(field_data):
    if isinstance(field_data, dict): return field_data
    try: return json.loads(field_data or '{}')
    except: return {}

def get_safe_date_params():
    try:
        y_str = request.args.get('year', '')
        m_str = request.args.get('month', '')
        now = datetime.now()
        y = int(y_str) if y_str and y_str.isdigit() else now.year
        m = int(m_str) if m_str and m_str.isdigit() else now.month
        if m < 1: m = 1
        if m > 12: m = 12
        if y < 2000: y = 2000
        if y > 2100: y = 2100
        return y, m
    except:
        now = datetime.now()
        return now.year, now.month

def calcular_navegacion(m, y):
    return {
        'prev_m': 12 if m == 1 else m - 1,
        'prev_y': y - 1 if m == 1 else y,
        'next_m': 1 if m == 12 else m + 1,
        'next_y': y + 1 if m == 12 else y
    }

def formatear_rut(rut_raw):
    """
    Estandariza el RUT: Sin puntos, con guion, mayúsculas.
    Ej: 1.111.111-1 -> 11111111-1
    """
    if not rut_raw: return ""
    limpio = str(rut_raw).replace(".", "").replace(" ", "").strip().upper()
    # Agregar guion si falta y parece un RUT largo (evitar romper 'admin')
    if "-" not in limpio and len(limpio) > 3: # Solo si tiene más de 3 caracteres
        limpio = limpio[:-1] + "-" + limpio[-1]
    return limpio

# --- RUTAS PRINCIPALES ---
@app.route('/')
def home():
    if 'user_id' in session: 
        role = session.get('rol')
        if role == 'superadmin': return redirect(url_for('panel_superadmin'))
        elif role == 'admin': return redirect(url_for('panel_admin'))
        elif role == 'conserje': return redirect(url_for('panel_conserje'))
        elif role == 'residente': return redirect(url_for('panel_residente'))
        else: session.clear(); return redirect(url_for('home'))
    return render_template('login.html')

# --- LOGIN BLINDADO (CORREGIDO) ---
@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.json
        rut_input = data.get('rut', '').strip()
        password = data.get('password', '').strip()
        
        # 1. Versión Limpia del RUT (para residentes)
        rut_clean = formatear_rut(rut_input)
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # ESTRATEGIA DOBLE CHECK:
        # A) Buscar EXACTO (para 'admin', 'superadmin' o RUTs bien escritos)
        cur.execute("SELECT * FROM usuarios WHERE rut = %s", (rut_input,))
        user = cur.fetchone()
        
        # B) Si no existe, buscar versión LIMPIA (para residentes con puntos)
        if not user:
            cur.execute("SELECT * FROM usuarios WHERE rut = %s", (rut_clean,))
            user = cur.fetchone()
            # Si lo encontramos así, actualizamos la variable local para usarla después
            if user: rut_input = user['rut']

        if user and user['password'] == password:
            if user.get('activo') is False: 
                cur.close(); conn.close()
                return jsonify({"status": "error", "message": "🚫 Cuenta Bloqueada"})
            
            # Validar Edificio Activo (Excepto SuperAdmin)
            if user['rol'] != 'superadmin':
                cur.execute("SELECT activo FROM edificios WHERE id = %s", (user['edificio_id'],))
                ed_status = cur.fetchone()
                if not ed_status or ed_status['activo'] is False:
                    cur.close(); conn.close()
                    return jsonify({"status": "error", "message": "🚫 Edificio Inactivo"})

            # Crear Sesión
            session['user_id'] = user['rut']
            session['rol'] = user['rol']
            session['nombre'] = user['nombre']
            session['edificio_id'] = user['edificio_id']
            
            # Lógica Residente: Buscar unidad
            if user['rol'] == 'residente':
                # Buscamos coincidencias en el JSON usando el RUT LIMPIO (que es el estándar)
                query_unidad = """
                    SELECT id FROM unidades 
                    WHERE edificio_id = %s 
                    AND (owner_json::text ILIKE %s OR tenant_json::text ILIKE %s)
                    LIMIT 1
                """
                # Usamos % para buscar el RUT dentro del string JSON
                cur.execute(query_unidad, (user['edificio_id'], f'%{rut_clean}%', f'%{rut_clean}%'))
                u_data = cur.fetchone()
                
                if u_data:
                    session['unidad_id_residente'] = u_data['id']
                else:
                    # Fallback: Si no encuentra por RUT limpio, intentar con el input original
                    cur.execute(query_unidad, (user['edificio_id'], f'%{rut_input}%', f'%{rut_input}%'))
                    u_data_fallback = cur.fetchone()
                    if u_data_fallback:
                        session['unidad_id_residente'] = u_data_fallback['id']
                    else:
                        print(f"⚠️ Residente {user['rut']} sin unidad asignada.")
            
            cur.close(); conn.close()
            return jsonify({"status": "success", "redirect_url": url_for('home')})
        
        cur.close(); conn.close()
        return jsonify({"status": "error", "message": "Credenciales incorrectas"})
        
    except Exception as e:
        print(f"Login Error: {e}")
        return jsonify({"status": "error", "message": "Error interno"})

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('home'))

# --- UTILIDADES PARKING ---
def obtener_estado_parking_real(edificio_id):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT v.id, v.estacionamiento_id, v.patente, v.ingreso, u.numero as unidad_numero FROM visitas v LEFT JOIN unidades u ON v.unidad_id = u.id WHERE v.edificio_id = %s AND v.salida IS NULL AND v.estacionamiento_id IS NOT NULL", (edificio_id,))
    visitas = cur.fetchall(); ocupados = {v['estacionamiento_id']: v for v in visitas}
    cur.execute("SELECT id, numero, estacionamiento FROM unidades WHERE edificio_id = %s AND estacionamiento IS NOT NULL AND estacionamiento != ''", (edificio_id,))
    asignados = {u['estacionamiento']: u for u in cur.fetchall()}
    mapa = []
    for i in range(1, 21):
        sid = f"E-{100+i}"; est = 'libre'; pat = None; t = ''; unum = ''
        if sid in MANTENCION_DB: est = 'mantencion'
        elif sid in ocupados:
            v = ocupados[sid]; pat = v['patente']; unum = v['unidad_numero']; est = 'ocupado'
            h, m = divmod(int((datetime.now() - v['ingreso']).total_seconds()/60), 60); t = f"{h}h {m}m"
        elif sid in asignados: est = 'asignado'; unum = asignados[sid]['numero']
        mapa.append({'id': sid, 'nombre': sid, 'estado': est, 'patente': pat, 'tiempo': t, 'unidad_numero': unum})
    cur.close(); conn.close(); return mapa

# ==========================================
# RUTAS CONSERJE
# ==========================================
@app.route('/panel-conserje')
def panel_conserje():
    if session.get('rol') != 'conserje': return redirect(url_for('home'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); edificio = cur.fetchone()
    cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,))
    encomiendas = cur.fetchall()
    
    # --- NUEVO: RESERVAS FUTURAS PARA CONSERJERÍA ---
    cur.execute("""
        SELECT r.fecha_uso, e.nombre as nombre_espacio, u.numero as numero_unidad 
        FROM reservas r 
        JOIN espacios e ON r.espacio_id = e.id 
        JOIN unidades u ON r.unidad_id = u.id 
        WHERE e.edificio_id = %s AND r.fecha_uso >= CURRENT_DATE AND r.estado = 'CONFIRMADA'
        ORDER BY r.fecha_uso ASC LIMIT 10
    """, (eid,))
    reservas_futuras = cur.fetchall()
    
    cur.execute("SELECT id, numero, owner_json, tenant_json FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (eid,)); raw = cur.fetchall()
    u_proc = []
    for u in raw:
        o = parse_json_field(u.get('owner_json')); t = parse_json_field(u.get('tenant_json'))
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'residente': t.get('nombre') or o.get('nombre','S/D'), 'fono': t.get('fono') or o.get('fono','')})
    
    cur.close(); conn.close()
    return render_template('dash_conserje.html', edificio=dict(edificio), parking=obtener_estado_parking_real(eid), encomiendas=encomiendas, unidades=u_proc, reservas_futuras=reservas_futuras)

@app.route('/conserje/visitas/validar_qr', methods=['POST'])
def conserje_validar_qr():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT i.*, u.numero FROM invitaciones i JOIN unidades u ON i.unidad_id = u.id WHERE i.token = %s AND i.estado = 'LISTO'", (request.form.get('codigo_qr'),))
    inv = cur.fetchone()
    if inv:
        cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],)); conn.commit(); cur.close(); conn.close()
        return jsonify({'status': 'success', 'visita': inv['nombre_visita'], 'unidad': inv['numero'], 'patente': inv['patente'] or 'A Pie'})
    cur.close(); conn.close(); return jsonify({'status': 'error', 'message': 'QR Inválido'})

@app.route('/conserje/parking/toggle', methods=['POST'])
def conserje_parking_toggle():
    sid = request.form.get('slot_id'); acc = request.form.get('accion'); pat = request.form.get('patente','VISITA').upper(); eid = session.get('edificio_id')
    if sid in MANTENCION_DB: return jsonify({'status': 'error', 'message': 'Bloqueado por Admin'})
    conn = get_db_connection(); cur = conn.cursor()
    if acc == 'ocupar': cur.execute("INSERT INTO visitas (edificio_id, patente, estacionamiento_id, ingreso) VALUES (%s, %s, %s, NOW())", (eid, pat, sid))
    else: cur.execute("UPDATE visitas SET salida = NOW() WHERE edificio_id = %s AND estacionamiento_id = %s AND salida IS NULL", (eid, sid))
    conn.commit(); cur.close(); conn.close(); return jsonify({'status': 'success'})

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
@app.route('/manifest.json')
def manifest(): return jsonify({"name": "HabitaPro", "short_name": "HabitaPro", "start_url": "/panel-residente", "display": "standalone", "background_color": "#1a1d24", "theme_color": "#0dcaf0", "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/555/555545.png", "sizes": "192x192", "type": "image/png"}]})

@app.route('/panel-residente')
def panel_residente():
    if session.get('rol') != 'residente': return redirect(url_for('home'))
    
    uid = session.get('unidad_id_residente')
    eid = session.get('edificio_id')
    
    conn = get_db_connection(); cur = conn.cursor()
    
    # 1. Datos Unidad y Usuario
    cur.execute("SELECT * FROM unidades WHERE id = %s", (uid,)); u = cur.fetchone()
    cur.execute("SELECT * FROM usuarios WHERE rut = %s", (session.get('user_id'),)); usr = cur.fetchone()
    
    # 2. Datos Edificio (NUEVO)
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); edificio = cur.fetchone()
    
    # 3. Encomiendas
    cur.execute("SELECT * FROM encomiendas WHERE unidad_id = %s AND entrega IS NULL", (uid,)); enc = cur.fetchall()
    
    # 4. Reservas y Espacios
    hoy = date.today().strftime('%Y-%m-%d')
    cur.execute("SELECT * FROM espacios WHERE edificio_id = %s AND activo = TRUE", (eid,)); espacios = cur.fetchall()
    cur.execute("SELECT r.*, e.nombre as nombre_espacio FROM reservas r JOIN espacios e ON r.espacio_id = e.id WHERE r.unidad_id = %s AND r.fecha_uso >= %s AND r.estado = 'CONFIRMADA' ORDER BY r.fecha_uso ASC", (uid, hoy))
    mis_reservas = cur.fetchall()
    
    cur.close(); conn.close()
    
    return render_template('dash_residente.html', 
        u=u, user=usr, edificio=dict(edificio), 
        encomiendas=enc, espacios=espacios, mis_reservas=mis_reservas, hoy=hoy
    )


@app.route('/residente/perfil/editar', methods=['POST'])
def residente_editar_perfil():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("UPDATE usuarios SET email=%s, telefono=%s WHERE rut=%s", (request.form.get('email'), request.form.get('telefono'), session.get('user_id'))); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_residente'))

@app.route('/residente/pagar_deuda', methods=['POST'])
def residente_pagar_deuda():
    uid = request.form.get('unidad_id'); m = int(request.form.get('monto')); eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT mes, anio FROM cierres_mes WHERE edificio_id = %s ORDER BY anio DESC, mes DESC LIMIT 1", (eid,)); uc = cur.fetchone()
    if uc: mp, ap = (uc['mes']+1, uc['anio']) if uc['mes'] < 12 else (1, uc['anio']+1)
    else: now = datetime.now(); mp, ap = now.month, now.year
    cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (m, uid))
    cur.execute("INSERT INTO historial_pagos (edificio_id, unidad_id, monto, metodo, comprobante_url, mes_periodo, anio_periodo) VALUES (%s, %s, %s, 'APP', 'N/A', %s, %s)", (eid, uid, m, mp, ap))
    conn.commit(); cur.close(); conn.close(); flash("Pago exitoso"); return redirect(url_for('panel_residente'))

@app.route('/residente/invitar/generar', methods=['POST'])
def generar_link_invitacion():
    t = f"{random.randint(100000,999999)}{session.get('unidad_id_residente')}"
    tipo = request.form.get('tipo', 'PEATON') # PEATON o VEHICULO
    nombre = request.form.get('nombre', '')   # Nombre pre-llenado por el residente
    
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO invitaciones (token, edificio_id, unidad_id, estado, tipo, pre_nombre) 
        VALUES (%s, %s, %s, 'PENDIENTE', %s, %s)
    """, (t, session.get('edificio_id'), session.get('unidad_id_residente'), tipo, nombre))
    conn.commit(); cur.close(); conn.close()
    
    return jsonify({'status': 'success', 'link': url_for('vista_invitado_form', token=t, _external=True)})


@app.route('/invitacion/<token>')
def vista_invitado_form(token):
    conn = get_db_connection(); cur = conn.cursor()
    query = """
        SELECT i.*, u.numero, u.owner_json, u.tenant_json, e.direccion, e.nombre as nombre_edificio 
        FROM invitaciones i 
        JOIN unidades u ON i.unidad_id = u.id 
        JOIN edificios e ON i.edificio_id = e.id 
        WHERE i.token = %s AND i.estado = 'PENDIENTE'
    """
    cur.execute(query, (token,)); invitacion = cur.fetchone()
    cur.close(); conn.close()
    
    if not invitacion: return "<h1>Invitación inválida, expirada o ya utilizada.</h1>"
    
    # Determinar anfitrión
    o = parse_json_field(invitacion.get('owner_json')); t = parse_json_field(invitacion.get('tenant_json'))
    anfitrion = t.get('nombre') if t.get('nombre') else o.get('nombre', 'Residente')
    
    return render_template('public_visita.html', 
        token=token, 
        anfitrion=anfitrion, 
        depto=invitacion['numero'], 
        direccion=invitacion['direccion'], 
        edificio=invitacion['nombre_edificio'],
        tipo=invitacion.get('tipo', 'PEATON'),      # Importante: Tipo de invitación
        pre_nombre=invitacion.get('pre_nombre', '') # Importante: Nombre pre-llenado
    )


@app.route('/invitacion/guardar', methods=['POST'])
def guardar_datos_invitado():
    t = request.form.get('token'); n = request.form.get('nombre'); conn = get_db_connection(); cur = conn.cursor()
    r = formatear_rut(request.form.get('rut'))
    cur.execute("UPDATE invitaciones SET nombre_visita=%s, rut_visita=%s, patente=%s, estado='LISTO' WHERE token=%s RETURNING id", (n, r, request.form.get('patente'), t)); res = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return render_template('public_qr_exito.html', qr_content=t, nombre=n) if res else "Error"

# ==========================================
# PANEL ADMIN
# ==========================================
@app.route('/panel-admin')
def panel_admin():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    
    cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,)); ed = cur.fetchone()
    cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (eid,)); units = cur.fetchall()
    deuda_aexon = ed.get('deuda_omnisoft', 0)
    
    y, m = get_safe_date_params()
    mes_nombre = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"][m]
    nav = calcular_navegacion(m, y)
    
    cur.execute("SELECT SUM(monto) as t FROM gastos WHERE edificio_id=%s AND mes=%s AND anio=%s", (eid, m, y)); g = cur.fetchone(); tg = g['t'] if g and g['t'] else 0
    cur.execute("""SELECT SUM(monto) as t FROM historial_pagos WHERE edificio_id=%s AND ((mes_periodo=%s AND anio_periodo=%s) OR (mes_periodo IS NULL AND EXTRACT(MONTH FROM fecha)=%s AND EXTRACT(YEAR FROM fecha)=%s))""", (eid, m, y, m, y)); i = cur.fetchone(); ti = i['t'] if i and i['t'] else 0
    fin = {'ingresos': ti, 'egresos': tg, 'saldo': ti - tg}
    
    cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,)); enc = cur.fetchall()
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s", (eid,)); activos = cur.fetchall(); eventos = {}
    f_ini = date(y, m, 1); f_fin = date(y, m, calendar.monthrange(y, m)[1])
    for a in activos:
        if a['ultimo_servicio']:
            f = a['ultimo_servicio']; 
            while f <= f_fin:
                if f >= f_ini: eventos.setdefault(f.day, []).append({'nombre': a['nombre'], 'costo': a['costo_estimado']})
                f += timedelta(days=a['periodicidad_dias'])
    
    # --- CARGAR DATOS DE ESPACIOS Y RESERVAS ---
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
    
    cur.close(); conn.close()
    
    u_proc = []
    for u in units:
        o = parse_json_field(u.get('owner_json')); t = parse_json_field(u.get('tenant_json'))
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'propietario': o.get('nombre'), 'owner': o, 'tenant': t, 'deuda_actual': u.get('deuda_monto', 0), 'metraje': u['metraje'], 'piso': u['piso'], 'prorrateo': u['prorrateo']})
        
    return render_template('dash_admin.html', edificio=dict(ed), unidades=u_proc, finanzas=fin, stats={'unidades': len(u_proc)}, calendario=calendar.monthcalendar(y, m), eventos=eventos, mes_actual=mes_nombre, anio_actual=y, nav=nav, encomiendas=enc, parking=obtener_estado_parking_real(eid), deuda_aexon=deuda_aexon, espacios=espacios, reservas=reservas, whatsapp_soporte="56912345678", new_credentials=session.pop('new_credentials_unidad', None))

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
        rut = formatear_rut(request.form.get('rut'))
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO usuarios (rut, nombre, email, telefono, rol, password, edificio_id, activo) VALUES (%s, %s, %s, %s, 'conserje', %s, %s, TRUE) ON CONFLICT (rut) DO NOTHING RETURNING rut", (rut, request.form.get('nombre'), request.form.get('email'), '', new_pass, session.get('edificio_id')))
        msg = 'success' if cur.fetchone() else 'existe'; conn.commit(); cur.close(); conn.close(); return jsonify({'status': msg, 'password': new_pass})
    except: return jsonify({'status': 'error'})

@app.route('/admin/conserjes/eliminar', methods=['POST'])
def eliminar_conserje():
    conn = get_db_connection(); cur = conn.cursor(); cur.execute("DELETE FROM usuarios WHERE rut = %s", (request.form.get('rut'),)); conn.commit(); cur.close(); conn.close(); return jsonify({'status': 'success'})

@app.route('/admin/conserjes/reset_clave', methods=['POST'])
def reset_clave_conserje():
    np = f"Conserje{random.randint(1000,9999)}"; conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE usuarios SET password = %s WHERE rut = %s", (np, request.form.get('rut'))); conn.commit(); cur.close(); conn.close(); return jsonify({'status': 'success', 'password': np})

@app.route('/admin/residentes/registrar_pago', methods=['POST'])
def registrar_pago_residente():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    uid = request.form.get('unidad_id'); m_str = request.form.get('monto_pago')
    if not uid or not m_str: flash("Datos incompletos"); return redirect(url_for('panel_admin'))
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT mes, anio FROM cierres_mes WHERE edificio_id = %s ORDER BY anio DESC, mes DESC LIMIT 1", (eid,)); uc = cur.fetchone()
    if uc: mp, ap = (uc['mes']+1, uc['anio']) if uc['mes'] < 12 else (1, uc['anio']+1)
    else: now = datetime.now(); mp, ap = now.month, now.year
    cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (int(m_str), uid))
    cur.execute("INSERT INTO historial_pagos (edificio_id, unidad_id, monto, metodo, comprobante_url, mes_periodo, anio_periodo) VALUES (%s, %s, %s, 'TRANSFERENCIA', 'manual', %s, %s)", (eid, uid, int(m_str), mp, ap))
    conn.commit(); cur.close(); conn.close(); flash("Pago registrado"); return redirect(url_for('panel_admin'))

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

@app.route('/admin/parking/maintenance', methods=['POST'])
def parking_maintenance():
    if session.get('rol') != 'admin': return redirect(url_for('home'))
    sid = request.form.get('slot_id'); acc = request.form.get('accion')
    if acc == 'activar': MANTENCION_DB.add(sid)
    else: MANTENCION_DB.discard(sid)
    return redirect(url_for('panel_admin'))

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
    eid = session.get('edificio_id'); conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM activos WHERE edificio_id = %s ORDER BY nombre", (eid,)); activos = cur.fetchall()
    gasto_mes = 0; hoy = date.today()
    for a in activos:
        if a['ultimo_servicio']:
            prox = a['ultimo_servicio']; 
            while prox < hoy: prox += timedelta(days=a['periodicidad_dias'])
            a['prox_fecha'] = prox.strftime('%d/%m/%Y'); a['dias_restantes'] = (prox - hoy).days
            if prox.month == hoy.month and prox.year == hoy.year: gasto_mes += a['costo_estimado']
    cur.close(); conn.close()
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
        """, (rut_limpio, nombre, target.get('email',''), target.get('fono',''), new_pass, eid, new_pass, eid))
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
    espacio_id = request.form.get('espacio_id'); fecha = request.form.get('fecha')
    
    conn = get_db_connection(); cur = conn.cursor()
    try:
        # Validar si ya está ocupado
        cur.execute("SELECT id FROM reservas WHERE espacio_id=%s AND fecha_uso=%s AND estado='CONFIRMADA'", (espacio_id, fecha))
        if cur.fetchone(): flash("⛔ Fecha no disponible."); return redirect(url_for('panel_residente'))

        # Obtener precio
        cur.execute("SELECT precio, nombre FROM espacios WHERE id=%s", (espacio_id,))
        espacio = cur.fetchone()
        
        # Crear Reserva
        cur.execute("INSERT INTO reservas (espacio_id, unidad_id, fecha_uso) VALUES (%s, %s, %s)", (espacio_id, uid, fecha))
        
        # Cargar Deuda Automática
        if espacio['precio'] > 0:
            cur.execute("UPDATE unidades SET deuda_monto = deuda_monto + %s WHERE id = %s", (espacio['precio'], uid))
            
        conn.commit(); flash(f"✅ Reserva Confirmada: {espacio['nombre']} (${espacio['precio']})")
    except Exception as e: print(e); flash("❌ Error al reservar")
    finally: cur.close(); conn.close()
    return redirect(url_for('panel_residente'))

# --- MÓDULO SUPER ADMIN (QUE FALTABA) ---
@app.route('/panel-superadmin')
def panel_superadmin():
    if session.get('rol') != 'superadmin': return redirect(url_for('home'))
    conn = get_db_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT version()")
        db_ver = cur.fetchone()['version'].split(',')[0]
        db_info = {'status': "ONLINE", 'version': db_ver, 'color': 'text-success'}
    except:
        db_info = {'status': "OFFLINE", 'version': "Error", 'color': 'text-danger'}

    cur.execute("SELECT e.*, COUNT(u.rut) as cantidad_usuarios FROM edificios e LEFT JOIN usuarios u ON e.id = u.edificio_id AND u.activo = TRUE WHERE e.activo = TRUE GROUP BY e.id ORDER BY e.id ASC")
    edificios_stats = cur.fetchall()
    total_edificios = len(edificios_stats)
    total_usuarios = sum(e['cantidad_usuarios'] for e in edificios_stats)

    query_logs = "((SELECT 'VISITA' as tipo, v.ingreso as fecha, e.nombre as edificio, CONCAT('Visita: ', v.nombre_visita) as detalle, 'Conserjería' as actor FROM visitas v JOIN edificios e ON v.edificio_id = e.id) UNION ALL (SELECT 'INCIDENCIA', i.fecha, e.nombre, i.titulo as detalle, i.autor as actor FROM incidencias i JOIN edificios e ON i.edificio_id = e.id) UNION ALL (SELECT 'PAGO', h.fecha, e.nombre, CONCAT('Pago GC: $', h.monto) as detalle, 'App' as actor FROM historial_pagos h JOIN edificios e ON h.edificio_id = e.id) UNION ALL (SELECT 'ENCOMIENDA', enc.recepcion, e.nombre, CONCAT('Paquete: ', enc.remitente) as detalle, 'Conserjería' as actor FROM encomiendas enc JOIN edificios e ON enc.edificio_id = e.id)) ORDER BY fecha DESC LIMIT 30"
    cur.execute(query_logs); logs = cur.fetchall()
    logs_proc = []
    for l in logs:
        delta = datetime.now() - l['fecha']
        tiempo = f"Hace {delta.seconds//60}m" if delta.days == 0 else f"Hace {delta.days}d"
        logs_proc.append({'tipo': l['tipo'], 'fecha_full': l['fecha'].strftime('%d/%m %H:%M'), 'tiempo': tiempo, 'edificio': l['edificio'], 'detalle': l['detalle'], 'actor': l['actor']})

    cur.close(); conn.close()
    return render_template('dash_super.html', stats={'edificios': total_edificios, 'users': total_usuarios}, edificios=edificios_stats, global_logs=logs_proc, db_info=db_info)

@app.route('/superadmin/detalle_edificio/<int:id>')
def super_detalle_edificio(id):
    if session.get('rol') != 'superadmin': return redirect(url_for('home'))
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("SELECT * FROM edificios WHERE id = %s", (id,)); ed = cur.fetchone()
    # TRAER TODOS LOS ADMINS
    cur.execute("SELECT * FROM usuarios WHERE edificio_id = %s AND rol = 'admin' AND activo = TRUE", (id,))
    admins = cur.fetchall()
    cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (id,)); units = cur.fetchall()
    conn.close()
    ed_full = dict(ed)
    u_proc = []
    for u in units:
        o = parse_json_field(u.get('owner_json')); t = parse_json_field(u.get('tenant_json'))
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'owner': o, 'tenant': t, 'metraje': u['metraje'], 'prorrateo': u['prorrateo'], 'estado_deuda': u.get('estado_deuda', 'AL_DIA')})
    return render_template('super_detalle_edificio.html', e=ed_full, admins=admins, unidades=u_proc)

@app.route('/superadmin/enviar_cobro', methods=['POST'])
def enviar_cobro():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE edificios SET deuda_omnisoft = %s, estado_pago = 'PENDIENTE' WHERE id = %s", (request.form.get('monto'), request.form.get('edificio_id')))
    conn.commit(); cur.close(); conn.close(); flash("Cobro generado"); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))

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
def crear_admin_rapido():
    r = formatear_rut(request.form.get('rut')); ed = request.form.get('edificio_id'); p = f"Habita{random.randint(1000,9999)}$"
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("INSERT INTO usuarios (rut, nombre, email, password, rol, edificio_id, activo) VALUES (%s, %s, %s, %s, 'admin', %s, TRUE) ON CONFLICT (rut) DO UPDATE SET rol='admin', edificio_id=%s, activo=TRUE", (r, request.form.get('nombre'), request.form.get('email'), p, ed, ed))
    conn.commit(); cur.close(); conn.close()
    flash(f"NEW_ADMIN_PASS:{p}")
    return redirect(url_for('super_detalle_edificio', id=ed))

@app.route('/superadmin/editar_admin', methods=['POST'])
def superadmin_editar_admin():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE usuarios SET nombre=%s, email=%s, telefono=%s WHERE rut=%s", (request.form.get('nombre'), request.form.get('email'), request.form.get('telefono'), request.form.get('rut')))
    conn.commit(); cur.close(); conn.close(); flash("Datos actualizados"); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))

@app.route('/superadmin/reset_pass_admin', methods=['POST'])
def reset_pass_admin():
    np = f"Reset{random.randint(1000,9999)}$"; conn = get_db_connection(); cur = conn.cursor()
    cur.execute("UPDATE usuarios SET password=%s WHERE rut=%s", (np, request.form.get('rut_admin')))
    conn.commit(); cur.close(); conn.close(); flash(f"NEW_ADMIN_PASS:{np}"); return redirect(url_for('super_detalle_edificio', id=request.form.get('edificio_id')))

@app.route('/superadmin/crear_edificio', methods=['POST'])
def crear_edificio():
    conn=get_db_connection(); cur=conn.cursor(); cur.execute("INSERT INTO edificios (nombre,direccion,activo) VALUES (%s,%s,TRUE)",(request.form.get('nombre').upper(),request.form.get('direccion'))); conn.commit(); cur.close(); conn.close(); return redirect(url_for('panel_superadmin'))

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
        o = json.dumps({"rut": rut_demo, "nombre": "Residente Test", "email": "test@habita.cl"})
        cur.execute("UPDATE unidades SET owner_json = %s WHERE id = %s", (o, uid))
        cur.execute("INSERT INTO usuarios (rut, nombre, email, password, rol, edificio_id, activo) VALUES (%s, 'Residente Test', 'test@habita.cl', '1234', 'residente', %s, TRUE) ON CONFLICT (rut) DO UPDATE SET rol='residente', edificio_id=%s, activo=TRUE", (rut_demo, eid, eid))
        conn.commit(); return f"OK: {rut_demo}"
    except Exception as e: return f"Error {e}"
    finally: cur.close(); conn.close()

if __name__ == '__main__':
    app.run(debug=True, port=5003)