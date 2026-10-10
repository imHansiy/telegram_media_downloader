"""The application shell must not depend on an external entry-script request."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import module.web as web


class SpaDeliveryTest(unittest.TestCase):
    def test_embeds_built_entry_and_escapes_script_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'newui'
            (root / 'assets').mkdir(parents=True)
            (root / 'assets' / 'entry.js').write_text('window.example="</script>";', encoding='utf-8')
            (root / 'index.html').write_text('<script type="module" crossorigin src="/static/newui/assets/entry.js"></script><div id="root"></div>', encoding='utf-8')
            app = web.get_flask_app()
            with patch.object(app, '_static_folder', directory), patch.object(web, '_bootstrap_payload', return_value={'config': {'fixture': '</script><b>'}, 'account': {'accounts': [{}, {}]}}), app.test_request_context('/files'):
                response = app.make_response(web._render_spa())
                response.direct_passthrough = False
                body = response.get_data(as_text=True)
                self.assertNotIn('src="/static/newui/assets/entry.js"', body)
                self.assertIn('window.example=', body)
                self.assertIn('id="app-bootstrap"', body)
                self.assertIn(r'\u003c/script\u003e', body)
                self.assertIn(r'<\/script>', body)
                self.assertIn('no-store', response.headers.get('Cache-Control', ''))

    def test_unauthenticated_page_does_not_disclose_bootstrap(self):
        app = web.get_flask_app()
        with patch.dict(app.config, LOGIN_DISABLED=False):
            response = app.test_client().get('/config')
            self.assertEqual(response.status_code, 302)
            self.assertNotIn('app-bootstrap', response.get_data(as_text=True))
