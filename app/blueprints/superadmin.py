from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
from flask_login import login_required, current_user, login_user
from app.database import get_db_cursor
from app.models import Usuario
from werkzeug.security import generate_password_hash
from datetime import datetime, date
import json
import random
import csv
import io
import requests

superadmin_bp = Blueprint('superadmin', __name__)

# ==========================================
# 0. UTILIDADES Y HELPERS
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

# ==========================================
# 1. DASHBOARD Y VISTAS PRINCIPALES
# ==========================================

@superadmin_bp.route('/panel-superadmin')
@login_required
def panel_superadmin():
    if current_user.rol != 'superadmin': return redirect(url_for('auth.login'))
    
    with get_db_cursor() as cur:
        # 1. ESTADO DE LA BASE DE DATOS
        try:
            cur.execute("SELECT version()")
            ver_raw = cur.fetchone()
            db_ver = ver_raw['version'] if ver_raw else "Unknown"
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

        # NUEVO: Métricas adicionales para el dashboard
        try:
            cur.execute("SELECT SUM(deuda_omnisoft) as total FROM edificios WHERE activo = TRUE")
            total_deuda = cur.fetchone()['total'] or 0
        except:
            total_deuda = 0

        # Placeholder para sesiones activas (requeriría un store de sesiones como Redis para ser exacto)
        active_sessions = total_usuarios // 3 + random.randint(-5, 10) 

        # Placeholder para el gráfico de crecimiento
        growth_data = {
            "labels": ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio"],
            "users": [120, 150, 180, 250, 310, total_usuarios]
        }

        # 3. LOGS DEL SISTEMA
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

    return render_template('dash_super.html', 
                           stats={'edificios': total_edificios, 'users': total_usuarios, 'deuda': total_deuda, 'sessions': active_sessions}, 
                           edificios=edificios_stats, 
                           global_logs=logs_proc, 
                           db_info=db_info,
                           indicadores=obtener_indicadores(),
                           growth_data=growth_data)

@superadmin_bp.route('/superadmin/detalle_edificio/<int:id>')
@login_required
def super_detalle_edificio(id):
    if current_user.rol != 'superadmin': return redirect(url_for('auth.login'))
    
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM edificios WHERE id = %s", (id,))
        edificio = cur.fetchone()
        
        if not edificio:
            return "Edificio no encontrado", 404

        cur.execute("SELECT * FROM usuarios WHERE edificio_id = %s AND rol = 'admin' AND activo = TRUE", (id,))
        admins = cur.fetchall()
        
        cur.execute("SELECT * FROM unidades WHERE edificio_id = %s ORDER BY numero ASC", (id,))
        unidades_raw = cur.fetchall()
        
        unidades_procesadas = []
        for u in unidades_raw:
            u['owner'] = parse_json_field(u.get('owner_json'))
            u['tenant'] = parse_json_field(u.get('tenant_json'))
            unidades_procesadas.append(u)
            
        # NUEVO: Historial de pagos del edificio a HabitaPro
        try:
            cur.execute("SELECT * FROM pagos_edificios WHERE edificio_id = %s ORDER BY fecha_pago DESC LIMIT 5", (id,))
            historial_pagos = cur.fetchall()
        except Exception: # La tabla podría no existir aún
            historial_pagos = []

    return render_template('super_detalle_edificio.html', 
                           e=edificio, 
                           admins=admins, 
                           unidades=unidades_procesadas,
                           historial_pagos=historial_pagos,
                           indicadores=obtener_indicadores())

# ==========================================
# 2. GESTIÓN DE FINANZAS Y COBROS
# ==========================================

@superadmin_bp.route('/superadmin/enviar_cobro', methods=['POST'])
def enviar_cobro():
    if session.get('rol') != 'superadmin': return redirect(url_for('auth.login'))
    
    eid = request.form.get('edificio_id')
    monto_raw = request.form.get('monto')
    desc = request.form.get('descripcion')
    vence = request.form.get('vencimiento')
    
    try:
        if monto_raw:
            monto = int(str(monto_raw).replace('.', '').replace(',', ''))
        else:
            monto = 0
    except ValueError:
        flash("Error: El monto ingresado no es válido.")
        return redirect(url_for('superadmin.super_detalle_edificio', id=eid))
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("""
            UPDATE edificios 
            SET deuda_omnisoft = %s, 
                deuda_descripcion = %s, 
                deuda_vencimiento = %s, 
                estado_pago = 'PENDIENTE',
                deuda_comprobante_url = NULL 
            WHERE id = %s
        """, (monto, desc, vence, eid))
    
    flash("Cobro enviado al edificio.")
    return redirect(url_for('superadmin.super_detalle_edificio', id=eid))

@superadmin_bp.route('/superadmin/registrar_pago_edificio', methods=['POST'])
def registrar_pago_edificio():
    eid = request.form.get('edificio_id')
    with get_db_cursor(commit=True) as cur:
        # 1. Obtener datos de la deuda antes de borrarla
        cur.execute("SELECT deuda_omnisoft, deuda_descripcion FROM edificios WHERE id = %s", (eid,))
        deuda_info = cur.fetchone()
        monto = deuda_info['deuda_omnisoft'] if deuda_info else 0
        concepto = deuda_info['deuda_descripcion'] if deuda_info else 'Pago de servicio'

        # 2. Guardar en el nuevo historial de pagos
        cur.execute("""
            INSERT INTO pagos_edificios (edificio_id, monto, concepto, fecha_pago)
            VALUES (%s, %s, %s, NOW())
        """, (eid, monto, concepto))
        cur.execute("UPDATE edificios SET deuda_omnisoft = 0, estado_pago = 'PAGADO' WHERE id = %s", (eid,))
    flash("Pago registrado y archivado correctamente.")
    return redirect(url_for('superadmin.super_detalle_edificio', id=request.form.get('edificio_id')))

# ==========================================
# 3. GESTIÓN DE USUARIOS (ADMINS & STAFF)
# ==========================================

@superadmin_bp.route('/superadmin/crear_admin_rapido', methods=['POST'])
@login_required
def crear_admin_rapido():
    rut = formatear_rut(request.form.get('rut'))
    nombre = request.form.get('nombre')
    email = request.form.get('email')
    edificio_id = request.form.get('edificio_id')
    
    new_pass = f"Habipro{random.randint(1000,9999)}$"
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO usuarios (rut, nombre, email, password, rol, edificio_id, activo) 
                VALUES (%s, %s, %s, %s, 'admin', %s, TRUE) 
                ON CONFLICT (rut) 
                DO UPDATE SET rol='admin', edificio_id=%s, activo=TRUE, password=%s
            """, (rut, nombre, email, hashed_pass, edificio_id, edificio_id, hashed_pass))
        
        flash(f"{nombre}|{rut}|{new_pass}", "credenciales_new_admin")
    except Exception as e:
        flash(f"Error al crear admin: {str(e)}", "error")

    return redirect(url_for('superadmin.super_detalle_edificio', id=edificio_id))

@superadmin_bp.route('/superadmin/reset_pass_admin', methods=['POST'])
@login_required
def reset_pass_admin():
    rut_admin = request.form.get('rut_admin')
    edificio_id = request.form.get('edificio_id')
    
    new_pass = f"Reset{random.randint(1000,9999)}$"
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("SELECT nombre FROM usuarios WHERE rut = %s", (rut_admin,))
            u = cur.fetchone()
            nombre = u['nombre'] if u else 'Administrador'

            cur.execute("UPDATE usuarios SET password=%s WHERE rut=%s", (hashed_pass, rut_admin))
        
        flash(f"{nombre}|{rut_admin}|{new_pass}", "credenciales_reset")
    except Exception as e:
        flash("Error al resetear clave", "error")
        
    return redirect(url_for('superadmin.super_detalle_edificio', id=edificio_id))

@superadmin_bp.route('/superadmin/editar_admin', methods=['POST'])
def superadmin_editar_admin():
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE usuarios SET nombre=%s, email=%s, telefono=%s WHERE rut=%s", (request.form.get('nombre'), request.form.get('email'), request.form.get('telefono'), request.form.get('rut')))
    flash("Datos actualizados")
    return redirect(url_for('superadmin.super_detalle_edificio', id=request.form.get('edificio_id')))

@superadmin_bp.route('/superadmin/toggle_acceso', methods=['POST'])
def toggle_acceso():
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE usuarios SET activo=%s WHERE rut=%s",(request.form.get('nuevo_estado')=='true',request.form.get('rut_admin')))
    return redirect(url_for('superadmin.super_detalle_edificio', id=int(request.form.get('edificio_id'))))

@superadmin_bp.route('/superadmin/buscar_usuarios_global')
@login_required
def superadmin_buscar_global():
    if current_user.rol != 'superadmin': return jsonify([])
    q = request.args.get('q', '').lower()
    
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT u.rut as id, u.rut, u.nombre, u.rol, COALESCE(e.nombre, 'Sin Edificio') as edificio 
            FROM usuarios u
            LEFT JOIN edificios e ON u.edificio_id = e.id
            WHERE (LOWER(u.nombre) LIKE %s OR LOWER(u.rut) LIKE %s)
            AND u.activo = TRUE
            LIMIT 10
        """, (f'%{q}%', f'%{q}%'))
        results = cur.fetchall()
    
    return jsonify([dict(r) for r in results])

# ==========================================
# 4. GESTIÓN DE EDIFICIOS (CRUD)
# ==========================================

@superadmin_bp.route('/superadmin/crear_edificio', methods=['POST'])
@login_required
def super_crear_edificio():
    if current_user.rol != 'superadmin':
        return redirect(url_for('auth.login'))
        
    nombre = request.form.get('nombre')
    direccion = request.form.get('direccion')
    lat = request.form.get('lat')
    lon = request.form.get('lon')
    
    try:
        lat_val = float(lat) if lat and lat.strip() else None
        lon_val = float(lon) if lon and lon.strip() else None
    except ValueError:
        lat_val = None
        lon_val = None

    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("""
                INSERT INTO edificios (nombre, direccion, latitud, longitud, activo, deuda_omnisoft, estado_pago)
                VALUES (%s, %s, %s, %s, TRUE, 0, 'PENDIENTE') RETURNING id
            """, (nombre, direccion, lat_val, lon_val))
            new_id = cur.fetchone()['id']
        
        flash(f'¡Éxito! El edificio "{nombre}" ya está en el sistema.', 'success')
        return redirect(url_for('superadmin.panel_superadmin', highlight=new_id))
    except Exception as e:
        print(f"🔥 Error SQL Crear Edificio: {e}")
        flash(f'Error al crear el edificio en la base de datos.', 'error')
        
    return redirect(url_for('superadmin.panel_superadmin'))

@superadmin_bp.route('/superadmin/toggle_edificio/<int:edificio_id>', methods=['POST'])
def superadmin_toggle_edificio(edificio_id):
    if session.get('rol') != 'superadmin': return jsonify({'status': 'error'})
    
    data = request.get_json()
    nuevo_estado = data.get('activo')
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE edificios SET activo = %s WHERE id = %s", (nuevo_estado, edificio_id))
    
    return jsonify({'status': 'success'})

@superadmin_bp.route('/superadmin/eliminar_edificio', methods=['POST'])
def eliminar_edificio():
    with get_db_cursor(commit=True) as cur:
        cur.execute("UPDATE edificios SET activo = FALSE WHERE id = %s", (request.form.get('edificio_id'),))
        cur.execute("UPDATE usuarios SET activo = FALSE WHERE edificio_id = %s", (request.form.get('edificio_id'),))
    return redirect(url_for('superadmin.panel_superadmin'))

# ==========================================
# 5. GESTIÓN DE UNIDADES
# ==========================================

@superadmin_bp.route('/superadmin/crear_unidad', methods=['POST'])
def crear_unidad():
    o = json.dumps({'rut': formatear_rut(request.form.get('owner_rut')), 'nombre': request.form.get('owner_nombre'), 'email': request.form.get('owner_email'), 'fono': request.form.get('owner_fono')})
    t = json.dumps({'rut': formatear_rut(request.form.get('tenant_rut')), 'nombre': request.form.get('tenant_nombre'), 'email': request.form.get('tenant_email'), 'fono': request.form.get('tenant_fono')})
    
    with get_db_cursor(commit=True) as cur:
        cur.execute("INSERT INTO unidades (edificio_id, numero, piso, metraje, prorrateo, owner_json, tenant_json) VALUES (%s, %s, %s, %s, %s, %s, %s)", (request.form.get('edificio_id'), request.form.get('numero'), request.form.get('piso'), request.form.get('metraje'), request.form.get('prorrateo'), o, t))
    
    return redirect(url_for('superadmin.super_detalle_edificio', id=request.form.get('edificio_id')))

@superadmin_bp.route('/superadmin/carga_masiva_csv', methods=['POST'])
def carga_masiva_csv():
    try:
        ed = int(request.form.get('edificio_id_retorno'))
        f = request.files['archivo_csv']
        s = io.TextIOWrapper(f.stream._file, "utf-8", newline="")
        r = csv.DictReader(s)
        
        with get_db_cursor(commit=True) as cur:
            for x in r:
                o = json.dumps({'rut':formatear_rut(x.get('owner_rut')),'nombre':x.get('owner_nombre'),'email':x.get('owner_email')})
                t = json.dumps({'rut':formatear_rut(x.get('tenant_rut')),'nombre':x.get('tenant_nombre'),'email':x.get('tenant_email')})
                cur.execute("INSERT INTO unidades (edificio_id,numero,piso,metraje,prorrateo,owner_json,tenant_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",(ed,x['numero'],x.get('piso',1),x['metraje'],x['prorrateo'],o,t))
        
        return redirect(url_for('superadmin.super_detalle_edificio', id=ed))
    except: 
        return redirect(url_for('superadmin.super_detalle_edificio', id=ed))

# ==========================================
# 6. HERRAMIENTAS DE CONTROL TOTAL (GHOST & BROADCAST)
# ==========================================

@superadmin_bp.route('/superadmin/ghost_login/<user_rut>', methods=['POST'])
@login_required
def superadmin_ghost_login(user_rut):
    if current_user.rol not in ['superadmin', 'admin']:
        return redirect(url_for('auth.login'))
    
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE rut = %s", (user_rut,))
        user_data = cur.fetchone()

        if user_data:
            session['god_mode_origin'] = current_user.rut 
            
            session['user_id'] = user_data['rut']
            session['nombre'] = user_data['nombre']
            session['rol'] = user_data['rol']
            session['edificio_id'] = user_data.get('edificio_id')

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

            user_obj = Usuario()
            user_obj.rut = user_data['rut']
            user_obj.nombre = user_data['nombre']
            user_obj.rol = user_data['rol']
            user_obj.edificio_id = user_data.get('edificio_id')
            login_user(user_obj)

            flash(f"Modo Fantasma: {user_data['nombre']}", "info")
            
            if user_data['rol'] == 'residente': return redirect(url_for('residente.panel_residente'))
            if user_data['rol'] == 'admin': return redirect(url_for('admin.panel_admin'))
            if user_data['rol'] == 'conserje': return redirect(url_for('conserje.panel_conserje'))

    return redirect(url_for('superadmin.panel_superadmin'))

@superadmin_bp.route('/superadmin/exit_ghost')
@login_required
def superadmin_exit_ghost():
    origin_rut = session.get('god_mode_origin')
    
    if not origin_rut:
        flash("No se detectó una sesión de origen válida.", "error")
        return redirect(url_for('auth.login'))
    
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE rut = %s", (origin_rut,))
        god_user = cur.fetchone()
        
        if god_user:
            session.clear()
            
            session['user_id'] = god_user['rut']
            session['nombre'] = god_user['nombre']
            session['rol'] = 'superadmin'
            session['edificio_id'] = None 
            
            god_obj = Usuario()
            god_obj.rut = god_user['rut']
            god_obj.nombre = god_user['nombre']
            god_obj.rol = 'superadmin'
            login_user(god_obj)
            
            flash(f"Saliendo del Modo Fantasma. Bienvenido de vuelta, {god_user['nombre']}", "info")
            return redirect(url_for('superadmin.panel_superadmin'))
        
    return redirect(url_for('auth.login'))

@superadmin_bp.route('/superadmin/broadcast', methods=['POST'])
@login_required
def superadmin_broadcast():
    if current_user.rol != 'superadmin': return jsonify({'status': 'error'})
    mensaje = request.form.get('mensaje')
    titulo = request.form.get('titulo', 'COMUNICADO GLOBAL')
    
    if not mensaje:
        flash("El mensaje no puede estar vacío.", "error")
        return redirect(url_for('superadmin.panel_superadmin'))

    with get_db_cursor(commit=True) as cur:
        # Obtener todos los edificios activos
        cur.execute("SELECT id FROM edificios WHERE activo = TRUE")
        edificios = cur.fetchall()
        
        count = 0
        for ed in edificios:
            cur.execute("""
                INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor)
                VALUES (%s, %s, %s, NOW(), 'SOPORTE HABIPRO')
            """, (ed['id'], titulo, mensaje))
            count += 1
            
    flash(f"Mensaje enviado a {count} edificios.", "success")
    return redirect(url_for('superadmin.panel_superadmin'))