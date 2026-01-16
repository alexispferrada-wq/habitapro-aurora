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
def landing():
    # Si el usuario ya está logueado, lo mandamos a su panel. Si no, ve la landing.
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/dashboard')
@login_required
def dashboard():
    # Esta ruta centraliza la redirección post-login.
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    rol = session.get('rol')
    if rol == 'superadmin': return redirect(url_for('panel_superadmin'))
    if rol == 'admin': return redirect(url_for('panel_admin'))
    if rol == 'conserje': return redirect(url_for('panel_conserje'))
    if rol == 'residente': return redirect(url_for('panel_residente'))

# --- NUEVAS RUTAS PARA LA SELECCIÓN MÚLTIPLE ---

@app.route('/seleccionar_unidad')
def seleccionar_unidad():
    opciones = session.get('opciones_login')
    if not opciones:
        return redirect(url_for('landing'))
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
    
    return redirect(url_for('landing'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('landing'))

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

@app.route('/conserje/medidores/guardar', methods=['POST'])
def conserje_guardar_medidor():
    if session.get('rol') != 'conserje': return redirect(url_for('home'))
    uid = request.form.get('unidad_id')
    tipo = request.form.get('tipo')
    valor = request.form.get('valor')
    eid = session.get('edificio_id')
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Validación de consistencia: No permitir lecturas menores a la anterior
        cur.execute("""
            SELECT valor FROM lecturas_medidores 
            WHERE unidad_id = %s AND tipo = %s 
            ORDER BY fecha DESC LIMIT 1
        """, (uid, tipo))
        ultima_lectura = cur.fetchone()
        
        if ultima_lectura and float(valor) < float(ultima_lectura['valor']):
            flash(f"❌ Error: El valor ({valor}) no puede ser menor a la última lectura registrada ({ultima_lectura['valor']}).")
            return redirect(url_for('panel_conserje'))

        cur.execute("""
            INSERT INTO lecturas_medidores (edificio_id, unidad_id, tipo, valor, fecha, registrado_por)
            VALUES (%s, %s, %s, %s, NOW(), %s)
        """, (eid, uid, tipo, valor, session.get('nombre')))
        conn.commit()
        flash(f"✅ Lectura de {tipo} registrada correctamente.")
    except Exception as e:
        conn.rollback()
        flash("❌ Error al guardar lectura de medidor.")
    finally:
        cur.close(); conn.close()
    return redirect(url_for('panel_conserje'))

@app.route('/conserje/medidores/ultima_lectura')
def conserje_ultima_lectura():
    if session.get('rol') != 'conserje': return jsonify({'status': 'error'})
    uid = request.args.get('unidad_id')
    tipo = request.args.get('tipo')
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT valor, fecha FROM lecturas_medidores 
        WHERE unidad_id = %s AND tipo = %s 
        ORDER BY fecha DESC LIMIT 1
    """, (uid, tipo))
    res = cur.fetchone()
    cur.close(); conn.close()
    
    if res:
        return jsonify({'status': 'success', 'valor': res['valor'], 'fecha': res['fecha'].strftime('%d/%m/%Y')})
    return jsonify({'status': 'success', 'valor': 0, 'fecha': 'N/A'})

@app.route('/conserje/turno/guardar', methods=['POST'])
def conserje_guardar_turno():
    if session.get('rol') != 'conserje': return redirect(url_for('home'))
    novedades = request.form.get('novedades')
    caja = request.form.get('caja', 0)
    eid = session.get('edificio_id')
    nombre = session.get('nombre')
    
    # Formateamos el mensaje para que aparezca claro en la bitácora del admin
    detalle = f"ENTREGA DE TURNO\nConserje: {nombre}\nCaja: ${caja}\nNovedades: {novedades}"
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor) VALUES (%s, %s, %s, NOW(), %s)", 
               (eid, "ENTREGA DE TURNO", detalle, nombre))
    conn.commit(); cur.close(); conn.close()
    flash("✅ Turno finalizado y registrado en bitácora.")
    return redirect(url_for('panel_conserje'))

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
        hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
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
    hashed_np = generate_password_hash(np, method='pbkdf2:sha256')
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
    hashed_pass = generate_password_hash(nueva_pass, method='pbkdf2:sha256')
    
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
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
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
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
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
    hashed_pass = generate_password_hash(new_pass, method='pbkdf2:sha256')
    
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
            # Si ya está logueado, va al dashboard central.
            return redirect(url_for('dashboard'))
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

                # Redirección unificada para todos los roles al dashboard.
                return redirect(url_for('dashboard'))
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
        hashed_demo = generate_password_hash("1234", method='pbkdf2:sha256')
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
            VALUES (%s, %s, %s, %s, TRUE, 0, 'PENDIENTE') RETURNING id
        """, (nombre, direccion, lat_val, lon_val))
        new_id = cur.fetchone()['id']
        
        conn.commit()
        flash(f'¡Éxito! El edificio "{nombre}" ya está en el sistema.', 'success')
        return redirect(url_for('panel_superadmin', highlight=new_id))
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