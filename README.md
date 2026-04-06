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
- Password reset email delivered through Brevo SMTP

Required Heroku config vars for password reset email:

- `PASSWORD_RESET_URL=https://alevelexambuilder.netlify.app/reset-password`
- `DEFAULT_FROM_EMAIL=moobradbury@hotmail.com`
- `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`
- `EMAIL_HOST=smtp-relay.brevo.com`
- `EMAIL_PORT=587`
- `EMAIL_HOST_USER=<Brevo SMTP login>`
- `EMAIL_HOST_PASSWORD=<Brevo SMTP password>`
- `EMAIL_USE_TLS=True`

Important Brevo note:

- `DEFAULT_FROM_EMAIL` must match a verified Brevo sender identity
- The Brevo account login email does not need to match the sender email
- In this setup, the verified sender is `moobradbury@hotmail.com`

If password reset returns a 500 for real accounts but succeeds for unknown emails, check Heroku logs first:

```powershell
heroku logs -n 200 -a exambuilder
```

Common causes:

- sender email is not verified in Brevo
- `DEFAULT_FROM_EMAIL` does not match the verified sender
- SMTP credentials are missing or invalid

Useful Heroku commands:

```powershell
heroku config -a exambuilder
heroku config:set DEFAULT_FROM_EMAIL=moobradbury@hotmail.com -a exambuilder
heroku logs -n 200 -a exambuilder
```

Security reminder:

- If an SMTP password, API key, or other secret is ever pasted into chat, logs, screenshots, or commits, rotate it immediately in the provider dashboard and update the Heroku config var with the new value.

## Stripe billing

Stripe is used to move a user from the free tier to the paid tier.
The backend creates a hosted Stripe Checkout session and waits for a webhook before updating the user's entitlement.

Current account fields used for billing:

- `plan_type` supports `free`, `paid`, and `lifetime`
- `stripe_customer_id`
- `stripe_checkout_session_id`
- `stripe_subscription_id`
- `paid_at`

API endpoints:

- `POST /accounts/billing/create-checkout-session/`
- `POST /accounts/billing/webhook/`

Required Stripe config vars:

- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_PRICE_ID`
- `STRIPE_CHECKOUT_MODE`
- `STRIPE_SUCCESS_URL`
- `STRIPE_CANCEL_URL`

Suggested defaults:

- `STRIPE_CHECKOUT_MODE=payment` for a one-time paid unlock
- `STRIPE_SUCCESS_URL=https://alevelexambuilder.netlify.app/account?checkout=success`
- `STRIPE_CANCEL_URL=https://alevelexambuilder.netlify.app/account?checkout=cancelled`

Webhook behavior:

- `checkout.session.completed` upgrades the user to `paid`
- `customer.subscription.updated` keeps the user on `paid` while the subscription is active
- `customer.subscription.deleted` downgrades the user back to `free`

Frontend flow:

1. Authenticated frontend calls `POST /accounts/billing/create-checkout-session/`
2. Backend returns a Stripe Checkout URL
3. Frontend redirects the user to that URL
4. Stripe sends a webhook to `POST /accounts/billing/webhook/`
5. Backend updates the user's entitlement
6. Frontend reads the updated `plan_type` from `GET /accounts/user/`