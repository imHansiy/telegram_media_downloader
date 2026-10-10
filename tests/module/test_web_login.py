"""Login must work when browser script requests are unavailable."""
import unittest
from unittest.mock import patch

import module.web as web


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.client = web.get_flask_app().test_client()
        self.secret = patch.dict(web.web_login_users, {"root": "test-only-password"}, clear=True)
        self.secret.start()
        self.addCleanup(self.secret.stop)

    def test_native_form_login_sets_session(self):
        response = self.client.post('/login', data={'password': 'test-only-password'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/files')
        with self.client.session_transaction() as session:
            self.assertEqual(session.get('_user_id'), 'root')

    def test_wrong_form_password_is_not_authenticated(self):
        response = self.client.post('/login', data={'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertIn('管理密码不正确', response.get_data(as_text=True))
        with self.client.session_transaction() as session:
            self.assertNotIn('_user_id', session)

    def test_page_has_post_fallback(self):
        page = self.client.get('/login').get_data(as_text=True)
        self.assertIn('method="post"', page)
        self.assertIn('name="password"', page)

    def test_malformed_json_is_rejected(self):
        response = self.client.post('/login', json={'password': 'bad-cipher'})
        self.assertEqual(response.json, {'code': '0'})

    def test_existing_encrypted_login_still_works(self):
        password = web.deAesCrypt.encrypt('test-only-password').decode('ascii')
        response = self.client.post('/login', json={'password': password})
        self.assertEqual(response.json, {'code': '1'})
