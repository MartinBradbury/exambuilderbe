import importlib.util
import os
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS_PATH = Path(__file__).resolve().parent / 'settings.py'

STAGING_ENV = {
    'APP_ENV': 'staging',
    'SECRET_KEY': 'staging-test-secret',
    'DATABASE_URL': 'postgres://user:pass@db.invalid:5432/staging',
    'USE_LOCAL_DB': 'false',
}


def load_settings(env):
    """Execute settings.py in isolation with a controlled environment and no .env file."""
    spec = importlib.util.spec_from_file_location('exambuilder_settings_under_test', SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env, clear=True), patch('dotenv.load_dotenv'):
        spec.loader.exec_module(module)
    return module


class AllowedHostsAndOriginsTests(SimpleTestCase):
    def test_defaults_match_existing_production_values(self):
        settings = load_settings({'SECRET_KEY': 'x'})

        self.assertEqual(settings.APP_ENV, 'production')
        self.assertEqual(
            settings.ALLOWED_HOSTS,
            ['exambuilder-efae14d59f03.herokuapp.com', 'localhost', '127.0.0.1'],
        )
        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS,
            [
                'https://exambuilder-efae14d59f03.herokuapp.com',
                'http://localhost',
                'http://127.0.0.1',
            ],
        )

    def test_extra_allowed_hosts_are_appended_and_trimmed(self):
        settings = load_settings({
            'SECRET_KEY': 'x',
            'EXTRA_ALLOWED_HOSTS': ' staging.example.com, ,other.example.com ',
        })

        self.assertEqual(
            settings.ALLOWED_HOSTS,
            [
                'exambuilder-efae14d59f03.herokuapp.com',
                'localhost',
                '127.0.0.1',
                'staging.example.com',
                'other.example.com',
            ],
        )

    def test_extra_csrf_trusted_origins_are_appended(self):
        settings = load_settings({
            'SECRET_KEY': 'x',
            'EXTRA_CSRF_TRUSTED_ORIGINS': 'https://staging.example.com,https://app.staging.example.com',
        })

        self.assertEqual(
            settings.CSRF_TRUSTED_ORIGINS[-2:],
            ['https://staging.example.com', 'https://app.staging.example.com'],
        )
        self.assertIn('https://exambuilder-efae14d59f03.herokuapp.com', settings.CSRF_TRUSTED_ORIGINS)


class StagingSafeguardTests(SimpleTestCase):
    def test_valid_staging_configuration_loads(self):
        settings = load_settings({**STAGING_ENV, 'STRIPE_SECRET_KEY': 'sk_test_123'})

        self.assertEqual(settings.APP_ENV, 'staging')
        self.assertEqual(settings.STRIPE_SECRET_KEY, 'sk_test_123')
        self.assertIn('postgres', settings.DATABASES['default']['ENGINE'])

    def test_staging_without_stripe_key_is_allowed_and_leaves_stripe_unconfigured(self):
        settings = load_settings(STAGING_ENV)

        self.assertEqual(settings.STRIPE_SECRET_KEY, '')

    def test_staging_requires_secret_key(self):
        env = {key: value for key, value in STAGING_ENV.items() if key != 'SECRET_KEY'}

        with self.assertRaisesMessage(ImproperlyConfigured, 'SECRET_KEY'):
            load_settings(env)

    def test_staging_requires_database_url(self):
        env = {key: value for key, value in STAGING_ENV.items() if key != 'DATABASE_URL'}

        with self.assertRaisesMessage(ImproperlyConfigured, 'DATABASE_URL'):
            load_settings(env)

    def test_staging_rejects_explicit_local_database(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'USE_LOCAL_DB'):
            load_settings({**STAGING_ENV, 'USE_LOCAL_DB': 'true'})

    def test_staging_rejects_live_stripe_key_without_revealing_it(self):
        with self.assertRaises(ImproperlyConfigured) as context:
            load_settings({**STAGING_ENV, 'STRIPE_SECRET_KEY': 'sk_live_supersecret'})

        self.assertNotIn('supersecret', str(context.exception))

    def test_staging_rejects_any_non_test_stripe_key(self):
        with self.assertRaises(ImproperlyConfigured):
            load_settings({**STAGING_ENV, 'STRIPE_SECRET_KEY': 'rk_live_restricted'})

    def test_production_does_not_apply_staging_checks(self):
        settings = load_settings({'SECRET_KEY': 'x', 'STRIPE_SECRET_KEY': 'sk_live_not_checked'})

        self.assertEqual(settings.APP_ENV, 'production')
        self.assertEqual(settings.STRIPE_SECRET_KEY, 'sk_live_not_checked')
