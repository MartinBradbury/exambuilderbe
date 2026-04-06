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
- `PASSWORD_RESET_URL`
- `FRONTEND_URL`
- `EMAIL_BACKEND`
- `EMAIL_HOST`
- `EMAIL_PORT`
- `EMAIL_HOST_USER`
- `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`

Local development defaults to Django's console email backend, so reset emails print in the server logs unless you override the backend.

### Production password reset setup

Password reset emails are sent from Heroku using Django SMTP settings.
In production, the backend uses the database from `DATABASE_URL` and email settings from Heroku config vars.

Current production flow:

- Backend hosted on Heroku
- Database hosted on Supabase via `DATABASE_URL`
- Frontend hosted on Netlify
- Password reset email delivered through SendGrid SMTP

Required Heroku config vars for password reset email:

- `PASSWORD_RESET_URL=https://alevelexambuilder.netlify.app/reset-password`
- `DEFAULT_FROM_EMAIL=moobradbury@hotmail.com`
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST=smtp.sendgrid.net`
- `EMAIL_PORT=587`
- `EMAIL_HOST_USER=apikey`
- `EMAIL_HOST_PASSWORD=<SendGrid API key>`
- `EMAIL_USE_TLS=True`

Important SendGrid note:

- `DEFAULT_FROM_EMAIL` must match a verified SendGrid sender identity
- The SendGrid account login email does not need to match the sender email
- In this setup, the verified single sender is `moobradbury@hotmail.com`

If password reset returns a 500 for real accounts but succeeds for unknown emails, check Heroku logs first:

```powershell
heroku logs -n 200 -a exambuilder
```

Common causes:

- sender email is not verified in SendGrid
- `DEFAULT_FROM_EMAIL` does not match the verified sender
- SMTP credentials are missing or invalid

Useful Heroku commands:

```powershell
heroku config -a exambuilder
heroku config:set DEFAULT_FROM_EMAIL=moobradbury@hotmail.com -a exambuilder
heroku logs -n 200 -a exambuilder
```