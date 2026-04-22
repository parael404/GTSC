# Deployment Notes

## Required Environment

Set these values on the deployment host before starting the app:

```text
SECRET_KEY=<long random value>
DB_HOST=<mysql host>
DB_PORT=<mysql port>
DB_USER=<mysql user>
DB_PASSWORD=<mysql password>
DB_NAME=<mysql database name>
FLASK_ENV=production
RESEND_API_KEY=<resend api key>
EMAIL_FROM=<verified sender address>
GMAIL_USER=gajoda.system@gmail.com
GMAIL_APP_PASSWORD=<gmail app password>
APP_BASE_URL=<public app URL>
INITIAL_ADMIN_USERNAME=<first admin username>
INITIAL_ADMIN_EMAIL=<first admin email>
INITIAL_ADMIN_PASSWORD=<first admin password>
INITIAL_ADMIN_FULL_NAME=<first admin display name>
```

`DB_NAME` may only contain letters, numbers, and underscores.

Railway's MySQL plugin also works with its default variable names:

```text
MYSQLHOST
MYSQLPORT
MYSQLUSER
MYSQLPASSWORD
MYSQLDATABASE
```

Keep `SECRET_KEY` and `FLASK_ENV=production` set manually in Railway variables.
Set the `INITIAL_ADMIN_*` values before the first production deploy so the
database bootstrap can create your first login without using public demo
credentials.

`RESEND_API_KEY` and `EMAIL_FROM` are preferred for password reset email in Railway because Resend sends through HTTPS. `EMAIL_FROM` must use a Resend-verified sender, such as `Gajoda TSC <onboarding@resend.dev>` for testing or an address on your verified domain for production.

`GMAIL_APP_PASSWORD` is the 16-character Gmail app password for `GMAIL_USER`. Gmail may display it in four groups with spaces; the app strips spaces automatically, but storing the 16 characters without spaces is preferred. Gmail SMTP remains a fallback when Resend is not configured, but some hosts block outbound SMTP ports. Keep all email credentials in environment variables only. `APP_BASE_URL` must match the public deployment URL so password reset links point to the live system.

## Database Setup

The included `Procfile` runs schema setup automatically before Gunicorn starts:

```sh
python -c "from app import initialize_database; initialize_database()"
```

In production, demo users are not created unless `CODEXMBS_SEED_DEMO_USERS=true`
is explicitly set. The first admin is created from the `INITIAL_ADMIN_*`
environment variables only when no admin or super admin exists yet.

## Start Command

Linux-style hosts can use the included `Procfile`:

```sh
gunicorn app:app --bind 0.0.0.0:$PORT
```

Railway will provide `$PORT` at runtime and use the `Procfile` automatically.

For Windows hosting, use Waitress:

```sh
waitress-serve --listen=0.0.0.0:8000 app:app
```
