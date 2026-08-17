import sqlite3
import os
import sys

# Add the project root to the path so we can import src.constants
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from src.constants import APP_DB
except ImportError:
    # Fallback if src.constants fails
    APP_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "app.db"))

def migrate():
    print(f"Migrating database at: {APP_DB}")
    if not os.path.exists(APP_DB):
        print(f"Database not found at {APP_DB}. Are you running this from the right directory?")
        return

    conn = sqlite3.connect(APP_DB)
    cursor = conn.cursor()

    columns_to_add = [
        ("duration", "FLOAT"),
        ("fps", "FLOAT"),
        ("source_media_id", "VARCHAR"),
        ("generation_mode", "VARCHAR")
    ]

    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE studio_media ADD COLUMN {col_name} {col_type}")
            print(f"Added column {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column {col_name} already exists")
            else:
                print(f"Error adding {col_name}: {e}")

    conn.commit()
    conn.close()
    print("Migration completed successfully.")

if __name__ == "__main__":
    migrate()
