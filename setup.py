from database import create_tables_and_seed

if __name__ == "__main__":
    print("⚠️ INICIANDO CONFIGURACIÓN DE BASE DE DATOS NEON...")
    create_tables_and_seed()
    print("🚀 PROCESO FINALIZADO. AHORA PUEDES EJECUTAR APP.PY")