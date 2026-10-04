import os

DATABASE_URL = os.getenv("DATABASE_URL")


def get_users(cursor):
    cursor.execute("SELECT * FROM users")
