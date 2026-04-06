# exambuilderbe

openAI: https://platform.openai.com/settings/profile/user
google account

supabase db
heroku hosting backend
netlify frontend

## Local database safety

Local development now defaults to SQLite in `db.sqlite3`, even if `DATABASE_URL` is present in your shell or `.env`.

- Local default: `USE_LOCAL_DB=True`
- Heroku default: `USE_LOCAL_DB=False` because `DYNO` is set
- To force the remote database locally, set `USE_LOCAL_DB=False`

Recommended local flow before touching production:

```powershell
python manage.py migrate
python manage.py runserver
```

Only point at the live database when you are ready to run production migrations.

## Password reset

API endpoints:

- `POST /accounts/password-reset/`
- `POST /accounts/password-reset/confirm/`

Requesting a reset sends an email with a frontend link built from `PASSWORD_RESET_URL`.
If `PASSWORD_RESET_URL` is not set, the backend uses `FRONTEND_URL/reset-password`.

Email settings are environment-driven:

- `DEFAULT_FROM_EMAIL`
- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`

Local development defaults to Django's console email backend, so reset emails print in the server logs unless you override the backend.