from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash, current_app
from flask_login import login_required, current_user
from app.database import get_db_cursor
from werkzeug.security import generate_password_hash
from datetime import date, datetime, timedelta
import os
import json
import random
import calendar
import requests

admin_bp = Blueprint('admin', __name__)

# ==========================================
# UTILIDADES (Copiadas de app.py para mantener lógica)
# ==========================================

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
    try:
        y_str = request.args.get('year', '')
        m_str = request.args.get('month', '')
        now = date.today()
        y = int(y_str) if y_str and y_str.isdigit() else now.year
        m = int(m_str) if m_str and m_str.isdigit() else now.month
        
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

def obtener_estado_parking_real(edificio_id):
    # Refactorizado para usar get_db_cursor
    with get_db_cursor(commit=True) as cur:
        # 1. Traemos los espacios configurados
        cur.execute("SELECT * FROM estacionamientos_visita WHERE edificio_id = %s ORDER BY id ASC", (edificio_id,))
        slots_db = cur.fetchall()
        
        # Si no existen, creamos 5 por defecto
        if not slots_db:
            for i in range(1, 6):
                cur.execute("INSERT INTO estacionamientos_visita (edificio_id, nombre, estado) VALUES (%s, %s, 'libre')", (edificio_id, f"V-{i}"))
            # Leemos de nuevo dentro de la misma transacción
            cur.execute("SELECT * FROM estacionamientos_visita WHERE edificio_id = %s ORDER BY id ASC", (edificio_id,))
            slots_db = cur.fetchall()

        # 2. Buscamos visitas activas
        cur.execute("""
            SELECT v.parking_id, v.patente, v.ingreso, v.nombre_visita, u.numero as unidad
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
            patente = None
            tiempo = ''
            unidad = ''

            if estado_bd == 'mantencion':
                estado_final = 'mantencion'
            elif s_id in visitas_activas:
                v = visitas_activas[s_id]
                estado_final = 'ocupado'
                patente = v['patente']
                unidad = v['unidad']
                minutos = int((datetime.now() - v['ingreso']).total_seconds() / 60)
                h, m_time = divmod(minutos, 60)
                tiempo = f"{h}h {m_time}m"
            elif estado_bd == 'ocupado':
                 estado_final = 'ocupado'
                 patente = slot.get('patente', 'OCUPADO')

            mapa.append({
                'id': slot['id'], 
                'nombre': slot['nombre'], 
                'estado': estado_final, 
                'patente': patente, 
                'tiempo': tiempo, 
                'unidad_numero': unidad
            })
        return mapa

# ==========================================
# RUTAS PANEL ADMIN
# ==========================================

@admin_bp.route('/panel-admin')
def panel_admin():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    
    eid = session.get('edificio_id')
    
    with get_db_cursor() as cur:
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
        
        # 3. Finanzas del Mes
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
        
        # 5. Activos y Calendario
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
        
    # 8. Alerta Deuda Aexon
    dias_restantes = 0
    alerta_deuda = None
    
    if ed['deuda_omnisoft'] > 0 and ed['estado_pago'] != 'PAGADO':
        if ed['deuda_vencimiento']:
            hoy = date.today()
            delta = ed['deuda_vencimiento'] - hoy
            dias_restantes = delta.days
            
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
        dias_restantes=dias_restantes,
        alerta_deuda=alerta_deuda,
        indicadores=obtener_indicadores()
    )

@admin_bp.route('/admin/gastos')
def admin_gastos():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    eid = session.get('edificio_id')
    y, m = get_safe_date_params()
    mes_nombre = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"][m]
    nav = calcular_navegacion(m, y)
    
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM gastos WHERE edificio_id = %s AND mes = %s AND anio = %s ORDER BY fecha DESC", (eid, m, y))
        gastos = cur.fetchall()
        tg = sum(g['monto'] for g in gastos)
        
        cur.execute("""SELECT p.*, u.numero as unidad_numero, u.owner_json FROM historial_pagos p JOIN unidades u ON p.unidad_id = u.id WHERE p.edificio_id = %s AND ((p.mes_periodo = %s AND p.anio_periodo = %s) OR (p.mes_periodo IS NULL AND EXTRACT(MONTH FROM p.fecha) = %s AND EXTRACT(YEAR FROM p.fecha) = %s)) ORDER BY p.fecha DESC""", (eid, m, y, m, y))
        ing_raw = cur.fetchall()
        ti = sum(i['monto'] for i in ing_raw)
        
        ing = []
        for i in ing_raw: 
            i['owner'] = parse_json_field(i.get('owner_json'))
            ing.append(i)
            
        cur.execute("SELECT * FROM cierres_mes WHERE edificio_id = %s AND mes = %s AND anio = %s", (eid, m, y))
        c = cur.fetchone()

    return render_template('admin_gastos.html', gastos=gastos, ingresos=ing, total_gastos=tg, total_ingresos=ti, balance=ti-tg, mes_actual=m, anio_actual=y, mes_nombre=mes_nombre, mes_cerrado=bool(c), nav=nav)

@admin_bp.route('/admin/gastos/nuevo', methods=['POST'])
def nuevo_gasto():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    eid = session.get('edificio_id')
    f = request.form.get('fecha')
    dt = datetime.strptime(f, '%Y-%m-%d')
    m, y = dt.month, dt.year
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT id FROM cierres_mes WHERE edificio_id=%s AND mes=%s AND anio=%s", (eid, m, y))
        if cur.fetchone(): 
            flash(f"⛔ Mes CERRADO.")
            return redirect(url_for('admin.admin_gastos', month=m, year=y))
        
        cur.execute("INSERT INTO gastos (edificio_id, categoria, descripcion, monto, fecha, mes, anio, comprobante_url) VALUES (%s, %s, %s, %s, %s, %s, %s, 'demo.pdf')", (eid, request.form.get('categoria'), request.form.get('descripcion'), request.form.get('monto'), f, m, y))
    
    return redirect(url_for('admin.admin_gastos', month=m, year=y))

@admin_bp.route('/admin/gastos/cierre_mes', methods=['POST'])
def cierre_mes():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    eid = session.get('edificio_id')
    m = int(request.form.get('mes'))
    y = int(request.form.get('anio'))
    tg = int(request.form.get('total_gastos')) if request.form.get('total_gastos') else 0
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT id, numero, prorrateo, deuda_monto FROM unidades WHERE edificio_id = %s", (eid,))
        units = cur.fetchall()
        for u in units:
            if u['prorrateo']: 
                cur.execute("UPDATE unidades SET deuda_monto = %s, estado_deuda = 'MOROSO' WHERE id = %s", ((u['deuda_monto'] or 0) + int(tg * (u['prorrateo'] / 100)), u['id']))
        
        cur.execute("INSERT INTO cierres_mes (edificio_id, mes, anio, total_gastos, admin_responsable) VALUES (%s, %s, %s, %s, %s)", (eid, m, y, tg, session.get('nombre')))
        cur.execute("UPDATE gastos SET cerrado = TRUE WHERE edificio_id = %s AND mes = %s AND anio = %s", (eid, m, y))
    
    flash(f"🏆 Cierre Exitoso!")
    return redirect(url_for('admin.admin_gastos', month=m, year=y))

@admin_bp.route('/admin/conserjes/listar')
def listar_conserjes():
    with get_db_cursor() as cur:
        cur.execute("SELECT rut, nombre, email, telefono, activo FROM usuarios WHERE edificio_id = %s AND rol = 'conserje'", (session.get('edificio_id'),))
        res = cur.fetchall()
    return jsonify(res)

@admin_bp.route('/admin/conserjes/crear', methods=['POST'])
def crear_conserje():
    try:
        new_pass = f"Conserje{random.randint(1000,9999)}"
        hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
        rut = formatear_rut(request.form.get('rut'))
        
        with get_db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO usuarios (rut, nombre, email, telefono, rol, password, edificio_id, activo) VALUES (%s, %s, %s, %s, 'conserje', %s, %s, TRUE) ON CONFLICT (rut) DO NOTHING RETURNING rut", (rut, request.form.get('nombre'), request.form.get('email'), '', hashed_pass, session.get('edificio_id')))
            msg = 'success' if cur.fetchone() else 'existe'
        
        return jsonify({'status': msg, 'password': new_pass})
    except: return jsonify({'status': 'error'})

@admin_bp.route('/admin/conserjes/eliminar', methods=['POST'])
def eliminar_conserje():
    with get_db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM usuarios WHERE rut = %s", (request.form.get('rut'),))
    return jsonify({'status': 'success'})

@admin_bp.route('/admin/conserjes/reset_clave', methods=['POST'])
def reset_clave_conserje():
    np = f"Conserje{random.randint(1000,9999)}"
    hashed_np = generate_password_hash(np, method='pbkdf2:sha256')
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE usuarios SET password = %s WHERE rut = %s", (hashed_np, request.form.get('rut')))
    return jsonify({'status': 'success', 'password': np})

@admin_bp.route('/admin/residentes/reset_clave', methods=['POST'])
def generar_clave_residente():
    unidad_id = request.form.get('unidad_id')
    new_pass = f"Habipro{random.randint(10000,99999)}"
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
    try:
        with get_db_cursor(commit=True) as cur:
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
            
        return jsonify({'status': 'success', 'password': new_pass, 'residente_nombre': nombre, 'residente_fono': target.get('fono','')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@admin_bp.route('/admin/residentes/registrar_pago', methods=['POST'])
def registrar_pago_residente():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    
    uid = request.form.get('unidad_id')
    monto = int(request.form.get('monto_pago'))
    archivo = request.files.get('comprobante')
    
    filename = 'manual.jpg'
    if archivo:
        filename = f"pago_residente_{uid}_{random.randint(1000,9999)}.jpg"
        archivo.save(os.path.join(current_app.root_path, 'static', 'uploads', filename))

    eid = session.get('edificio_id')
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT mes, anio FROM cierres_mes WHERE edificio_id = %s ORDER BY anio DESC, mes DESC LIMIT 1", (eid,))
        uc = cur.fetchone()
        if uc: mp, ap = (uc['mes']+1, uc['anio']) if uc['mes'] < 12 else (1, uc['anio']+1)
        else: now = datetime.now(); mp, ap = now.month, now.year
        
        cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (monto, uid))
        cur.execute("INSERT INTO historial_pagos (edificio_id, unidad_id, monto, metodo, comprobante_url, mes_periodo, anio_periodo) VALUES (%s, %s, %s, 'TRANSFERENCIA', %s, %s, %s)", (eid, uid, monto, filename, mp, ap))
    
    flash("Pago registrado con comprobante.")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/residentes/multar', methods=['POST'])
def multar_residente():
    uid = request.form.get('unidad_id')
    m = int(request.form.get('monto_multa'))
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE unidades SET deuda_monto = deuda_monto + %s WHERE id = %s", (m, uid))
        cur.execute("INSERT INTO multas (edificio_id, unidad_id, monto, motivo, fecha) VALUES (%s, %s, %s, %s, NOW())", (session.get('edificio_id'), uid, m, request.form.get('motivo')))
    
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/residentes/historial/<int:id>')
def historial_residente(id):
    with get_db_cursor() as cur:
        cur.execute("SELECT remitente, recepcion FROM encomiendas WHERE unidad_id = %s ORDER BY recepcion DESC LIMIT 5", (id,))
        enc = cur.fetchall()
        cur.execute("SELECT fecha, monto, metodo FROM historial_pagos WHERE unidad_id = %s ORDER BY fecha DESC LIMIT 5", (id,))
        pag = cur.fetchall()
    return jsonify({'encomiendas': enc, 'pagos': [{'fecha': p['fecha'].strftime('%d/%m'), 'monto': p['monto'], 'metodo': p['metodo']} for p in pag]})

@admin_bp.route('/admin/gestionar-estacionamiento', methods=['POST'])
def gestionar_estacionamiento():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    acc = request.form.get('accion')
    uid = request.form.get('unidad_id')
    
    with get_db_cursor(commit=True) as cur:
        if acc == 'editar': 
            cur.execute("UPDATE unidades SET estacionamiento=%s WHERE id=%s", (request.form.get('nuevo_nombre_parking').upper(), uid))
        elif acc == 'eliminar': 
            cur.execute("UPDATE unidades SET estacionamiento=NULL WHERE id=%s", (uid,))
            
    flash("Estacionamiento actualizado")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/activos')
def admin_activos():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    
    eid = session.get('edificio_id')
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM activos WHERE edificio_id = %s ORDER BY nombre", (eid,))
        activos = cur.fetchall()

    gasto_mes = 0
    hoy = date.today()
    ultimo_dia_mes = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])

    for a in activos:
        if a['ultimo_servicio']:
            prox = a['ultimo_servicio']
            periodo = a['periodicidad_dias']
            costo = a['costo_estimado']

            while prox < hoy:
                prox += timedelta(days=periodo)
            
            a['prox_fecha'] = prox.strftime('%d/%m/%Y')
            a['dias_restantes'] = (prox - hoy).days

            temp_date = prox
            while temp_date <= ultimo_dia_mes:
                if temp_date.month == hoy.month and temp_date.year == hoy.year:
                    gasto_mes += costo
                temp_date += timedelta(days=periodo)

    return render_template('admin_activos.html', activos=activos, gasto_mes=gasto_mes)

@admin_bp.route('/admin/activos/guardar', methods=['POST'])
def guardar_activo():
    with get_db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO activos (edificio_id, nombre, periodicidad_dias, costo_estimado, ultimo_servicio) VALUES (%s, %s, %s, %s, %s)", (session.get('edificio_id'), request.form.get('nombre'), request.form.get('periodicidad'), request.form.get('costo'), request.form.get('ultimo_servicio')))
    return redirect(url_for('admin.admin_activos'))

@admin_bp.route('/admin/activos/eliminar/<int:id>')
def eliminar_activo(id):
    with get_db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM activos WHERE id=%s", (id,))
    return redirect(url_for('admin.admin_activos'))

@admin_bp.route('/admin/residentes/guardar_edicion', methods=['POST'])
def guardar_edicion_residente():
    tenant_rut = formatear_rut(request.form.get('tenant_rut'))
    t_json = json.dumps({'rut': tenant_rut, 'nombre': request.form.get('tenant_nombre'), 'email': request.form.get('tenant_email'), 'fono': request.form.get('tenant_fono')})
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE unidades SET piso=%s, prorrateo=%s, tenant_json=%s WHERE id=%s", (request.form.get('piso'), request.form.get('prorrateo'), t_json, request.form.get('unidad_id')))
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/auditoria')
def admin_auditoria(): 
    return render_template('base.html')

@admin_bp.route('/admin/difusion', methods=['POST'])
def admin_difusion(): 
    flash(f"📢 Alerta: {request.form.get('mensaje')}")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/espacios/guardar', methods=['POST'])
def admin_guardar_espacio():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    try:
        eid = session.get('edificio_id')
        with get_db_cursor(commit=True) as cur:
            cur.execute("INSERT INTO espacios (edificio_id, nombre, capacidad, precio) VALUES (%s, %s, %s, %s)", 
                       (eid, request.form.get('nombre').upper(), request.form.get('capacidad'), int(request.form.get('precio') or 0)))
        flash("✅ Espacio creado.")
    except: flash("❌ Error al guardar.")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/espacios/eliminar/<int:id>', methods=['GET'])
def admin_eliminar_espacio(id):
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE espacios SET activo = FALSE WHERE id = %s", (id,))
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/reservas/cancelar', methods=['POST'])
def admin_cancelar_reserva():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    rid = request.form.get('reserva_id')
    reembolsar = request.form.get('reembolsar') == 'on'
    
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE reservas SET estado = 'CANCELADA' WHERE id = %s RETURNING unidad_id, espacio_id", (rid,))
            res = cur.fetchone()
            if res and reembolsar:
                cur.execute("SELECT precio FROM espacios WHERE id = %s", (res['espacio_id'],))
                espacio = cur.fetchone()
                if espacio['precio'] > 0:
                    cur.execute("UPDATE unidades SET deuda_monto = GREATEST(0, deuda_monto - %s) WHERE id = %s", (espacio['precio'], res['unidad_id']))
        flash("✅ Reserva cancelada.")
    except: flash("❌ Error.")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/pagar_servicio', methods=['POST'])
def admin_pagar_servicio():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    
    eid = session.get('edificio_id')
    file = request.files.get('comprobante')
    
    if not file:
        flash("Debes subir una foto del comprobante.")
        return redirect(url_for('admin.panel_admin'))
    
    filename = f"pago_{eid}_{random.randint(1000,9999)}.jpg"
    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_folder, exist_ok=True)
    
    try:
        file.save(os.path.join(upload_folder, filename))
    except Exception as e:
        flash(f"Error al guardar imagen: {e}")
        return redirect(url_for('admin.panel_admin'))
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE edificios 
            SET estado_pago = 'REVISION', 
                deuda_comprobante_url = %s 
            WHERE id = %s
        """, (filename, eid))
    
    flash("Comprobante enviado. Esperando validación de Habipro.")
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/logs/listar')
def admin_logs_listar():
    if session.get('rol') != 'admin': return jsonify([])
    
    eid = session.get('edificio_id')
    filtro_fecha = request.args.get('fecha')
    filtro_unidad = request.args.get('unidad')

    if not filtro_fecha:
        filtro_fecha = date.today().strftime('%Y-%m-%d')

    with get_db_cursor() as cur:
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

        if filtro_unidad:
            query += " AND unidad = %s"
            params.append(filtro_unidad)

        query += " ORDER BY fecha_full DESC"

        cur.execute(query, tuple(params))
        logs = cur.fetchall()
    
    data = []
    for l in logs:
        data.append({
            'tipo': l['tipo'],
            'fecha': l['fecha_full'].strftime('%H:%M') if l['fecha_full'] else '-',
            'detalle': l['detalle'],
            'extra': l['extra'] or '-',
            'unidad': l['unidad'] or '-'
        })
    return jsonify(data)

@admin_bp.route('/admin/parking/agregar', methods=['POST'])
def admin_parking_agregar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    nombre = request.form.get('nombre', '').upper()
    eid = session.get('edificio_id')
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO estacionamientos_visita (edificio_id, nombre) VALUES (%s, %s)", (eid, nombre))
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/parking/eliminar', methods=['POST'])
def admin_parking_eliminar():
    if session.get('rol') != 'admin': return jsonify({'status':'error'})
    pid = request.form.get('id')
    with get_db_cursor(commit=True) as cur:
        cur.execute("DELETE FROM estacionamientos_visita WHERE id = %s", (pid,))
    return redirect(url_for('admin.panel_admin'))

@admin_bp.route('/admin/parking/maintenance', methods=['POST'])
def parking_maintenance():
    if session.get('rol') != 'admin': return redirect(url_for('auth.login'))
    sid = request.form.get('slot_id') 
    accion = request.form.get('accion') 
    
    nuevo_estado = 'MANTENCION' if accion == 'activar' else 'LIBRE'
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE estacionamientos_visita SET estado = %s WHERE id = %s", (nuevo_estado, sid))
    return redirect(url_for('admin.panel_admin'))