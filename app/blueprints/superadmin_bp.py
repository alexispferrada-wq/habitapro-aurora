from flask import Blueprint, request, flash, redirect, url_for
from flask_login import login_required, current_user
from app.database import get_db_connection

superadmin_bp = Blueprint('superadmin', __name__)

@superadmin_bp.route('/broadcast_message', methods=['POST'])
@login_required
def superadmin_broadcast():
    """
    Permite al Super Administrador enviar un comunicado a la bitácora de todos los edificios activos.
    """
    if current_user.rol != 'superadmin':
        flash("No autorizado para realizar esta acción.", "error")
        return redirect(url_for('login'))

    titulo = request.form.get('titulo')
    mensaje = request.form.get('mensaje')
    
    if not titulo or not mensaje:
        flash("Título y mensaje son obligatorios.", "error")
        # 'panel_superadmin' se asume como un endpoint global o que será movido al blueprint
        return redirect(url_for('panel_superadmin')) 

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Obtener todos los edificios activos
        cur.execute("SELECT id FROM edificios WHERE activo = TRUE")
        active_buildings = cur.fetchall()

        for building in active_buildings:
            cur.execute("""
                INSERT INTO incidencias (edificio_id, titulo, descripcion, fecha, autor)
                VALUES (%s, %s, %s, NOW(), %s)
            """, (building['id'], f"COMUNICADO GLOBAL: {titulo}", mensaje, current_user.nombre))
        conn.commit()
        flash("✅ Comunicado global enviado a todos los edificios activos.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"❌ Error al enviar el comunicado global: {e}", "error")
    finally:
        cur.close()
        conn.close()
    
    return redirect(url_for('panel_superadmin'))