# app/models.py
from flask_login import UserMixin
from app import db, login_manager

class Usuario(UserMixin, db.Model):
    __tablename__ = 'usuarios'
    rut = db.Column(db.String(20), primary_key=True)
    nombre = db.Column(db.String(100))
    email = db.Column(db.String(100))
    rol = db.Column(db.String(50))
    edificio_id = db.Column(db.Integer)
    activo = db.Column(db.Boolean, default=True)
    
    def get_id(self):
        return self.rut

@login_manager.user_loader
def load_user(user_rut):
    return db.session.get(Usuario, user_rut)