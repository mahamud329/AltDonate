import os
import logging
from datetime import datetime, timedelta, date
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean, func, desc, Float, Date, ForeignKey, Numeric, text
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base, relationship

# --- Database Configuration ---
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://altdonate:altdbpg4646@localhost:5432/altdonate")
engine = create_engine(DATABASE_URL)
Base = declarative_base()

# Create session factory
SessionFactory = sessionmaker(bind=engine)
Session = scoped_session(SessionFactory)

# --- ORM Models ---
class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    token = Column(String(100), unique=True, nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    last_login = Column(DateTime, nullable=True)
    
    # Relationships
    donations = relationship("DonationLog", back_populates="streamer")
    daily_totals = relationship("DailyDonationTotal", back_populates="streamer")

class Log(Base):
    __tablename__ = 'logs'
    id = Column(Integer, primary_key=True)
    level = Column(String(10), nullable=False)
    message = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=func.now())

class Donor(Base):
    __tablename__ = 'donors'
    id = Column(Integer, primary_key=True)
    phone_number = Column(String(20), unique=True, nullable=False)
    display_name = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # Relationships
    donations = relationship("DonationLog", back_populates="donor")

class AdminUser(Base):
    __tablename__ = 'admin_users'
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=func.now())

class DonationLog(Base):
    __tablename__ = 'donation_logs'
    id = Column(Integer, primary_key=True)
    streamer_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    username = Column(String(50), nullable=False)
    donor_phone = Column(String(20), ForeignKey('donors.phone_number', ondelete='SET NULL'), nullable=True)
    donor_name = Column(String(100), default='Anonymous')
    payment_method = Column(String(20), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    timestamp = Column(DateTime, default=func.now())
    message = Column(Text)
    
    # Relationships
    streamer = relationship("User", back_populates="donations")
    donor = relationship("Donor", back_populates="donations")

class DailyDonationTotal(Base):
    __tablename__ = 'daily_donation_totals'
    id = Column(Integer, primary_key=True)
    streamer_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    donation_date = Column(Date, nullable=False)
    total_amount = Column(Numeric(10, 2), default=0.00, nullable=False)
    donation_count = Column(Integer, default=0, nullable=False)
    
    streamer = relationship("User", back_populates="daily_totals")

class TodaysSupporter(Base):
    __tablename__ = 'todays_supporters'
    id = Column(Integer, primary_key=True)
    streamer_id = Column(Integer)
    donor_phone = Column(String(20))
    donor_name = Column(String(100))
    total_amount = Column(Numeric(10, 2))
    rank = Column(Integer)
    __table_args__ = {'info': {'is_view': True}}

class WeeklyTopSupporter(Base):
    __tablename__ = 'weekly_top_supporters'
    id = Column(Integer, primary_key=True)
    streamer_id = Column(Integer)
    week_start_date = Column(Date)
    week_end_date = Column(Date)
    donor_phone = Column(String(20))
    donor_name = Column(String(100))
    total_amount = Column(Numeric(10, 2))
    rank = Column(Integer)
    __table_args__ = {'info': {'is_view': True}}

class MonthlyTopSupporter(Base):
    __tablename__ = 'monthly_top_supporters'
    id = Column(Integer, primary_key=True)
    streamer_id = Column(Integer)
    month_start_date = Column(Date)
    month_end_date = Column(Date)
    donor_phone = Column(String(20))
    donor_name = Column(String(100))
    total_amount = Column(Numeric(10, 2))
    rank = Column(Integer)
    __table_args__ = {'info': {'is_view': True}}

# --- Config ---
class Config(Base):
    __tablename__ = 'config'
    key = Column(String(50), primary_key=True)
    value = Column(Text, nullable=False)

class DatabaseLogHandler(logging.Handler):
    def emit(self, record):
        session = Session()
        try:
            log = Log(level=record.levelname, message=record.getMessage())
            session.add(log)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"Error writing to log database: {e}")
        finally:
            session.close()

def init_database_logger():
    """Initialize and return a database log handler"""
    handler = DatabaseLogHandler()
    handler.setLevel(logging.ERROR)
    return handler

def get_database_logger():
    """Get a logger that writes to the database"""
    logger = logging.getLogger('database')
    handler = DatabaseLogHandler()
    handler.setLevel(logging.ERROR)
    logger.addHandler(handler)
    return logger

def get_week_dates(date=None):
    if date is None:
        date = datetime.now().date()
    current_weekday = date.weekday()
    if current_weekday < 5:
        days_since_saturday = current_weekday + 2
    else:
        days_since_saturday = current_weekday - 5
    week_start = date - timedelta(days=days_since_saturday)
    week_end = week_start + timedelta(days=6)
    return week_start, week_end

def get_month_dates(date=None):
    if date is None:
        date = datetime.now().date()
    month_start = date.replace(day=1)
    if date.month == 12:
        next_month = date.replace(year=date.year + 1, month=1, day=1)
    else:
        next_month = date.replace(month=date.month + 1, day=1)
    month_end = next_month - timedelta(days=1)
    return month_start, month_end

def log_donation(session, streamer_id, username, donor_phone, donor_name, payment_method, amount, message):
    """Log a donation to the database"""
    donation = DonationLog(
        streamer_id=streamer_id,
        username=username,
        donor_phone=donor_phone,
        donor_name=donor_name or "Anonymous",
        payment_method=payment_method,
        amount=amount,
        message=message
    )
    session.add(donation)
    session.commit()
    return donation

# --- Data Retrieval Functions (Unchanged Public Interface) ---
def get_top_supporters(session, streamer_id):
    """Get current weekly and monthly top 3 supporters for a streamer"""
    today = datetime.now().date()
    week_start, week_end = get_week_dates(today)
    month_start, month_end = get_month_dates(today)
    
    # These queries now seamlessly target the views instead of tables.
    weekly_supporters = session.query(WeeklyTopSupporter).filter_by(
        streamer_id=streamer_id
    ).order_by(WeeklyTopSupporter.rank).limit(3).all()
    
    monthly_supporters = session.query(MonthlyTopSupporter).filter_by(
        streamer_id=streamer_id
    ).order_by(MonthlyTopSupporter.rank).limit(3).all()
    
    # Formatting logic remains the same.
    weekly_result = [{
        "rank": s.rank, 
        "name": s.donor_name, 
        "amount": float(s.total_amount)} for s in weekly_supporters]
    while len(weekly_result) < 3:
        weekly_result.append({
            "rank": len(weekly_result) + 1, 
            "name": "No donations yet", 
            "amount": 0.0})
        
    monthly_result = [{
        "rank": s.rank, 
        "name": s.donor_name, 
        "amount": float(s.total_amount)} for s in monthly_supporters]
    while len(monthly_result) < 3:
        monthly_result.append({
            "rank": len(monthly_result) + 1, 
            "name": "No donations yet", 
            "amount": 0.0})
        
    return {
        "weekly": {
            "supporters": weekly_result,
            "start_date": week_start.strftime("%Y-%m-%d"),
            "end_date": week_end.strftime("%Y-%m-%d")
        },
        "monthly": {
            "supporters": monthly_result,
            "start_date": month_start.strftime("%Y-%m-%d"),
            "end_date": month_end.strftime("%Y-%m-%d")
        }
    }

def get_streamer_earnings(session, streamer_id=None):
    """Get total earnings and donation count for a streamer or all streamers"""
    query = session.query(
        User.id,
        User.username,
        func.coalesce(func.sum(DailyDonationTotal.total_amount), 0).label('total_earnings'),
        func.coalesce(func.sum(DailyDonationTotal.donation_count), 0).label('donation_count')
    ).outerjoin(DailyDonationTotal)

    if streamer_id:
        query = query.filter(User.id == streamer_id)

    return query.group_by(User.id, User.username).all()

def get_retention_days(session):
    """Get the current donation retention period in days"""
    config = session.query(Config).filter_by(key='donation_retention_days').first()
    if not config:
        # Create default config if it doesn't exist
        config = Config(key='donation_retention_days', value='7')
        session.add(config)
        session.commit()
        return 7
    return int(config.value)

def set_retention_days(session, days):
    """Set the donation retention period in days"""
    if not isinstance(days, int) or days < 1:
        raise ValueError("Retention days must be a positive integer")
        
    config = session.query(Config).filter_by(key='donation_retention_days').first()
    if not config:
        config = Config(key='donation_retention_days', value=str(days))
        session.add(config)
    else:
        config.value = str(days)
    session.commit()
    return days    

def get_monthly_donation_stats(session, streamer_id):
    """Get monthly donation statistics for a streamer - Enhanced version compatible with view-based system"""
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    current_month_name = now.strftime("%B %Y")
    current_month_start, current_month_end = get_month_dates(now.date())
    
    # Get all months with donations (for the UI dropdown)
    months_query = session.query(
        func.to_char(DonationLog.timestamp, 'YYYY-MM').label('month')
    ).filter(
        DonationLog.streamer_id == streamer_id
    ).group_by('month').order_by('month').all()
    
    months = [month[0] for month in months_query]
    
    # Get monthly summary (totals per month) - RESTORED FROM OLD VERSION
    monthly_summary = []
    verification = {}
    
    for month in months:
        month_start = datetime.strptime(f"{month}-01", "%Y-%m-%d")
        # Calculate the last day of the month
        if month_start.month == 12:
            next_month = datetime(month_start.year + 1, 1, 1)
        else:
            next_month = datetime(month_start.year, month_start.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        month_end = datetime.combine(month_end.date(), datetime.max.time())
        
        # Get donation totals for this month
        month_totals = session.query(
            func.sum(DonationLog.amount).label('total_amount'),
            func.count(DonationLog.id).label('donation_count')
        ).filter(
            DonationLog.streamer_id == streamer_id,
            DonationLog.timestamp >= month_start,
            DonationLog.timestamp <= month_end
        ).first()
        
        month_name = month_start.strftime("%B %Y")
        
        monthly_summary.append({
            "month": month,
            "month_name": month_name,
            "total_amount": float(month_totals.total_amount or 0),
            "donation_count": int(month_totals.donation_count or 0)
        })
        
        verification[month] = float(month_totals.total_amount or 0)
    
    # Get the full ranked list of supporters for the current month from the view
    current_month_supporters = session.query(MonthlyTopSupporter).filter(
        MonthlyTopSupporter.streamer_id == streamer_id
    ).order_by(MonthlyTopSupporter.rank).all()
    
    # Build donor details with full historical data - ENHANCED VERSION
    donor_details = []
    
    # Get all unique donors who donated in the current month
    current_month_donor_names = [supporter.donor_name for supporter in current_month_supporters]
    
    for supporter in current_month_supporters:
        donor_name = supporter.donor_name
        current_month_amount = float(supporter.total_amount)
        
        # Get historical data for this donor across all months - RESTORED FUNCTIONALITY
        donor_history = {}
        donor_total = 0
        donor_count = 0
        
        for month in months:
            month_start = datetime.strptime(f"{month}-01", "%Y-%m-%d")
            if month_start.month == 12:
                next_month = datetime(month_start.year + 1, 1, 1)
            else:
                next_month = datetime(month_start.year, month_start.month + 1, 1)
            month_end = next_month - timedelta(days=1)
            month_end = datetime.combine(month_end.date(), datetime.max.time())
            
            donor_month_data = session.query(
                func.sum(DonationLog.amount).label('total_amount'),
                func.count(DonationLog.id).label('donation_count')
            ).filter(
                DonationLog.streamer_id == streamer_id,
                DonationLog.donor_name == donor_name,
                DonationLog.timestamp >= month_start,
                DonationLog.timestamp <= month_end
            ).first()
            
            month_amount = float(donor_month_data.total_amount or 0)
            month_count = int(donor_month_data.donation_count or 0)
            
            if month_amount > 0:
                donor_history[month] = {
                    "amount": month_amount,
                    "count": month_count
                }
                
                donor_total += month_amount
                donor_count += month_count
        
        # Get current month count from the breakdown
        current_month_count = donor_history.get(current_month, {}).get("count", 0)
        
        donor_details.append({
            "rank": supporter.rank,  # NEW: Include rank from view
            "donor_name": donor_name,
            "total_amount": donor_total,  # Total across all time
            "donation_count": current_month_count,  # Current month count
            "current_month_amount": current_month_amount,  # From view
            "monthly_breakdown": donor_history  # RESTORED: Full historical breakdown
        })

    return {
        "months": months,  # RESTORED
        "current_month": current_month,
        "current_month_name": current_month_name,
        "monthly_summary": monthly_summary,  # RESTORED
        "donor_details": donor_details,  # ENHANCED with rank
        "verification": verification  # RESTORED
    }

# --- Database initialization ---
def init_db():
    """
    Initializes the database.
    - Creates tables for all models.
    - Creates the new database views that replace those tables.
    - Creates the donation processing trigger.
    """
    # Create all tables defined in Base.metadata
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        # Transactionally drop old tables and create new views.
        with conn.begin():
            print("Dropping old supporter tables/views if they exist...")
            # Drop views first, then tables (using IF EXISTS to avoid errors)
            conn.execute(text("DROP VIEW IF EXISTS weekly_top_supporters CASCADE;"))
            conn.execute(text("DROP VIEW IF EXISTS monthly_top_supporters CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS weekly_top_supporters CASCADE;"))
            conn.execute(text("DROP TABLE IF EXISTS monthly_top_supporters CASCADE;"))
            
            print("Creating database views for ranked supporters...")
            
            # Weekly View (Saturday to Friday)
            conn.execute(text("""
            CREATE OR REPLACE VIEW weekly_top_supporters AS
            WITH weekly_donations AS (
                SELECT
                    streamer_id,
                    donor_phone,
                    SUM(amount) AS total_amount
                FROM donation_logs
                WHERE
                    timestamp >= (date_trunc('week', current_date + INTERVAL '1 day') - INTERVAL '2 day')::date AND
                    timestamp < (date_trunc('week', current_date + INTERVAL '1 day') + INTERVAL '5 day')::date
                GROUP BY
                    streamer_id, donor_phone
            )
            SELECT
                ROW_NUMBER() OVER () AS id,
                wd.streamer_id,
                (date_trunc('week', current_date + INTERVAL '1 day') - INTERVAL '2 day')::date AS week_start_date,
                (date_trunc('week', current_date + INTERVAL '1 day') + INTERVAL '4 day')::date AS week_end_date,
                wd.donor_phone,
                COALESCE(d.display_name, 'Anonymous') AS donor_name,
                wd.total_amount,
                RANK() OVER (PARTITION BY wd.streamer_id ORDER BY wd.total_amount DESC) AS rank
            FROM weekly_donations wd
            LEFT JOIN donors d ON wd.donor_phone = d.phone_number;
            """))

            # Monthly View
            conn.execute(text("""
            CREATE OR REPLACE VIEW monthly_top_supporters AS
            WITH monthly_donations AS (
                SELECT
                    streamer_id,
                    donor_phone,
                    SUM(amount) AS total_amount
                FROM donation_logs
                WHERE
                    timestamp >= date_trunc('month', current_date)::date AND
                    timestamp < (date_trunc('month', current_date) + INTERVAL '1 month')::date
                GROUP BY
                    streamer_id, donor_phone
            )
            SELECT
                ROW_NUMBER() OVER () AS id,
                md.streamer_id,
                date_trunc('month', current_date)::date AS month_start_date,
                (date_trunc('month', current_date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS month_end_date,
                md.donor_phone,
                COALESCE(d.display_name, 'Anonymous') AS donor_name,
                md.total_amount,
                RANK() OVER (PARTITION BY md.streamer_id ORDER BY md.total_amount DESC) AS rank
            FROM monthly_donations md
            LEFT JOIN donors d ON md.donor_phone = d.phone_number;
            """))

            # Daily View
            conn.execute(text("""
            CREATE OR REPLACE VIEW todays_supporters AS
            WITH todays_donations AS (
                SELECT
                    streamer_id,
                    donor_phone,
                    SUM(amount) AS total_amount
                FROM donation_logs
                WHERE
                    DATE(timestamp) = CURRENT_DATE
                GROUP BY
                    streamer_id, donor_phone
            )
            SELECT
                ROW_NUMBER() OVER () AS id,
                td.streamer_id,
                td.donor_phone,
                COALESCE(d.display_name, 'Anonymous') AS donor_name,
                td.total_amount,
                RANK() OVER (PARTITION BY td.streamer_id ORDER BY td.total_amount DESC) AS rank
            FROM todays_donations td
            LEFT JOIN donors d ON td.donor_phone = d.phone_number
            ORDER BY td.streamer_id, rank;
            """))
            print("Views created successfully.")
            
            # Create the trigger
            # (Your existing trigger creation logic can be placed here)
            print("Creating donation trigger...")
            conn.execute(text("""
            CREATE OR REPLACE FUNCTION update_daily_donation_total()
            RETURNS TRIGGER AS $$
            BEGIN
                UPDATE daily_donation_totals 
                SET total_amount = total_amount + NEW.amount, donation_count = donation_count + 1
                WHERE streamer_id = NEW.streamer_id AND donation_date = DATE(NEW.timestamp);
                IF NOT FOUND THEN
                    INSERT INTO daily_donation_totals (streamer_id, donation_date, total_amount, donation_count)
                    VALUES (NEW.streamer_id, DATE(NEW.timestamp), NEW.amount, 1);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """))
            conn.execute(text("DROP TRIGGER IF EXISTS update_donation_total_trigger ON donation_logs;"))
            conn.execute(text("""
            CREATE TRIGGER update_donation_total_trigger
            AFTER INSERT ON donation_logs
            FOR EACH ROW EXECUTE FUNCTION update_daily_donation_total();
            """))
            print("Trigger created successfully.")

    print("Database initialization complete.")

if __name__ == "__main__":
    init_db()