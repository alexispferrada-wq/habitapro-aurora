from flask import Blueprint, render_template, redirect, url_for, request, jsonify, session, flash
from flask_login import login_required
from ..database import get_db_cursor
import smtplib
import ssl
from email.message import EmailMessage
import os
import requests
from datetime import date
import socket

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

def enviar_correo_contacto(name, email, whatsapp, unidades):
    try:
        mail_sender = os.getenv('MAIL_USERNAME')
        mail_password = os.getenv('MAIL_PASSWORD')
        mail_receiver = os.getenv('MAIL_RECEIVER', 'alexispferrada@gmail.com')

        if not mail_sender or not mail_password:
            print("⚠️ Credenciales de correo no configuradas.")
            return False

        # Generar Link Inteligente para Cotización
        link_cotizacion = url_for('main.generacion_cotizacion', 
                                  nombre=name, 
                                  email=email, 
                                  whatsapp=whatsapp, 
                                  unidades=unidades, 
                                  _external=True)

        subject = f"Nuevo Prospecto: {name}"
        body = f"""
        Nuevo contacto desde la web:
        
        Nombre: {name}
        Email: {email}
        WhatsApp: {whatsapp}
        Unidades: {unidades}
        
        ------------------------------------------------
        ⚡ GENERAR COTIZACIÓN AHORA:
        {link_cotizacion}
        """

        em = EmailMessage()
        em['From'] = mail_sender
        em['To'] = mail_receiver
        em['Subject'] = subject
        em.set_content(body)

        context = ssl.create_default_context()
        mail_server = os.getenv('MAIL_SERVER') or 'smtp.gmail.com'
        # Limpieza robusta de la URL del servidor
        mail_server = mail_server.replace("http://", "").replace("https://", "").replace("ssl://", "").strip()
        
        try:
            mail_port = int(os.getenv('MAIL_PORT', 587))
        except ValueError:
            mail_port = 587

        # FIX CRÍTICO RENDER: El puerto 465 suele estar bloqueado (Errno 101). 
        # Forzamos 587 (STARTTLS) que es el estándar para entornos Cloud.
        if mail_port == 465:
            print("⚠️ Puerto 465 detectado. Cambiando a 587 para evitar bloqueo de red en Render.")
            mail_port = 587
            
        print(f"📧 Intentando conectar a SMTP: {mail_server}:{mail_port}")

        # ESTRATEGIA DE CONEXIÓN ROBUSTA (Hostname -> IPv4 -> Fallback Puerto 25)
        smtp_conn = None
        last_error = None
        
        # Puertos a probar: El configurado (idealmente 587) y 25 como fallback
        ports_to_try = [mail_port]
        if mail_port == 587 and 25 not in ports_to_try:
            ports_to_try.append(25)

        for port in ports_to_try:
            if smtp_conn: break
            print(f"🔄 Probando conexión SMTP en puerto {port}...")

            # 1. Intento Estándar (Hostname)
            try:
                smtp_conn = smtplib.SMTP(mail_server, port, timeout=20)
                print(f"✅ Conexión establecida con {mail_server}:{port}")
            except Exception as e:
                print(f"⚠️ Falló conexión estándar: {e}")
                last_error = e
            
            # 2. Intento IPv4 Forzado (Si falló estándar)
            if not smtp_conn:
                try:
                    print(f"🔍 Resolviendo IPs IPv4 para {mail_server}...")
                    addr_info = socket.getaddrinfo(mail_server, port, socket.AF_INET, socket.SOCK_STREAM)
                    ips = list(dict.fromkeys([ai[4][0] for ai in addr_info]))
                    for ip in ips:
                        try:
                            print(f"⏳ Probando IP {ip}:{port}...")
                            smtp_conn = smtplib.SMTP(ip, port, timeout=20)
                            smtp_conn._host = mail_server # Restaurar host para SSL
                            print(f"✅ Conexión establecida a IP {ip}:{port}")
                            break
                        except Exception as e:
                            print(f"⚠️ Falló IP {ip}: {e}")
                            last_error = e
                except Exception as e:
                    print(f"⚠️ Error resolución DNS: {e}")

        if not smtp_conn:
            raise Exception(f"No se pudo conectar a SMTP. Último error: {last_error}")

        with smtp_conn as smtp:
            try:
                smtp.starttls(context=context)
                smtp.login(mail_sender, mail_password)
                smtp.sendmail(mail_sender, mail_receiver, em.as_string())
            except Exception as e:
                print(f"🔥 Error en sesión SMTP: {e}")
                raise e
        
        return True
    except Exception as e:
        print(f"🔥 Error enviando correo: {e}")
        import traceback
        traceback.print_exc()
        return False

@main_bp.route('/send_contact_form', methods=['GET', 'POST'])
def send_contact_form():
    if request.method != 'POST':
        return redirect(url_for('main.landing'))

    name = request.form.get('name')
    email = request.form.get('email')
    whatsapp = request.form.get('whatsapp')
    unidades = request.form.get('unidades')

    if enviar_correo_contacto(name, email, whatsapp, unidades):
        flash('¡Gracias! Hemos recibido tu solicitud y te contactaremos pronto.', 'success')
    else:
        flash('Hubo un problema al enviar el correo. Por favor intenta más tarde.', 'error')
        
    return redirect(url_for('main.landing') + '#contacto')

@main_bp.route('/generacion_cotizacion')
def generacion_cotizacion():
    # Capturamos los datos que vienen por la URL (desde el correo)
    nombre = request.args.get('nombre', '')
    email = request.args.get('email', '')
    whatsapp = request.args.get('whatsapp', '')
    unidades = request.args.get('unidades', '')
    
    return render_template('generacion_cotizacion.html', 
                           nombre=nombre, email=email, 
                           whatsapp=whatsapp, unidades=unidades,
                           indicadores=obtener_indicadores())

@main_bp.route('/legal/contrato_servicios')
def ver_contrato_servicios():
    return render_template('contrato_servicios.html', fecha=date.today())

@main_bp.route('/legal/licencia_uso')
def ver_licencia_uso():
    return render_template('licencia_uso.html', fecha=date.today())
