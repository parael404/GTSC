web: python -c "from app import initialize_database; initialize_database()" && gunicorn app:app --worker-class gthread --threads 100 --bind 0.0.0.0:$PORT
