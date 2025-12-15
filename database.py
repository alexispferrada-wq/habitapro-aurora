import psycopg2
from psycopg2.extras import RealDictCursor
import os

# --- CONFIGURACIÓN NEON (POSTGRESQL) ---
# Usamos os.environ.get para producción, o el string directo para desarrollo
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://neondb_owner:npg_sQAbR0FX4oYz@ep-curly-wildflower-acyole1h-pooler.sa-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require')

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    except Exception as e:
        print(f"❌ Error crítico conectando a la BD: {e}")
        return None

def inicializar_tablas():
    conn = get_db_connection()
    if not conn:
        return
    
    cur = None # Inicializamos vacía por seguridad
    try:
        cur = conn.cursor() # <--- AQUÍ SE CREA EL CURSOR
        
        # 1. PARCHE SEGURIDAD ROLES (Eliminar restricción vieja)
        try:
            cur.execute("ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_rol_check;")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Info: {e}")

        # 2. TABLAS PRINCIPALES
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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS edificios (
                id SERIAL PRIMARY KEY,
                nombre VARCHAR(100),
                direccion VARCHAR(200),
                lat FLOAT,
                lon FLOAT,
                deuda_omnisoft INT DEFAULT 0,
                estado_pago VARCHAR(20) DEFAULT 'PENDIENTE',
                activo BOOLEAN DEFAULT TRUE 
            );
        """)
        # Parche columna activo para soft delete
        try:
            cur.execute("ALTER TABLE edificios ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT TRUE;")
            conn.commit()
        except: conn.rollback()

        # 3. RESTO DE TABLAS
        cur.execute("""CREATE TABLE IF NOT EXISTS unidades (id SERIAL PRIMARY KEY, edificio_id INT, numero VARCHAR(10), piso INT, metraje INT, prorrateo FLOAT, estacionamiento VARCHAR(20), bodega VARCHAR(20), owner_json TEXT, tenant_json TEXT, broker_json TEXT, deuda_monto INT DEFAULT 0, estado_deuda VARCHAR(20) DEFAULT 'AL_DIA');""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS gastos (id SERIAL PRIMARY KEY, edificio_id INT, categoria VARCHAR(50), descripcion TEXT, monto INT, fecha DATE, mes INT, anio INT, comprobante_url TEXT, cerrado BOOLEAN DEFAULT FALSE);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS historial_pagos (id SERIAL PRIMARY KEY, edificio_id INT, unidad_id INT, monto INT, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metodo VARCHAR(50), comprobante_url TEXT, mes_periodo INT, anio_periodo INT);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS multas (id SERIAL PRIMARY KEY, edificio_id INT, unidad_id INT, monto INT, motivo TEXT, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS encomiendas (id SERIAL PRIMARY KEY, edificio_id INT, unidad_id INT, remitente VARCHAR(100), recepcion TIMESTAMP DEFAULT CURRENT_TIMESTAMP, entrega TIMESTAMP);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS visitas (id SERIAL PRIMARY KEY, edificio_id INT, unidad_id INT, rut VARCHAR(20), nombre_visita VARCHAR(100), patente VARCHAR(20), estacionamiento_id VARCHAR(10), ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP, salida TIMESTAMP);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS incidencias (id SERIAL PRIMARY KEY, edificio_id INT, titulo VARCHAR(100), descripcion TEXT, fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP, autor VARCHAR(100));""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS activos (id SERIAL PRIMARY KEY, edificio_id INT, nombre VARCHAR(100), periodicidad_dias INT, costo_estimado INT, ultimo_servicio DATE);""")
        
        cur.execute("""CREATE TABLE IF NOT EXISTS cierres_mes (id SERIAL PRIMARY KEY, edificio_id INT, mes INT, anio INT, total_gastos INT, admin_responsable VARCHAR(100), fecha_cierre TIMESTAMP DEFAULT CURRENT_TIMESTAMP);""")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS invitaciones (
                id SERIAL PRIMARY KEY,
                token VARCHAR(64) UNIQUE NOT NULL,
                edificio_id INT,
                unidad_id INT,
                nombre_visita VARCHAR(100),
                rut_visita VARCHAR(20),
                patente VARCHAR(20),
                estado VARCHAR(20) DEFAULT 'PENDIENTE',
                fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fecha_uso TIMESTAMP
            );
        """)

        # 4. MÓDULO ESPACIOS COMUNES
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

        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservas (
                id SERIAL PRIMARY KEY,
                espacio_id INT,
                unidad_id INT,
                fecha_uso DATE,
                estado VARCHAR(20) DEFAULT 'CONFIRMADA', 
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        try:
            cur.execute("ALTER TABLE invitaciones ADD COLUMN IF NOT EXISTS tipo VARCHAR(20) DEFAULT 'PEATON';")
            cur.execute("ALTER TABLE invitaciones ADD COLUMN IF NOT EXISTS pre_nombre VARCHAR(100);") # Nombre pre-llenado
        except: pass
        


        conn.commit()
        print("✅ Base de datos inicializada correctamente.")
        
    except Exception as e:
        print(f"❌ Error en inicializar_tablas: {e}")
        if conn: conn.rollback()
    finally:
        if cur: cur.close()
        if conn: conn.close()



if __name__ == "__main__":
    inicializar_tablas()