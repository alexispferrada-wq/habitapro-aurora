import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

# Fallback: Si no se cargó DB_URI, intentar cargar desde .env.txt (Igual que en app/__init__.py)
if not os.getenv('DB_URI') and os.path.exists('.env.txt'):
    print("⚠️  AVISO: Cargando configuración desde .env.txt en database.py")
    load_dotenv('.env.txt')
    
    # FIX: Si .env.txt tiene solo la URL (sin DB_URI=), la leemos manualmente
    if not os.getenv('DB_URI'):
        try:
            with open('.env.txt', 'r') as f:
                content = f.read().strip()
                if content.startswith('postgresql://'):
                    os.environ['DB_URI'] = content
        except: pass

# --- CONFIGURACIÓN NEON (POSTGRESQL) ---
DB_URI = os.environ.get('DB_URI')

def get_db_connection():
    try:
        if not DB_URI: raise ValueError("La variable DB_URI está vacía o no se encontró.")
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

        # 14. ESPACIOS COMUNES
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

        # 16. LECTURAS DE MEDIDORES (NUEVO)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS lecturas_medidores (
                id SERIAL PRIMARY KEY,
                edificio_id INT,
                unidad_id INT,
                tipo VARCHAR(50),
                valor FLOAT,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                registrado_por VARCHAR(100)
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