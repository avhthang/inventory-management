from app import app, db
from sqlalchemy import text, inspect

def run_migration():
    with app.app_context():
        try:
            inspector = inspect(db.engine)
            
            with db.engine.connect() as conn:
                # Add edited_at to order_tracking
                if 'order_tracking' in inspector.get_table_names():
                    columns = {col['name'] for col in inspector.get_columns('order_tracking')}
                    if 'edited_at' not in columns:
                        conn.execute(text("ALTER TABLE order_tracking ADD COLUMN edited_at TIMESTAMP"))
                        print("Added edited_at to order_tracking")
                    else:
                        print("edited_at already exists in order_tracking")

                # Checking bug_report_comment just in case
                if 'bug_report_comment' in inspector.get_table_names():
                    columns = {col['name'] for col in inspector.get_columns('bug_report_comment')}
                    if 'edited_at' not in columns:
                        conn.execute(text("ALTER TABLE bug_report_comment ADD COLUMN edited_at TIMESTAMP"))
                        print("Added edited_at to bug_report_comment")
                    else:
                        print("edited_at already exists in bug_report_comment")

                conn.commit()
                print("Migrations applied successfully!")
        except Exception as e:
            print(f"Error during migration: {e}")

if __name__ == '__main__':
    run_migration()
