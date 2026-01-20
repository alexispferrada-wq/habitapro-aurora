from app import app
import database

if __name__ == '__main__':
    print("🚀 Iniciando HABITEX Modular v2...")
    # Inicializar tablas (crear Marketplace y otras si faltan)
    database.inicializar_tablas()
    
    # Puedes cambiar el puerto si lo necesitas
    app.run(debug=True, port=5004)