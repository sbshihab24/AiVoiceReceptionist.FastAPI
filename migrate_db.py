from database import engine
from sqlalchemy import text

def migrate():
    with engine.connect() as conn:
        print("🔍 Checking columns in call_logs...")
        try:
            # Add 'reason' column
            conn.execute(text("ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS reason VARCHAR;"))
            print("✅ Added 'reason' column.")
            
            # Add 'duration' column
            conn.execute(text("ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS duration INTEGER;"))
            print("✅ Added 'duration' column.")
            
            conn.commit()
            print("🚀 Migration completed successfully!")
        except Exception as e:
            print(f"❌ Migration failed: {e}")

if __name__ == "__main__":
    migrate()
