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
GMAIL_USER=gajoda.system@gmail.com
GMAIL_APP_PASSWORD=<gmail app password>
APP_BASE_URL=<public app URL>
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

`GMAIL_APP_PASSWORD` is the 16-character Gmail app password for `GMAIL_USER`. Keep it in environment variables only. `APP_BASE_URL` must match the public deployment URL so password reset links point to the live system.

## Database Setup

Run schema setup and starter data seeding as a one-time release task:

```sh
python -c "from app import initialize_database; initialize_database()"
```

The temporary starter admin account remains:

```text
username: admin
password: admin123
```

The password is stored as a hash after seeding or first login.

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
