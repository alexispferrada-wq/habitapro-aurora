from app import create_app

# Crea la aplicación usando la fábrica que definimos arriba
app = create_app()

if __name__ == '__main__':
    print("🚀 Iniciando Habipro Modular v2...")
    # Puedes cambiar el puerto si lo necesitas
    app.run(debug=True, port=5004)