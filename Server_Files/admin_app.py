# admin_app.py - Admin Web Interface
import os
import requests
import json
import logging
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired
from werkzeug.security import generate_password_hash, check_password_hash

# Import shared models from models.py
from models import (
    engine, Base, Session, User, Log, Donor, AdminUser,
    init_database_logger, get_database_logger, init_db
)

# --- Setup Logging ---
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('admin_server')

# Add database handler to logger
db_handler = init_database_logger()
logger.addHandler(db_handler)

# --- Flask App Setup ---
app = Flask(__name__)
app.jinja_env.globals.update(max=max, min=min)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_secret_key_change_in_production')
app.config['SYNC_SERVICE_URL'] = os.environ.get('SYNC_SERVICE_URL', 'http://localhost:5001')
app.config['MAIN_SERVICE_URL'] = os.environ.get('MAIN_SERVICE_URL', 'http://localhost:5000')
app.config['WEBSOCKET_SERVER_URL'] = os.environ.get('WEBSOCKET_SERVER_URL', 'ws://localhost:8765')

# --- Forms ---
class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')
    
class UserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password')
    token = StringField('Token')
    submit = SubmitField('Save')

class DonorForm(FlaskForm):
    phone_number = StringField('Phone Number', validators=[DataRequired()])
    display_name = StringField('Display Name', validators=[DataRequired()])
    submit = SubmitField('Save')

# --- Initialize Admin User ---
def init_admin_user():
    session = Session()
    try:
        # Check if admin user exists
        if session.query(AdminUser).count() == 0:
            # Create default admin user
            admin = AdminUser(
                username="admin",
                password_hash=generate_password_hash("admin123")  # Change this in production!
            )
            session.add(admin)
            session.commit()
            logger.info("Default admin user created")
    except Exception as e:
        logger.error(f"Error initializing admin user: {e}")
        session.rollback()
    finally:
        session.close()

# --- Helper Functions ---
def get_connected_clients():
    """
    Query the main service for connected WebSocket clients
    """
    try:
        # In a real implementation, you'd make an API call to the main service
        # For now, we'll make a direct query to the database to simulate this
        session = Session()
        try:
            # Get all users
            users = session.query(User).all()
            
            # For each user, check if they've logged in recently
            connected_clients = {}
            for user in users:
                # In a real implementation, you would check against the main_app's connected_clients
                # Since we don't have direct access, we'll simulate based on last_login
                if user.last_login and (datetime.now() - user.last_login).total_seconds() < 3600:  # Within the last hour
                    connected_clients[user.username] = {
                        "token": user.token,
                        "connections": 1  # Simulate one connection
                    }
                else:
                    connected_clients[user.username] = {
                        "token": user.token,
                        "connections": 0
                    }
            
            return connected_clients
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error getting connected clients: {e}")
        return {}

def trigger_sheets_sync():
    """Trigger a manual sync on the sync service"""
    try:
        response = requests.post(f"{app.config['SYNC_SERVICE_URL']}/sync")
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to sync service")
        return {"status": "error", "message": "Could not connect to sync service"}
    except Exception as e:
        logger.error(f"Error triggering sync: {e}")
        return {"status": "error", "message": str(e)}

def send_test_donation(token, amount="100.00", donor_name="Test Donor"):
    """Send a test donation notification to a user"""
    try:
        # Send a request to the main service's donation endpoint
        payload = {
            "message": f"You have received Tk {amount} from 1712345678"
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        response = requests.post(f"{app.config['MAIN_SERVICE_URL']}/donation", 
                                json=payload, 
                                headers=headers)
        return response.json()
    except requests.exceptions.ConnectionError:
        logger.error("Could not connect to main service")
        return {"status": "error", "message": "Could not connect to main service"}
    except Exception as e:
        logger.error(f"Error sending test donation: {e}")
        return {"status": "error", "message": str(e)}

# --- Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'admin_user' in session:
        return redirect(url_for('dashboard'))
        
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        
        session_db = Session()
        try:
            admin = session_db.query(AdminUser).filter_by(username=username).first()
            if admin and check_password_hash(admin.password_hash, password):
                session['admin_user'] = username
                flash('Logged in successfully!', 'success')
                return redirect(url_for('dashboard'))
            else:
                flash('Invalid username or password.', 'danger')
        finally:
            session_db.close()
            
    return render_template('login.html', form=form)

@app.route('/logout')
def logout():
    session.pop('admin_user', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/')
def dashboard():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        user_count = session_db.query(User).count()
        donor_count = session_db.query(Donor).count()
        log_count = session_db.query(Log).count()
        recent_logs = session_db.query(Log).order_by(Log.timestamp.desc()).limit(5).all()
        
        return render_template(
            'dashboard.html',
            user_count=user_count,
            donor_count=donor_count,
            log_count=log_count,
            recent_logs=recent_logs
        )
    finally:
        session_db.close()

@app.route('/users')
def users():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        users = session_db.query(User).all()
        connected_clients = get_connected_clients()
        
        return render_template('users.html', users=users, connected_clients=connected_clients)
    finally:
        session_db.close()

@app.route('/users/add', methods=['GET', 'POST'])
def add_user():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    form = UserForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        token = form.token.data or os.urandom(16).hex()
        
        session_db = Session()
        try:
            # Check if username already exists
            existing_user = session_db.query(User).filter_by(username=username).first()
            if existing_user:
                flash('Username already exists.', 'danger')
                return render_template('user_form.html', form=form, title='Add User')
            
            # Create user
            user = User(
                username=username,
                password_hash=generate_password_hash(password),
                token=token
            )
            session_db.add(user)
            session_db.commit()
            flash('User created successfully.', 'success')
            return redirect(url_for('users'))
        except Exception as e:
            session_db.rollback()
            flash(f'Error creating user: {str(e)}', 'danger')
        finally:
            session_db.close()
    
    return render_template('user_form.html', form=form, title='Add User')

@app.route('/users/edit/<int:user_id>', methods=['GET', 'POST'])
def edit_user(user_id):
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        user = session_db.query(User).get(user_id)
        if not user:
            flash('User not found.', 'danger')
            return redirect(url_for('users'))
        
        form = UserForm(obj=user)
        if form.validate_on_submit():
            user.username = form.username.data
            if form.password.data:
                user.password_hash = generate_password_hash(form.password.data)
            if form.token.data:
                user.token = form.token.data
            
            session_db.commit()
            flash('User updated successfully.', 'success')
            return redirect(url_for('users'))
            
        # For GET request, pre-fill form
        form.username.data = user.username
        form.token.data = user.token
        # Don't pre-fill password for security reasons
        
        return render_template('user_form.html', form=form, user=user, title='Edit User')
    except Exception as e:
        session_db.rollback()
        flash(f'Error updating user: {str(e)}', 'danger')
        return redirect(url_for('users'))
    finally:
        session_db.close()

@app.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        user = session_db.query(User).get(user_id)
        if not user:
            flash('User not found.', 'danger')
        else:
            session_db.delete(user)
            session_db.commit()
            flash('User deleted successfully.', 'success')
    except Exception as e:
        session_db.rollback()
        flash(f'Error deleting user: {str(e)}', 'danger')
    finally:
        session_db.close()
        
    return redirect(url_for('users'))

@app.route('/users/test_donation/<int:user_id>', methods=['POST'])
def test_donation(user_id):
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        user = session_db.query(User).get(user_id)
        if not user:
            return jsonify({"status": "error", "message": "User not found"}), 404
        
        amount = request.form.get('amount', '100.00')
        result = send_test_donation(user.token, amount)
        return jsonify(result)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    finally:
        session_db.close()

@app.route('/logs')
def logs():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 50
    level = request.args.get('level', '')
    search = request.args.get('search', '')
    
    session_db = Session()
    try:
        query = session_db.query(Log).order_by(Log.timestamp.desc())
        
        # Apply filters
        if level:
            query = query.filter(Log.level == level)
        if search:
            query = query.filter(Log.message.ilike(f'%{search}%'))
        
        # Paginate
        total = query.count()
        logs = query.limit(per_page).offset((page - 1) * per_page).all()
        
        # Calculate pagination values
        total_pages = (total + per_page - 1) // per_page
        has_prev = page > 1
        has_next = page < total_pages
        
        return render_template(
            'logs.html',
            logs=logs,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            has_prev=has_prev,
            has_next=has_next,
            level=level,
            search=search
        )
    finally:
        session_db.close()

@app.route('/donors')
def donors():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    page = request.args.get('page', 1, type=int)
    per_page = 50
    search = request.args.get('search', '')
    
    session_db = Session()
    try:
        query = session_db.query(Donor)
        
        # Apply filters
        if search:
            query = query.filter(
                (Donor.phone_number.ilike(f'%{search}%')) | 
                (Donor.display_name.ilike(f'%{search}%'))
            )
        
        # Paginate
        total = query.count()
        donors = query.limit(per_page).offset((page - 1) * per_page).all()
        
        # Calculate pagination values
        total_pages = (total + per_page - 1) // per_page
        has_prev = page > 1
        has_next = page < total_pages
        
        return render_template(
            'donors.html',
            donors=donors,
            page=page,
            per_page=per_page,
            total=total,
            total_pages=total_pages,
            has_prev=has_prev,
            has_next=has_next,
            search=search
        )
    finally:
        session_db.close()

@app.route('/donors/add', methods=['GET', 'POST'])
def add_donor():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    form = DonorForm()
    if form.validate_on_submit():
        phone_number = form.phone_number.data.strip().lstrip('0')
        display_name = form.display_name.data
        
        session_db = Session()
        try:
            # Check if phone already exists
            existing_donor = session_db.query(Donor).filter_by(phone_number=phone_number).first()
            if existing_donor:
                flash('Phone number already exists.', 'danger')
                return render_template('donor_form.html', form=form, title='Add Donor')
            
            # Create donor
            donor = Donor(
                phone_number=phone_number,
                display_name=display_name
            )
            session_db.add(donor)
            session_db.commit()
            flash('Donor created successfully.', 'success')
            return redirect(url_for('donors'))
        except Exception as e:
            session_db.rollback()
            flash(f'Error creating donor: {str(e)}', 'danger')
        finally:
            session_db.close()
    
    return render_template('donor_form.html', form=form, title='Add Donor')

@app.route('/donors/edit/<int:donor_id>', methods=['GET', 'POST'])
def edit_donor(donor_id):
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        donor = session_db.query(Donor).get(donor_id)
        if not donor:
            flash('Donor not found.', 'danger')
            return redirect(url_for('donors'))
        
        form = DonorForm(obj=donor)
        if form.validate_on_submit():
            donor.phone_number = form.phone_number.data.strip().lstrip('0')
            donor.display_name = form.display_name.data
            
            session_db.commit()
            flash('Donor updated successfully.', 'success')
            return redirect(url_for('donors'))
            
        # For GET request, pre-fill form
        form.phone_number.data = donor.phone_number
        form.display_name.data = donor.display_name
        
        return render_template('donor_form.html', form=form, donor=donor, title='Edit Donor')
    except Exception as e:
        session_db.rollback()
        flash(f'Error updating donor: {str(e)}', 'danger')
        return redirect(url_for('donors'))
    finally:
        session_db.close()

@app.route('/donors/delete/<int:donor_id>', methods=['POST'])
def delete_donor(donor_id):
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    session_db = Session()
    try:
        donor = session_db.query(Donor).get(donor_id)
        if not donor:
            flash('Donor not found.', 'danger')
        else:
            session_db.delete(donor)
            session_db.commit()
            flash('Donor deleted successfully.', 'success')
    except Exception as e:
        session_db.rollback()
        flash(f'Error deleting donor: {str(e)}', 'danger')
    finally:
        session_db.close()
        
    return redirect(url_for('donors'))

@app.route('/sync', methods=['POST'])
def sync():
    if 'admin_user' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    result = trigger_sheets_sync()
    return jsonify(result)

@app.route('/system/services', methods=['GET'])
def system_services():
    if 'admin_user' not in session:
        return redirect(url_for('login'))
    
    # Check status of all services
    services = {
        "admin_service": {
            "name": "Admin Service",
            "status": "running",
            "url": f"http://localhost:{app.config.get('PORT', 5002)}"
        },
        "main_service": {
            "name": "Main Service",
            "status": "unknown",
            "url": app.config['MAIN_SERVICE_URL']
        },
        "sync_service": {
            "name": "Sync Service",
            "status": "unknown",
            "url": app.config['SYNC_SERVICE_URL']
        },
        "websocket_service": {
            "name": "WebSocket Service",
            "status": "unknown",
            "url": app.config['WEBSOCKET_SERVER_URL']
        }
    }
    
    # Check main service
    try:
        response = requests.get(f"{app.config['MAIN_SERVICE_URL']}/", timeout=2)
        if response.status_code == 200:
            services["main_service"]["status"] = "running"
    except:
        services["main_service"]["status"] = "not running"
    
    # Check sync service
    try:
        response = requests.get(f"{app.config['SYNC_SERVICE_URL']}/", timeout=2)
        if response.status_code == 200:
            services["sync_service"]["status"] = "running"
            services["sync_service"]["data"] = response.json()
    except:
        services["sync_service"]["status"] = "not running"
    
    # We can't directly check WebSocket service, so we'll just assume it's running
    # if the main service is running
    services["websocket_service"]["status"] = "likely running" if services["main_service"]["status"] == "running" else "unknown"
    
    return render_template('services.html', services=services)

# --- API Routes ---
@app.route('/api/stats')
def api_stats():
    if 'admin_user' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    session_db = Session()
    try:
        stats = {
            "users": session_db.query(User).count(),
            "donors": session_db.query(Donor).count(),
            "logs": session_db.query(Log).count(),
            "log_levels": {
                "INFO": session_db.query(Log).filter_by(level="INFO").count(),
                "WARNING": session_db.query(Log).filter_by(level="WARNING").count(),
                "ERROR": session_db.query(Log).filter_by(level="ERROR").count()
            }
        }
        return jsonify(stats)
    finally:
        session_db.close()

# --- Additional Templates ---
def create_additional_templates():
    """Create additional template files needed for the admin app"""
    
    # Create the donor form template
    with open('templates/donor_form.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">{{ title }}</h1>
</div>

<div class="row">
    <div class="col-md-6">
        <form method="POST">
            {{ form.hidden_tag() }}
            <div class="mb-3">
                {{ form.phone_number.label(class="form-label") }}
                {{ form.phone_number(class="form-control") }}
                <small class="form-text text-muted">Phone number should be 10-11 digits without country code</small>
            </div>
            <div class="mb-3">
                {{ form.display_name.label(class="form-label") }}
                {{ form.display_name(class="form-control") }}
            </div>
            <div class="mb-3">
                {{ form.submit(class="btn btn-primary") }}
                <a href="{{ url_for('donors') }}" class="btn btn-outline-secondary">Cancel</a>
            </div>
        </form>
    </div>
</div>
{% endblock %}""")

    # Create the donors template
    with open('templates/donors.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">Donors</h1>
    <div class="btn-toolbar mb-2 mb-md-0">
        <a href="{{ url_for('add_donor') }}" class="btn btn-sm btn-primary">
            Add Donor
        </a>
    </div>
</div>

<div class="row mb-3">
    <div class="col-md-12">
        <form method="get" class="row g-3">
            <div class="col-md-10">
                <input type="text" class="form-control" name="search" placeholder="Search phone or name..." value="{{ search }}">
            </div>
            <div class="col-md-2">
                <button type="submit" class="btn btn-primary w-100">Search</button>
            </div>
        </form>
    </div>
</div>

<div class="table-responsive">
    <table class="table table-striped table-hover">
        <thead>
            <tr>
                <th>ID</th>
                <th>Phone Number</th>
                <th>Display Name</th>
                <th>Created</th>
                <th>Updated</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for donor in donors %}
            <tr>
                <td>{{ donor.id }}</td>
                <td>{{ donor.phone_number }}</td>
                <td>{{ donor.display_name }}</td>
                <td>{{ donor.created_at.strftime('%Y-%m-%d') }}</td>
                <td>{{ donor.updated_at.strftime('%Y-%m-%d') }}</td>
                <td>
                    <div class="btn-group btn-group-sm" role="group">
                        <a href="{{ url_for('edit_donor', donor_id=donor.id) }}" class="btn btn-outline-primary">Edit</a>
                        <button type="button" class="btn btn-outline-danger" 
                            onclick="if (confirm('Are you sure you want to delete this donor?')) { 
                                document.getElementById('delete-form-{{ donor.id }}').submit(); 
                            }">Delete</button>
                        <form id="delete-form-{{ donor.id }}" action="{{ url_for('delete_donor', donor_id=donor.id) }}" method="post" style="display: none;"></form>
                    </div>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>

<div class="d-flex justify-content-between align-items-center mt-3">
    <div>
        Showing {{ donors|length }} of {{ total }} donors
    </div>
    <div>
        <nav aria-label="Page navigation">
            <ul class="pagination">
                <li class="page-item {% if not has_prev %}disabled{% endif %}">
                    <a class="page-link" href="{{ url_for('donors', page=page-1, search=search) }}">Previous</a>
                </li>
                {% for p in range(max(1, page-2), min(total_pages+1, page+3)) %}
                <li class="page-item {% if p == page %}active{% endif %}">
                    <a class="page-link" href="{{ url_for('donors', page=p, search=search) }}">{{ p }}</a>
                </li>
                {% endfor %}
                <li class="page-item {% if not has_next %}disabled{% endif %}">
                    <a class="page-link" href="{{ url_for('donors', page=page+1, search=search) }}">Next</a>
                </li>
            </ul>
        </nav>
    </div>
</div>
{% endblock %}""")

    # Create the services template
    with open('templates/services.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">System Services</h1>
    <div class="btn-toolbar mb-2 mb-md-0">
        <button type="button" id="refreshButton" class="btn btn-sm btn-outline-secondary">
            Refresh Status
        </button>
    </div>
</div>

<div class="row mb-4">
    {% for service_id, service in services.items() %}
    <div class="col-md-6 mb-3">
        <div class="card">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0">{{ service.name }}</h5>
                <span class="badge {% if service.status == 'running' %}bg-success{% elif service.status == 'not running' %}bg-danger{% else %}bg-warning{% endif %}">
                    {{ service.status }}
                </span>
            </div>
            <div class="card-body">
                <p class="card-text">URL: {{ service.url }}</p>
                {% if service.data %}
                <pre class="card-text">{{ service.data|tojson(indent=2) }}</pre>
                {% endif %}
            </div>
            <div class="card-footer">
                <a href="{{ service.url }}" target="_blank" class="btn btn-sm btn-outline-primary">
                    Visit Service
                </a>
            </div>
        </div>
    </div>
    {% endfor %}
</div>

<div class="row mt-4">
    <div class="col-md-12">
        <div class="card">
            <div class="card-header">
                <h5 class="mb-0">Manual Sync</h5>
            </div>
            <div class="card-body">
                <p>Trigger a manual sync of donor data from Google Sheets</p>
                <button id="syncButton" class="btn btn-primary">Trigger Sync</button>
                <div id="syncResult" class="mt-3"></div>
            </div>
        </div>
    </div>
</div>

<script>
document.getElementById('syncButton').addEventListener('click', function() {
    fetch('/sync', { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            const resultDiv = document.getElementById('syncResult');
            if (data.status === 'success') {
                resultDiv.innerHTML = `<div class="alert alert-success">Sync completed successfully!</div>`;
            } else {
                resultDiv.innerHTML = `<div class="alert alert-danger">Sync failed: ${data.message}</div>`;
            }
        })
        .catch(error => {
            document.getElementById('syncResult').innerHTML = 
                `<div class="alert alert-danger">Error: ${error.message}</div>`;
        });
});

document.getElementById('refreshButton').addEventListener('click', function() {
    window.location.reload();
});
</script>
{% endblock %}""")

    # Also create the remaining required templates like logs.html
    with open('templates/logs.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">System Logs</h1>
</div>

<div class="row mb-3">
    <div class="col-md-12">
        <form method="get" class="row g-3">
            <div class="col-md-3">
                <select name="level" class="form-select">
                    <option value="" {% if level == '' %}selected{% endif %}>All Levels</option>
                    <option value="INFO" {% if level == 'INFO' %}selected{% endif %}>INFO</option>
                    <option value="WARNING" {% if level == 'WARNING' %}selected{% endif %}>WARNING</option>
                    <option value="ERROR" {% if level == 'ERROR' %}selected{% endif %}>ERROR</option>
                </select>
            </div>
            <div class="col-md-7">
                <input type="text" class="form-control" name="search" placeholder="Search logs..." value="{{ search }}">
            </div>
            <div class="col-md-2">
                <button type="submit" class="btn btn-primary w-100">Filter</button>
            </div>
        </form>
    </div>
</div>

<div class="table-responsive">
    <table class="table table-striped table-hover">
        <thead>
            <tr>
                <th>ID</th>
                <th>Timestamp</th>
                <th>Level</th>
                <th>Source</th>
                <th>Message</th>
            </tr>
        </thead>
        <tbody>
            {% for log in logs %}
            <tr class="{% if log.level == 'ERROR' %}table-danger{% elif log.level == 'WARNING' %}table-warning{% endif %}">
                <td>{{ log.id }}</td>
                <td>{{ log.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                <td>
                    <span class="badge {% if log.level == 'ERROR' %}bg-danger{% elif log.level == 'WARNING' %}bg-warning text-dark{% else %}bg-info text-dark{% endif %}">
                        {{ log.level }}
                    </span>
                </td>
                <td>{{ log.source }}</td>
                <td>{{ log.message }}</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>

<div class="d-flex justify-content-between align-items-center mt-3">
    <div>
        Showing {{ logs|length }} of {{ total }} logs
    </div>
    <div>
        <nav aria-label="Page navigation">
            <ul class="pagination">
                <li class="page-item {% if not has_prev %}disabled{% endif %}">
                    <a class="page-link" href="{{ url_for('logs', page=page-1, level=level, search=search) }}">Previous</a>
                </li>
                {% for p in range(max(1, page-2), min(total_pages+1, page+3)) %}
                <li class="page-item {% if p == page %}active{% endif %}">
                    <a class="page-link" href="{{ url_for('logs', page=p, level=level, search=search) }}">{{ p }}</a>
                </li>
                {% endfor %}
                <li class="page-item {% if not has_next %}disabled{% endif %}">
                    <a class="page-link" href="{{ url_for('logs', page=page+1, level=level, search=search) }}">Next</a>
                </li>
            </ul>
        </nav>
    </div>
</div>
{% endblock %}""")

    # Create user templates
    with open('templates/users.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">Users</h1>
    <div class="btn-toolbar mb-2 mb-md-0">
        <a href="{{ url_for('add_user') }}" class="btn btn-sm btn-primary">
            Add User
        </a>
    </div>
</div>

<div class="table-responsive">
    <table class="table table-striped table-hover">
        <thead>
            <tr>
                <th>ID</th>
                <th>Username</th>
                <th>Token</th>
                <th>Last Login</th>
                <th>Status</th>
                <th>Actions</th>
            </tr>
        </thead>
        <tbody>
            {% for user in users %}
            <tr>
                <td>{{ user.id }}</td>
                <td>{{ user.username }}</td>
                <td><code>{{ user.token }}</code></td>
                <td>{{ user.last_login.strftime('%Y-%m-%d %H:%M:%S') if user.last_login else 'Never' }}</td>
                <td>
                    {% if user.username in connected_clients and connected_clients[user.username].connections > 0 %}
                    <span class="badge bg-success">Online ({{ connected_clients[user.username].connections }})</span>
                    {% else %}
                    <span class="badge bg-secondary">Offline</span>
                    {% endif %}
                </td>
                <td>
                    <div class="btn-group btn-group-sm" role="group">
                        <a href="{{ url_for('edit_user', user_id=user.id) }}" class="btn btn-outline-primary">Edit</a>
                        <button type="button" class="btn btn-outline-warning test-donation-btn" 
                            data-user-id="{{ user.id }}" data-bs-toggle="modal" data-bs-target="#testDonationModal">
                            Test Donation
                        </button>
                        <button type="button" class="btn btn-outline-danger" 
                            onclick="if (confirm('Are you sure you want to delete this user?')) { 
                                document.getElementById('delete-form-{{ user.id }}').submit(); 
                            }">Delete</button>
                        <form id="delete-form-{{ user.id }}" action="{{ url_for('delete_user', user_id=user.id) }}" method="post" style="display: none;"></form>
                    </div>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>

<!-- Test Donation Modal -->
<div class="modal fade" id="testDonationModal" tabindex="-1" aria-labelledby="testDonationModalLabel" aria-hidden="true">
  <div class="modal-dialog">
    <div class="modal-content">
      <div class="modal-header">
        <h5 class="modal-title" id="testDonationModalLabel">Send Test Donation</h5>
        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body">
        <form id="testDonationForm">
          <input type="hidden" id="testDonationUserId" name="user_id" value="">
          <div class="mb-3">
            <label for="testAmount" class="form-label">Amount</label>
            <div class="input-group">
              <span class="input-group-text">৳</span>
              <input type="text" class="form-control" id="testAmount" name="amount" value="100.00">
            </div>
          </div>
        </form>
        <div id="donationResult"></div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
        <button type="button" class="btn btn-primary" id="sendTestDonation">Send</button>
      </div>
    </div>
  </div>
</div>

<script>
// Set user ID when opening the modal
document.querySelectorAll('.test-donation-btn').forEach(button => {
    button.addEventListener('click', function() {
        document.getElementById('testDonationUserId').value = this.getAttribute('data-user-id');
        document.getElementById('donationResult').innerHTML = '';
    });
});

// Send test donation
document.getElementById('sendTestDonation').addEventListener('click', function() {
    const userId = document.getElementById('testDonationUserId').value;
    const amount = document.getElementById('testAmount').value;
    const resultDiv = document.getElementById('donationResult');
    
    resultDiv.innerHTML = '<div class="alert alert-info">Sending test donation...</div>';
    
    const formData = new FormData();
    formData.append('amount', amount);
    
    fetch(`/users/test_donation/${userId}`, {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.status === 'success') {
            resultDiv.innerHTML = `<div class="alert alert-success">Test donation sent successfully!</div>`;
        } else {
            resultDiv.innerHTML = `<div class="alert alert-danger">Error: ${data.message}</div>`;
        }
    })
    .catch(error => {
        resultDiv.innerHTML = `<div class="alert alert-danger">Error: ${error.message}</div>`;
    });
});
</script>
{% endblock %}""")

    with open('templates/user_form.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">{{ title }}</h1>
</div>

<div class="row">
    <div class="col-md-6">
        <form method="POST">
            {{ form.hidden_tag() }}
            <div class="mb-3">
                {{ form.username.label(class="form-label") }}
                {{ form.username(class="form-control") }}
            </div>
            <div class="mb-3">
                {{ form.password.label(class="form-label") }}
                {{ form.password(class="form-control") }}
                {% if user %}
                <small class="form-text text-muted">Leave blank to keep current password</small>
                {% endif %}
            </div>
            <div class="mb-3">
                {{ form.token.label(class="form-label") }}
                {{ form.token(class="form-control") }}
                <small class="form-text text-muted">Leave blank to generate automatically</small>
            </div>
            <div class="mb-3">
                {{ form.submit(class="btn btn-primary") }}
                <a href="{{ url_for('users') }}" class="btn btn-outline-secondary">Cancel</a>
            </div>
        </form>
    </div>
</div>
{% endblock %}""")

    # Create dashboard template
    with open('templates/dashboard.html', 'w') as f:
        f.write("""{% extends "base.html" %}

{% block content %}
<div class="d-flex justify-content-between flex-wrap flex-md-nowrap align-items-center pt-3 pb-2 mb-3 border-bottom">
    <h1 class="h2">Dashboard</h1>
    <div class="btn-toolbar mb-2 mb-md-0">
        <button type="button" id="refreshButton" class="btn btn-sm btn-outline-secondary">
            <span data-feather="refresh-cw"></span>
            Refresh
        </button>
    </div>
</div>

<div class="row">
    <div class="col-md-4 mb-4">
        <div class="card h-100">
            <div class="card-body">
                <h5 class="card-title">Users</h5>
                <p class="card-text display-4">{{ user_count }}</p>
                <a href="{{ url_for('users') }}" class="btn btn-primary">Manage Users</a>
            </div>
        </div>
    </div>
    <div class="col-md-4 mb-4">
        <div class="card h-100">
            <div class="card-body">
                <h5 class="card-title">Donors</h5>
                <p class="card-text display-4">{{ donor_count }}</p>
                <a href="{{ url_for('donors') }}" class="btn btn-primary">Manage Donors</a>
            </div>
        </div>
    </div>
    <div class="col-md-4 mb-4">
        <div class="card h-100">
            <div class="card-body">
                <h5 class="card-title">System Logs</h5>
                <p class="card-text display-4">{{ log_count }}</p>
                <a href="{{ url_for('logs') }}" class="btn btn-primary">View Logs</a>
            </div>
        </div>
    </div>
</div>

<div class="row">
    <div class="col-md-12">
        <div class="card">
            <div class="card-header">
                <h5 class="mb-0">Recent Logs</h5>
            </div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-striped table-hover">
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Level</th>
                                <th>Source</th>
                                <th>Message</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for log in recent_logs %}
                            <tr class="{% if log.level == 'ERROR' %}table-danger{% elif log.level == 'WARNING' %}table-warning{% endif %}">
                                <td>{{ log.timestamp.strftime('%Y-%m-%d %H:%M:%S') }}</td>
                                <td>
                                    <span class="badge {% if log.level == 'ERROR' %}bg-danger{% elif log.level == 'WARNING' %}bg-warning text-dark{% else %}bg-info text-dark{% endif %}">
                                        {{ log.level }}
                                    </span>
                                </td>
                                <td>{{ log.source }}</td>
                                <td>{{ log.message }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="card-footer">
                <a href="{{ url_for('logs') }}" class="btn btn-sm btn-outline-primary">View All Logs</a>
            </div>
        </div>
    </div>
</div>

<script>
document.getElementById('refreshButton').addEventListener('click', function() {
    window.location.reload();
});
</script>
{% endblock %}""")

    # Create login template
    with open('templates/login.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            display: flex;
            align-items: center;
            padding-top: 40px;
            padding-bottom: 40px;
            background-color: #f5f5f5;
            height: 100vh;
        }
        .form-signin {
            width: 100%;
            max-width: 330px;
            padding: 15px;
            margin: auto;
        }
        .form-signin .form-floating:focus-within {
            z-index: 2;
        }
        .form-signin input[type="text"] {
            margin-bottom: -1px;
            border-bottom-right-radius: 0;
            border-bottom-left-radius: 0;
        }
        .form-signin input[type="password"] {
            margin-bottom: 10px;
            border-top-left-radius: 0;
            border-top-right-radius: 0;
        }
    </style>
</head>
<body class="text-center">
    <main class="form-signin">
        <form method="POST">
            {{ form.hidden_tag() }}
            <h1 class="h3 mb-3 fw-normal">Admin Login</h1>
            
            {% with messages = get_flashed_messages(with_categories=true) %}
                {% if messages %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ category }}">{{ message }}</div>
                    {% endfor %}
                {% endif %}
            {% endwith %}
            
            <div class="form-floating">
                {{ form.username(class="form-control", id="floatingInput", placeholder="Username") }}
                <label for="floatingInput">Username</label>
            </div>
            <div class="form-floating">
                {{ form.password(class="form-control", id="floatingPassword", placeholder="Password") }}
                <label for="floatingPassword">Password</label>
            </div>
            
            {{ form.submit(class="w-100 btn btn-lg btn-primary") }}
            <p class="mt-5 mb-3 text-muted">&copy; 2025</p>
        </form>
    </main>
</body>
</html>""")

    # Create base template
    with open('templates/base.html', 'w') as f:
        f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Panel</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.8.1/font/bootstrap-icons.css">
    <style>
        body {
            font-size: .875rem;
        }
        
        .feather {
            width: 16px;
            height: 16px;
            vertical-align: text-bottom;
        }
        
        /*
         * Sidebar
         */
        
        .sidebar {
            position: fixed;
            top: 0;
            bottom: 0;
            left: 0;
            z-index: 100; /* Behind the navbar */
            padding: 48px 0 0; /* Height of navbar */
            box-shadow: inset -1px 0 0 rgba(0, 0, 0, .1);
        }
        
        @media (max-width: 767.98px) {
            .sidebar {
                top: 5rem;
            }
        }
        
        .sidebar-sticky {
            position: relative;
            top: 0;
            height: calc(100vh - 48px);
            padding-top: .5rem;
            overflow-x: hidden;
            overflow-y: auto; /* Scrollable contents if viewport is shorter than content. */
        }
        
        .sidebar .nav-link {
            font-weight: 500;
            color: #333;
        }
        
        .sidebar .nav-link .feather {
            margin-right: 4px;
            color: #727272;
        }
        
        .sidebar .nav-link.active {
            color: #2470dc;
        }
        
        .sidebar .nav-link:hover .feather,
        .sidebar .nav-link.active .feather {
            color: inherit;
        }
        
        .sidebar-heading {
            font-size: .75rem;
            text-transform: uppercase;
        }
        
        /*
         * Navbar
         */
        
        .navbar-brand {
            padding-top: .75rem;
            padding-bottom: .75rem;
            font-size: 1rem;
            background-color: rgba(0, 0, 0, .25);
            box-shadow: inset -1px 0 0 rgba(0, 0, 0, .25);
        }
        
        .navbar .navbar-toggler {
            top: .25rem;
            right: 1rem;
        }
    </style>
</head>
<body>
    <header class="navbar navbar-dark sticky-top bg-dark flex-md-nowrap p-0 shadow">
        <a class="navbar-brand col-md-3 col-lg-2 me-0 px-3" href="{{ url_for('dashboard') }}">Admin Panel</a>
        <button class="navbar-toggler position-absolute d-md-none collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#sidebarMenu" aria-controls="sidebarMenu" aria-expanded="false" aria-label="Toggle navigation">
            <span class="navbar-toggler-icon"></span>
        </button>
        <div class="w-100"></div>
        <div class="navbar-nav">
            <div class="nav-item text-nowrap">
                <a class="nav-link px-3" href="{{ url_for('logout') }}">Sign out</a>
            </div>
        </div>
    </header>
    
    <div class="container-fluid">
        <div class="row">
            <nav id="sidebarMenu" class="col-md-3 col-lg-2 d-md-block bg-light sidebar collapse">
                <div class="position-sticky pt-3">
                    <ul class="nav flex-column">
                        <li class="nav-item">
                            <a class="nav-link {% if request.path == url_for('dashboard') %}active{% endif %}" href="{{ url_for('dashboard') }}">
                                <i class="bi bi-speedometer2"></i>
                                Dashboard
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link {% if request.path == url_for('users') %}active{% endif %}" href="{{ url_for('users') }}">
                                <i class="bi bi-people"></i>
                                Users
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link {% if request.path == url_for('donors') %}active{% endif %}" href="{{ url_for('donors') }}">
                                <i class="bi bi-cash-coin"></i>
                                Donors
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link {% if request.path == url_for('logs') %}active{% endif %}" href="{{ url_for('logs') }}">
                                <i class="bi bi-journal-text"></i>
                                Logs
                            </a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link {% if request.path == url_for('system_services') %}active{% endif %}" href="{{ url_for('system_services') }}">
                                <i class="bi bi-gear"></i>
                                System Services
                            </a>
                        </li>
                    </ul>
                </div>
            </nav>
            
            <main class="col-md-9 ms-sm-auto col-lg-10 px-md-4">
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        {% for category, message in messages %}
                            <div class="alert alert-{{ category }} alert-dismissible fade show mt-3" role="alert">
                                {{ message }}
                                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                            </div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}
                
                {% block content %}{% endblock %}
            </main>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/feather-icons@4.28.0/dist/feather.min.js"></script>
    <script>
        // Initialize feather icons
        document.addEventListener('DOMContentLoaded', function() {
            feather.replace();
        });
    </script>
</body>
</html>""")

# --- Main Entry Point ---
if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create additional templates
    create_additional_templates()
    
    # Initialize database and create tables
    init_db()
    
    # Initialize admin user
    init_admin_user()
    
    # Run the app
    port = int(os.environ.get('PORT', 5002))
    app.config['PORT'] = port
    app.run(host='0.0.0.0', port=port, debug=True)
