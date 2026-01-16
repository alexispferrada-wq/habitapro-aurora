from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
from app.database import get_db_cursor
from datetime import datetime
import json

conserje_bp = Blueprint('conserje', __name__)

# ==========================================
# UTILIDADES (Locales para el módulo)
# ==========================================

def parse_json_field(field_data):
    if isinstance(field_data, dict): return field_data
    try: return json.loads(field_data or '{}')
    except: return {}

def obtener_estado_parking_real(edificio_id):
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
                h, m = divmod(minutos, 60)
                tiempo = f"{h}h {m}m"
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
# RUTAS PANEL CONSERJE
# ==========================================

@conserje_bp.route('/panel-conserje')
def panel_conserje():
    if session.get('rol') != 'conserje': return redirect(url_for('auth.login'))
    
    eid = session.get('edificio_id')
    
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM edificios WHERE id = %s", (eid,))
        edificio = cur.fetchone()
        
        # 1. PAQUETES PENDIENTES
        cur.execute("SELECT e.id, u.numero as unidad, e.remitente, e.recepcion FROM encomiendas e JOIN unidades u ON e.unidad_id = u.id WHERE e.edificio_id = %s AND e.entrega IS NULL ORDER BY e.recepcion DESC", (eid,))
        encomiendas = cur.fetchall()
        
        # 2. RESERVAS (SOLO HOY Y FUTURO)
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
        
        # 3. UNIDADES
        cur.execute("SELECT id, numero, owner_json, tenant_json FROM unidades WHERE edificio_id = %s ORDER BY LENGTH(numero), numero ASC", (eid,))
        raw = cur.fetchall()
    
    u_proc = []
    for u in raw:
        o = parse_json_field(u.get('owner_json'))
        t = parse_json_field(u.get('tenant_json'))
        nombre = t.get('nombre') or o.get('nombre','S/D')
        fono = t.get('fono') or o.get('fono','')
        email = t.get('email') or o.get('email','')
        rut = t.get('rut') or o.get('rut','')
        u_proc.append({'id': u['id'], 'numero': u['numero'], 'residente': nombre, 'fono': fono, 'email': email, 'rut': rut, 'owner': o, 'tenant': t})
    
    return render_template('dash_conserje.html', 
                           edificio=dict(edificio), 
                           parking=obtener_estado_parking_real(eid), 
                           encomiendas=encomiendas, 
                           unidades=u_proc, 
                           reservas_futuras=reservas_futuras)

@conserje_bp.route('/conserje/parking/toggle', methods=['POST'])
def conserje_parking_toggle():
    sid = request.form.get('slot_id')
    acc = request.form.get('accion')
    pat = request.form.get('patente', 'VISITA').upper()
    uid = request.form.get('unidad_id')
    eid = session.get('edificio_id')

    try:
        with get_db_cursor(commit=True) as cur:
            if acc == 'ocupar':
                cur.execute("""
                    INSERT INTO visitas (edificio_id, unidad_id, patente, estacionamiento_id, parking_id, ingreso) 
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (eid, uid, pat, sid, sid))
                
                cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s", (pat, sid))

            else:
                cur.execute("""
                    UPDATE visitas 
                    SET salida = NOW() 
                    WHERE edificio_id = %s 
                    AND (parking_id = %s OR estacionamiento_id = %s) 
                    AND salida IS NULL
                """, (eid, int(sid), sid))

                cur.execute("UPDATE estacionamientos_visita SET estado = 'libre', patente = NULL WHERE id = %s", (sid,))

        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Error parking toggle: {e}")
        return jsonify({'status': 'error', 'message': str(e)})

@conserje_bp.route('/conserje/encomiendas/guardar', methods=['POST'])
def conserje_guardar_encomienda():
    with get_db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO encomiendas (edificio_id, unidad_id, remitente, recepcion) VALUES (%s, %s, %s, NOW())", (session.get('edificio_id'), request.form.get('unidad_id'), request.form.get('remitente')))
    return redirect(url_for('conserje.panel_conserje'))

@conserje_bp.route('/conserje/encomiendas/entregar', methods=['POST'])
def conserje_entregar_encomienda():
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE encomiendas SET entrega = NOW() WHERE id = %s", (request.form.get('encomienda_id'),))
    return redirect(url_for('conserje.panel_conserje'))

@conserje_bp.route('/conserje/incidencias/guardar', methods=['POST'])
def conserje_guardar_incidencia():
    with get_db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor) VALUES (%s, %s, %s, NOW(), %s)", (session.get('edificio_id'), request.form.get('titulo'), request.form.get('descripcion'), session.get('nombre')))
    return redirect(url_for('conserje.panel_conserje'))

@conserje_bp.route('/conserje/visitas/confirmar_vehiculo', methods=['POST'])
def confirmar_ingreso_vehiculo():
    token = request.form.get('token')
    parking_id = request.form.get('parking_id')
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("SELECT * FROM invitaciones WHERE token = %s", (token,))
        inv = cur.fetchone()
        
        parking_nombre = "Sin Asignar"
        if parking_id:
            cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s RETURNING nombre", (inv['patente'], parking_id))
            res_park = cur.fetchone()
            if res_park: parking_nombre = res_park['nombre']

        cur.execute("""
            INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, parking_id, ingreso)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
        """, (inv['edificio_id'], inv['unidad_id'], inv['rut_visita'], inv['nombre_visita'], inv['patente'], parking_id))
        
        cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],))
    
    return jsonify({'status': 'success', 'parking_nombre': parking_nombre, 'visita': inv['nombre_visita']})

@conserje_bp.route('/conserje/visitas/validar_qr', methods=['POST'])
def validar_qr_visita():
    codigo = request.form.get('codigo_qr')
    print(f"\n⚡ VALIDANDO QR: {codigo}") 
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            SELECT i.*, u.numero as unidad_numero 
            FROM invitaciones i 
            JOIN unidades u ON i.unidad_id = u.id 
            WHERE i.token = %s
        """, (codigo,))
        inv = cur.fetchone()
        
        if not inv:
            return jsonify({'status': 'error', 'message': 'QR No existe 🚫'})

        if inv['estado'] == 'USADO':
            return jsonify({'status': 'error', 'message': 'Este pase YA FUE USADO ⚠️'})

        patente_db = inv.get('patente')
        patente_limpia = str(patente_db if patente_db else '').strip().upper()
        es_vehiculo = len(patente_limpia) > 2
        
        if es_vehiculo:
            cur.execute("""
                SELECT e.id, e.nombre 
                FROM estacionamientos_visita e
                LEFT JOIN visitas v ON e.id = v.parking_id AND v.salida IS NULL
                WHERE e.edificio_id = %s 
                AND (e.estado = 'LIBRE' OR e.estado = 'libre')
                AND v.id IS NULL
                ORDER BY e.id ASC
            """, (inv['edificio_id'],))
            
            slots_libres = cur.fetchall()
            
            return jsonify({
                'status': 'parking_selection',
                'token_invitacion': codigo,
                'visita': inv['nombre_visita'],
                'patente': patente_limpia,
                'slots': slots_libres
            })
            
        else:
            cur.execute("""
                INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, ingreso)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (inv['edificio_id'], inv['unidad_id'], inv['rut_visita'], inv['nombre_visita'], 'PEATON'))
            
            cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE id = %s", (inv['id'],))
            
            return jsonify({
                'status': 'success', 
                'tipo': 'PEATON',
                'visita': inv['nombre_visita'],
                'unidad': inv['unidad_numero']
            })

@conserje_bp.route('/conserje/qr/validar', methods=['POST'])
def conserje_qr_validar():
    # Esta función parece ser una versión alternativa o duplicada de validar_qr_visita en el código original.
    # La mantenemos redirigiendo a la lógica principal para evitar duplicidad, o se puede implementar igual.
    # Por seguridad, implementamos la misma lógica que validar_qr_visita.
    return validar_qr_visita()

@conserje_bp.route('/conserje/qr/asignar_parking', methods=['POST'])
def conserje_qr_asignar_parking():
    token = request.form.get('token')
    sid = request.form.get('slot_id')

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT * FROM invitaciones WHERE token = %s", (token,))
            inv = cur.fetchone()

            if not inv:
                return jsonify({'status': 'error', 'message': 'Invitación no válida'})

            cur.execute("""
                INSERT INTO visitas (edificio_id, unidad_id, rut, nombre_visita, patente, parking_id, estacionamiento_id, ingreso)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            """, (inv['edificio_id'], inv['unidad_id'], inv.get('rut_visita',''), inv['nombre_visita'], inv['patente'], sid, sid))

            cur.execute("UPDATE estacionamientos_visita SET estado = 'ocupado', patente = %s WHERE id = %s", (inv['patente'], sid))

            cur.execute("UPDATE invitaciones SET estado = 'USADO', fecha_uso = NOW() WHERE token = %s", (token,))
            
        return jsonify({'status': 'success'})

    except Exception as e:
        print(f"Error Asignar QR: {e}")
        return jsonify({'status': 'error', 'message': str(e)})