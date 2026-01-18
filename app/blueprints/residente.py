from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
from app.database import get_db_cursor
from datetime import date, datetime
import json
import secrets

residente_bp = Blueprint('residente', __name__)

# ==========================================
# UTILIDADES
# ==========================================

def parse_json_field(field_data):
    if isinstance(field_data, dict): return field_data
    try: return json.loads(field_data or '{}')
    except: return {}

# ==========================================
# RUTAS PANEL RESIDENTE
# ==========================================

@residente_bp.route('/manifest.json')
def manifest():
    return jsonify({
        "name": "HABITEX",
        "short_name": "HABITEX",
        "start_url": "/panel-residente",
        "display": "standalone",
        "background_color": "#1a1d24",
        "theme_color": "#0dcaf0",
        "icons": [
            {
                "src": "https://res.cloudinary.com/dqsz4ua73/image/upload/v1768769649/logo_habitex_favicon_qpht7n.png",
                "sizes": "any",
                "type": "image/svg+xml"
            }
        ]
    })

@residente_bp.route('/residente/perfil/editar', methods=['POST'])
def residente_editar_perfil():
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE usuarios SET email=%s, telefono=%s WHERE rut=%s", (request.form.get('email'), request.form.get('telefono'), session.get('user_id')))
    return redirect(url_for('residente.panel_residente'))

@residente_bp.route('/panel-residente', methods=['GET', 'POST'])
def panel_residente():
    # 1. SEGURIDAD: Si faltan datos, CERRAMOS LA SESIÓN para romper el bucle
    if session.get('rol') != 'residente' or 'unidad_id_residente' not in session:
        flash("Error de sesión: No se pudo identificar tu departamento.", "error")
        return redirect(url_for('auth.logout')) 
    
    uid = session.get('unidad_id_residente')
    eid = session.get('edificio_id')
    
    with get_db_cursor() as cur:
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
        cur.execute("""
            SELECT v.*, p.nombre as parking_nombre,
            EXTRACT(EPOCH FROM (NOW() - v.ingreso))/60 as minutos_transcurridos
            FROM visitas v
            LEFT JOIN estacionamientos_visita p ON v.parking_id = p.id
            WHERE v.unidad_id = %s AND v.salida IS NULL
            ORDER BY v.ingreso DESC
        """, (uid,))
        visitas_activas = cur.fetchall()

        # 5. Multas Impagas
        try:
            cur.execute("SELECT * FROM multas WHERE unidad_id = %s AND pagada = FALSE ORDER BY fecha DESC", (uid,))
            multas = cur.fetchall()
        except:
            multas = []
    
    return render_template('dash_residente.html', 
                         u=u, user=user_data, edificio=edificio, 
                         espacios=espacios, encomiendas=encomiendas, 
                         mis_reservas=mis_reservas, visitas_activas=visitas_activas, 
                         multas=multas, hoy=date.today())

@residente_bp.route('/residente/invitar/generar', methods=['POST'])
def generar_link_invitacion():
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
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            INSERT INTO invitaciones (token, edificio_id, unidad_id, tipo, pre_nombre, estado)
            VALUES (%s, %s, %s, %s, %s, 'PENDIENTE')
        """, (t, eid, uid, tipo, pre_nombre))
    
    # Usamos .public_invitacion para referenciar la ruta dentro del mismo blueprint
    link_final = url_for('residente.public_invitacion', token=t, _external=True)
    
    return jsonify({'status': 'success', 'link': link_final})

@residente_bp.route('/invitacion/<token>', methods=['GET', 'POST'])
def public_invitacion(token):
    with get_db_cursor(commit=True) as cur:
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
            return render_template('public_qr_exito.html', error="Invitación no encontrada o enlace roto.")

        # --- CANDADO 2: SI YA FUE USADO ---
        if inv['estado'] == 'USADO':
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
                    inv['patente'] = patente 
                
                # Al enviar el formulario, mostramos el QR exitoso
                return render_template('public_qr_exito.html', inv=inv, token=token)

            except Exception as e:
                print(f"Error QR: {e}")
                return "Error procesando solicitud"

    # --- LOGICA GET: MOSTRAR FORMULARIO ---
    return render_template('public_visita.html', inv=inv)

@residente_bp.route('/invitacion/guardar', methods=['POST'])
def guardar_invitacion_visita():
    token = request.form.get('token')
    nombre = request.form.get('nombre')
    rut = request.form.get('rut')
    
    raw_patente = request.form.get('patente', '')
    patente = raw_patente.strip().upper()
    
    if len(patente) < 2:
        patente = None

    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE invitaciones 
            SET nombre_visita = %s, rut_visita = %s, patente = %s, estado = 'LISTO' 
            WHERE token = %s
        """, (nombre, rut, patente, token))
    
    return render_template('public_qr_exito.html', 
                           token=token, 
                           nombre=nombre, 
                           rut=rut)

@residente_bp.route('/residente/reservar', methods=['POST'])
def residente_crear_reserva():
    if session.get('rol') != 'residente': return redirect(url_for('auth.login'))
    
    uid = session.get('unidad_id_residente')
    espacio_id = request.form.get('espacio_id')
    fecha = request.form.get('fecha')
    hora = request.form.get('hora')
    
    try:
        with get_db_cursor(commit=True) as cur:
            # Validar disponibilidad
            cur.execute("SELECT id FROM reservas WHERE espacio_id=%s AND fecha_uso=%s AND hora_inicio=%s AND estado='CONFIRMADA'", (espacio_id, fecha, hora))
            if cur.fetchone(): 
                flash("⛔ Horario no disponible.")
                return redirect(url_for('residente.panel_residente'))

            cur.execute("SELECT precio, nombre FROM espacios WHERE id=%s", (espacio_id,))
            espacio = cur.fetchone()
            
            cur.execute("INSERT INTO reservas (espacio_id, unidad_id, fecha_uso, hora_inicio, estado) VALUES (%s, %s, %s, %s, 'CONFIRMADA')", (espacio_id, uid, fecha, hora))
            
            if espacio['precio'] > 0:
                cur.execute("UPDATE unidades SET deuda_monto = deuda_monto + %s WHERE id = %s", (espacio['precio'], uid))
                
        flash(f"✅ Reserva: {espacio['nombre']} a las {hora}")
    except Exception as e: flash("❌ Error al reservar")
    return redirect(url_for('residente.panel_residente'))