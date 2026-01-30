from flask import Blueprint, render_template, redirect, url_for, request, jsonify, session
from flask_login import login_required
from ..database import get_db_cursor

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def landing():
    # Siempre mostrar la landing page en la raíz
    return render_template('landing.html')

@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Redirigir a la lógica de paneles por rol definida en auth.home
    return redirect(url_for('auth.dashboard'))

@main_bp.route('/conserje/reservas/cambiar_estado', methods=['POST'])
@login_required
def conserje_reservas_estado():
    if session.get('rol') != 'conserje': return jsonify({'status': 'error'})
    
    rid = request.form.get('reserva_id')
    nuevo_estado = request.form.get('nuevo_estado') # EN_USO, FINALIZADA, ENTREGADO
    
    try:
        with get_db_cursor(commit=True) as cur:
            cur.execute("UPDATE reservas SET estado = %s WHERE id = %s", (nuevo_estado, rid))
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@main_bp.route('/manual/conserje')
def manual_conserje():
    return render_template('manual_conserje.html')

@main_bp.route('/manual/administrador')
def manual_admin():
    return render_template('manual_admin.html')

@main_bp.route('/manual/residente')
def manual_residente():
    return render_template('manual_residente.html')
