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