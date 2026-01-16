
import unittest
from app import create_app, db
from app.models import User, Role, Edificio
from flask import url_for

class SuperAdminTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app('testing')
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        Role.insert_roles()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_superadmin_ghost_login(self):
        # Create a superadmin user
        s = User(rut='1-9', nombre='super', email='super@example.com', role=Role.query.filter_by(name='SuperAdmin').first())
        s.set_password('super')
        # Create a regular admin user
        u = User(rut='2-7', nombre='admin', email='admin@example.com', role=Role.query.filter_by(name='Admin').first())
        u.set_password('admin')
        db.session.add_all([s, u])
        db.session.commit()

        # Log in as superadmin
        self.client.post(url_for('auth.login'), data={
            'rut': '1-9',
            'password': 'super'
        })

        # Try to ghost login as the admin user
        response = self.client.post(url_for('superadmin.superadmin_ghost_login', user_rut=u.rut), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        # Check that we are now logged in as the admin user
        self.assertIn(b'Panel de Administraci\xc3\xb3n', response.data)
        self.assertIn(b'admin', response.data)
