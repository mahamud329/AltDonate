# sync_app.py - Google Sheets Sync Application
import gspread
import os
import time
import logging
import threading
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, jsonify, request
from werkzeug.security import generate_password_hash
from sqlalchemy import text

# Import shared models from models.py
from models import (
    engine, Base, Session, User, Log, Donor,
    init_database_logger, get_database_logger
)

# --- Setup Logging ---
logging.basicConfig(
    level=logging.ERROR,  # Changed from INFO to ERROR
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('sync_server')

# Add database handler to logger
db_handler = init_database_logger()
logger.addHandler(db_handler)

# --- Flask App Setup ---
app = Flask(__name__)

# --- Create the log cleaning function ---
def create_log_cleanup_function():
    session = Session()
    try:
        # Check if the function already exists - using text() wrapper here
        check_fn = session.execute(text("SELECT to_regproc('cleanup_old_logs()') IS NOT NULL"))
        if check_fn.scalar():
            # Removed INFO log message here
            return

        # Create the function - using text() wrapper for all SQL statements
        session.execute(text("""
        CREATE OR REPLACE FUNCTION cleanup_old_logs() RETURNS void AS $$
        BEGIN
            DELETE FROM logs WHERE timestamp < NOW() - INTERVAL '15 days';
        END;
        $$ LANGUAGE plpgsql;
        """))

        # Create a daily trigger if it doesn't exist - using text() wrapper
        session.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trigger_cleanup_old_logs') THEN
                CREATE EXTENSION IF NOT EXISTS pg_cron;
                SELECT cron.schedule('0 0 * * *', 'SELECT cleanup_old_logs()');
            END IF;
        EXCEPTION WHEN OTHERS THEN
            -- If pg_cron is not available, inform but don't fail
            RAISE NOTICE 'pg_cron extension not available. Log cleanup will not be automated.';
        END
        $$;
        """))

        session.commit()
        # Removed INFO log message here
    except Exception as e:
        logger.error(f"Error creating log cleanup function: {e}")
        session.rollback()
    finally:
        session.close()

# --- Google Sheets Connection Helper ---
def get_sheets_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets"
    ])
    return gspread.authorize(creds)

# --- Sync Google Sheets to Database ---
def sync_donors_from_sheets():
    """Sync all donors from Google Sheets to the database"""
    # logger.info("[SYNC] Starting Google Sheets sync")  # Commented out
    try:
        client = get_sheets_client()
        sheet = client.open("AltDonate").worksheet("Form_Responses")

        records = sheet.get_all_records()
        
        # Check if there are any records to process
        if not records:
            logger.error("[SYNC] No records found in the spreadsheet")
            return
            
        # Check for required columns
        required_columns = ["Phone Number", "Display Name"]
        if records:
            spreadsheet_columns = records[0].keys()
            missing_columns = [col for col in required_columns if col not in spreadsheet_columns]
            if missing_columns:
                logger.error(f"[SYNC] Missing required columns in spreadsheet: {missing_columns}")
                return  # Exit function as we can't proceed without required columns

        new_count = 0
        update_count = 0

        session = Session()
        try:
            for row in records:
                phone = str(row.get("Phone Number", "")).strip().lstrip("0")
                name = row.get("Display Name", "Anonymous")

                if phone and name:
                    # Check if donor exists
                    donor = session.query(Donor).filter_by(phone_number=phone).first()
                    if donor:
                        # Update if name changed
                        if donor.display_name != name:
                            donor.display_name = name
                            update_count += 1
                    else:
                        # Create new donor
                        donor = Donor(phone_number=phone, display_name=name)
                        session.add(donor)
                        new_count += 1

            session.commit()
            # logger.info(f"[SYNC] Completed: Added {new_count} new donors, updated {update_count} existing donors")  # Commented out
        except Exception as e:
            session.rollback()
            logger.error(f"[SYNC] Database commit error: {e}")
        finally:
            session.close()

    except Exception as e:
        logger.error(f"[SYNC] Google Sheets sync error: {e}")

# --- Sync Thread Function ---
def sync_thread_function():
    """Background thread for periodic syncing"""
    while True:
        try:
            sync_donors_from_sheets()
        except Exception as e:
            logger.error(f"[SYNC] Error in sync thread: {e}")

        # Wait 3600 seconds before next sync
        time.sleep(3600)

# --- Flask Routes ---
@app.route("/")
def index():
    return jsonify({
        "status": "running",
        "service": "Google Sheets Sync Service",
        "version": "1.0.0"
    })

@app.route("/sync", methods=["POST"])
def trigger_sync():
    """Manually trigger a sync operation"""
    try:
        sync_donors_from_sheets()
        return jsonify({
            "status": "success",
            "message": "Sync completed"
        })
    except Exception as e:
        logger.error(f"[API] Error during manual sync: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/donor", methods=["POST"])
def add_donor():
    """Add or update a single donor"""
    try:
        data = request.get_json()
        if not data or not data.get("phone") or not data.get("name"):
            return jsonify({
                "status": "error",
                "message": "Missing required fields"
            }), 400
            
        phone = str(data["phone"]).strip().lstrip("0")
        name = data["name"]
        
        session = Session()
        try:
            # Check if donor exists
            donor = session.query(Donor).filter_by(phone_number=phone).first()
            if donor:
                # Update if name changed
                if donor.display_name != name:
                    donor.display_name = name
                    status = "updated"
                else:
                    status = "unchanged"
            else:
                # Create new donor
                donor = Donor(phone_number=phone, display_name=name)
                session.add(donor)
                status = "created"
                
            session.commit()
            logger.error(f"[API] Donor {status}: {name} ({phone})")  # Using ERROR level since INFO is filtered
            return jsonify({
                "status": "success",
                "action": status
            })
        except Exception as e:
            session.rollback()
            logger.error(f"[API] Database error: {e}")
            return jsonify({
                "status": "error",
                "message": str(e)
            }), 500
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[API] Error adding donor: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route("/stats")
def stats():
    """Get sync statistics"""
    session = Session()
    try:
        donor_count = session.query(Donor).count()
        last_log = session.query(Log).filter(Log.message.like('%SYNC%Completed%')).order_by(Log.timestamp.desc()).first()

        return jsonify({
            "total_donors": donor_count,
            "last_sync": last_log.timestamp.isoformat() if last_log else None
        })
    except Exception as e:
        logger.error(f"[API] Error getting stats: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    finally:
        session.close()

# --- Main ---
if __name__ == '__main__':
    # Create the database log cleanup function
    create_log_cleanup_function()

    # Start the sync thread
    sync_thread = threading.Thread(target=sync_thread_function)
    sync_thread.daemon = True
    sync_thread.start()
    # logger.info("Sync thread started")  # Commented out

    # Run the Flask app on port 5004 as requested
    # logger.info(f"Server running on port 5004")  # Commented out
    app.run(host='0.0.0.0', port=5004, debug=False, threaded=True)