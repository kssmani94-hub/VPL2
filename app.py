import os
import csv
import sqlite3
import random
from datetime import datetime, timedelta
from io import StringIO, TextIOWrapper
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.sql.expression import func
from sqlalchemy import event
from sqlalchemy.engine import Engine

# ==========================================
# 1. APP CONFIGURATION & DATABASE SETUP
# ==========================================
app = Flask(__name__)
app.secret_key = "vpl_2026_super_secure_key"

# Database Path: Points to 'instance/vpl_database.db'
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'vpl_database.db')
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SQLite Optimization (Prevent Database Locked Errors)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"connect_args": {"timeout": 15}}

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Enable WAL Mode for Speed (Crucial for Live Auction)
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

db = SQLAlchemy(app)

# ==========================================
# 2. LOGIN MANAGER
# ==========================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))  # <--- NEW WAY
    
# ==========================================
# 3. DATABASE MODELS
# ==========================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='captain') # 'super_admin', 'admin', 'captain'
    team = db.Column(db.String(100), nullable=True) # Linked Team Name
    last_seen = db.Column(db.DateTime, default=datetime.now)

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    logo = db.Column(db.String(100), default='default_team.png')
    color = db.Column(db.String(20), default='#00a6fb')
    
    # Financials
    purse_amount = db.Column(db.Integer, default=10000)
    spent_amount = db.Column(db.Integer, default=0)
    players_count = db.Column(db.Integer, default=0)
    rtm_count = db.Column(db.Integer, default=2) 
    
    players = db.relationship('Player', backref='team', lazy=True)

    @property
    def purse_rem(self):
        return self.purse_amount - self.spent_amount

    @property
    def slots_left(self):
        return 15 - self.players_count

    @property
    def max_bid(self):
        if self.slots_left <= 0: return 0
        if self.slots_left == 1: return self.purse_rem
        # Reserve 200 base price for every OTHER empty slot
        reserved_money = (self.slots_left - 1) * 200 
        return max(0, self.purse_rem - reserved_money)
    @property
    def team_id(self):
        # This allows your HTML to use 'current_user.team_id'
        if self.team:
            t = Team.query.filter_by(name=self.team).first()
            return t.id if t else None
        return None    

class Player(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vpl_id = db.Column(db.String(20), unique=True)
    full_name = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer) 
    phone = db.Column(db.String(15), unique=True, nullable=False)
    level = db.Column(db.String(50))
    role = db.Column(db.String(50))
    style = db.Column(db.String(100))
    
    # Additional Info
    ch_mobile = db.Column(db.String(15))
    ch_name = db.Column(db.String(100))
    current_team = db.Column(db.String(100))
    prev_team = db.Column(db.String(100))
    
    # Jersey Info
    photo = db.Column(db.String(200))
    shirt_name = db.Column(db.String(50))
    shirt_number = db.Column(db.Integer)
    shirt_size = db.Column(db.String(10))
    sleeves = db.Column(db.String(20))
    
    # Status & Payment
    payment_method = db.Column(db.String(50))
    status = db.Column(db.String(20), default='Pending Approval')
    payment_screenshot = db.Column(db.String(200))
    comments = db.Column(db.Text) 
    
    # Stats (Batting/Bowling)
    vpl_mat = db.Column(db.Integer, default=0)
    vpl_runs = db.Column(db.Integer, default=0)
    vpl_wkts = db.Column(db.Integer, default=0)
    vpl_sr = db.Column(db.Float, default=0.0)
    or_mat = db.Column(db.Integer, default=0)
    or_runs = db.Column(db.Integer, default=0)
    or_wkts = db.Column(db.Integer, default=0)
    or_sr = db.Column(db.Float, default=0.0)

    # Auction Data
    auction_status = db.Column(db.String(20), default='Upcoming') # Upcoming, Live, Sold, Unsold
    base_price = db.Column(db.Integer, default=200)
    current_bid = db.Column(db.Integer, default=0)
    bid_team_name = db.Column(db.String(100), nullable=True)
    sold_price = db.Column(db.Integer, default=0)
    team_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=True)


# --- ADD THIS NEW CLASS UNDER THE 'Player' CLASS ---
class Wishlist(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=False)


class AuctionControl(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(20), default='Not Started') 
    current_player_id = db.Column(db.Integer, db.ForeignKey('player.id'), nullable=True)

class Log(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    message = db.Column(db.String(200), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.now)


# ==========================================
# 4. CUSTOM HELPERS & DECORATORS
# ==========================================

# Updates Last Seen automatically on every request
@app.before_request
def update_last_seen():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.now()
        db.session.commit()

# Injects "Online Users" count into every HTML page
@app.context_processor
def inject_online_count():
    try:
        five_mins_ago = datetime.now() - timedelta(minutes=5)
        count = User.query.filter(User.last_seen > five_mins_ago).count()
    except:
        count = 0
    return dict(online_count=count)

def roles_required(*roles):
    """Custom decorator to restrict access based on User Role"""
    def wrapper(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in first.", "warning")
                return redirect(url_for('login'))
            if current_user.role not in roles:
                flash("Access Denied: You do not have permission.", "danger")
                return redirect(url_for('home'))
            return f(*args, **kwargs)
        return decorated_function
    return wrapper

def log_activity(message):
    """Logs actions to the database for audit trails"""
    try:
        user = current_user.username if current_user.is_authenticated else "System"
        new_log = Log(username=user, message=message)
        db.session.add(new_log)
        db.session.commit()
    except Exception as e:
        print(f"Failed to log: {e}")

# ==========================================
# 5. PUBLIC & GENERAL ROUTES
# ==========================================

@app.route('/')
@app.route('/home')
def home():
    # 1. Check if the URL has "?view=home" (This is the key fix)
    # If this is present, we skip the redirect and show the page.
    force_home = request.args.get('view') == 'home'

    # 2. Redirect ONLY if we are NOT forcing the home view
    if not force_home and current_user.is_authenticated:
        if current_user.role in ['admin', 'super_admin']:
            return redirect(url_for('players'))
        return redirect(url_for('teams'))
    
    # 3. Public Homepage Data
    total_slots = 230  # Updated to 230
    registered_count = Player.query.count()
    remaining = total_slots - registered_count
    
    return render_template('index.html', remaining=remaining, total_slots=total_slots)
    
@app.route('/gallery')
def gallery():
    champion = {
        'title': 'VPL 2025 Champions - Master Blasters',
        'image': 'champion_team.jpg' 
    }
    # Generates a list of images img1.jpg to img26.jpg
    photos = [f"img{i}.jpg" for i in range(1, 27)] 
    return render_template('gallery.html', champion=champion, photos=photos)

@app.route('/total_players')
def total_players():
    players_list = Player.query.order_by(Player.vpl_id).all()
    return render_template('total_players.html', players=players_list)

@app.route('/debug_stats')
@roles_required('super_admin')
def debug_stats():
    """Raw view of database for troubleshooting"""
    all_players = Player.query.all()
    output = "<h3>Database Debug Info:</h3><ul>"
    for p in all_players:
        output += f"<li>ID: {p.vpl_id} | Name: {p.full_name} | Status: {p.auction_status} | Sold: {p.sold_price}</li>"
    output += "</ul>"
    return output

# ==========================================
# 6. REGISTRATION & PAYMENT ROUTES
# ==========================================

@app.route('/register', methods=['GET', 'POST'])
def register():
    # Deadline Check
    if datetime.now() > datetime(2026, 1, 24, 23, 59):
        flash('Registration Closed.', 'danger')
        return redirect(url_for('home'))

    # Capacity Check
    if Player.query.count() >= 200:
        flash('Registration is full!', 'warning')
        return redirect(url_for('home'))

    if request.method == 'POST':
        phone = request.form.get('phone')
        if Player.query.filter_by(phone=phone).first():
            flash('Mobile already registered!', 'danger')
            return redirect(url_for('register'))
        
        # ID Generation (Fill Gaps logic from your original code)
        existing_numbers = [int(p.vpl_id.split('-')[1]) for p in Player.query.all() if p.vpl_id and '-' in p.vpl_id]
        new_id_num = 1
        while new_id_num in existing_numbers:
            new_id_num += 1
        v_id = f"VPL-{new_id_num:03d}"
        
        # Photo Upload Logic
        file = request.files.get('photo')
        photo_fn = f"{v_id}.jpg"
        if file:
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], photo_fn))
        else:
            photo_fn = 'default.jpg'

        # Create Player
        new_p = Player(
            vpl_id=v_id, full_name=request.form.get('full_name'), 
            age=request.form.get('age'), phone=phone, level=request.form.get('level'), 
            ch_mobile=request.form.get('ch_mobile'), ch_name=request.form.get('ch_name'),
            current_team=request.form.get('current_team'), 
            prev_team=request.form.get('prev_team'), 
            role=request.form.get('role'), style=request.form.get('style'), 
            photo=photo_fn, shirt_name=request.form.get('shirt_name'), 
            shirt_number=request.form.get('shirt_number'), shirt_size=request.form.get('shirt_size'), 
            sleeves=request.form.get('sleeves')
        )
        db.session.add(new_p)
        db.session.commit()
        return redirect(url_for('payment', player_id=new_p.id))

    return render_template('register.html')

@app.route('/payment/<int:player_id>', methods=['GET', 'POST'])
def payment(player_id):
    player = Player.query.get_or_404(player_id)
    if request.method == 'POST':
        method = request.form.get('payment_method')
        player.payment_method = method
        if method == 'UPI':
            file = request.files.get('screenshot')
            if file:
                pay_name = f"PAY_{player.vpl_id}.jpg"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], pay_name))
                player.payment_screenshot = pay_name
        
        player.status = 'Pending Approval'
        db.session.commit()
        return render_template('payment.html', player=player, success=True)
    return render_template('payment.html', player=player, success=False)

# ==========================================
# 7. AUTHENTICATION (Fixed for Flask-Login)
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password, password):
            login_user(user)
            user.last_seen = datetime.now()
            db.session.commit()
            
            log_activity(f"Logged in")
            
            if user.role in ['admin', 'super_admin']:
                # Admins go to the auction dashboard
                return redirect(url_for('auction')) 
            else:
                # Captains go to their team page
                return redirect(url_for('teams')) 
        
        flash('Invalid Credentials', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    logout_user()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('login'))

# ==========================================
# 8. ADMIN DASHBOARD & USER MANAGEMENT
# ==========================================

@app.route('/admin_dashboard')
@roles_required('admin', 'super_admin')
def admin_dashboard():
    """Manages Users (Captains/Admins)"""
    users = User.query.all()
    teams = Team.query.all()
    return render_template('admin_dashboard.html', users=users, teams=teams)

@app.route('/create_user_action', methods=['POST'])
@roles_required('super_admin')
def create_user_action():
    username = request.form.get('new_username')
    password = request.form.get('new_password')
    role = request.form.get('role')
    team_name = request.form.get('team_name')

    if User.query.filter_by(username=username).first():
        flash('Username already exists!', 'danger')
    else:
        new_user = User(username=username, password=generate_password_hash(password), role=role, team=team_name)
        db.session.add(new_user)
        db.session.commit()
        flash('User Created Successfully', 'success')
        log_activity(f"Created user {username}")
    return redirect(url_for('admin_dashboard_users'))

@app.route('/delete_user/<int:id>', methods=['POST'])
@roles_required('super_admin')
def delete_user(id):
    user = User.query.get(id)
    if user:
        if user.username == 'admin':
            flash('Cannot delete main Admin!', 'danger')
        else:
            db.session.delete(user)
            db.session.commit()
            flash('User deleted.', 'success')
    return redirect(url_for('admin_dashboard_users'))

# ==========================================
# 9. DATA MANAGEMENT (Import/Export/Logs)
# ==========================================

@app.route('/admin/import_stats', methods=['GET', 'POST'])
@roles_required('super_admin', 'admin')
def import_stats():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file or not file.filename.endswith('.csv'):
            flash('Upload a valid CSV.', 'danger')
            return redirect(request.url)

        csv_file = TextIOWrapper(file.stream, encoding='utf-8')
        reader = csv.DictReader(csv_file)
        updated = 0
        for row in reader:
            player = Player.query.filter_by(vpl_id=row['vpl_id']).first()
            if player:
                # Update stats safely handling empty values
                player.vpl_mat = int(row.get('vpl_mat', 0) or 0)
                player.vpl_runs = int(row.get('vpl_runs', 0) or 0)
                player.vpl_wkts = int(row.get('vpl_wkts', 0) or 0)
                player.vpl_sr = float(row.get('vpl_sr', 0.0) or 0.0)
                updated += 1
        db.session.commit()
        flash(f'Synced data for {updated} players!', 'success')
        return redirect(url_for('players'))
    return render_template('import_stats.html')


# --- 1. STATS ROUTE (Fixed variable name) ---
@app.route('/stats')
def stats():
    all_players = Player.query.all()
    
    # Calculate Wishlist IDs
    # We rename this to 'wishlist_ids' to match your HTML error log
    wishlist_ids = []
    if current_user.is_authenticated:
        wishlist_items = Wishlist.query.filter_by(user_id=current_user.id).all()
        wishlist_ids = [item.player_id for item in wishlist_items]

    # Pass 'wishlist_ids' (NOT wishlisted_ids) to the template
    return render_template('stats.html', 
                         players=all_players, 
                         wishlist_ids=wishlist_ids)

# --- 2. TOGGLE ROUTE (JSON for the Star Button) ---
@app.route('/toggle_wishlist/<int:player_id>', methods=['POST'])
@login_required
def toggle_wishlist(player_id):
    try:
        item = Wishlist.query.filter_by(user_id=current_user.id, player_id=player_id).first()

        if item:
            db.session.delete(item)
            action = 'removed'
        else:
            new_item = Wishlist(user_id=current_user.id, player_id=player_id)
            db.session.add(new_item)
            action = 'added'
        
        db.session.commit()
        return jsonify({'status': 'success', 'action': action})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
        
@app.route('/export_players')
@roles_required('admin', 'super_admin')
def export_players():
    """Download all player data as CSV"""
    players_list = Player.query.all()
    si = StringIO()
    cw = csv.writer(si)
    
    # Header
    cw.writerow(['VPL ID', 'Full Name', 'Age', 'Phone', 'Role', 'Status', 'Price', 'Sold To'])
    
    # Data
    for p in players_list:
        team_name = p.team.name if p.team else "N/A"
        cw.writerow([p.vpl_id, p.full_name, p.age, p.phone, p.role, p.status, p.sold_price, team_name])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=VPL_Full_Export_2026.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/activity_logs')
@roles_required('super_admin')
def activity_logs():
    logs = Log.query.order_by(Log.timestamp.desc()).all()
    return render_template('logs.html', logs=logs)

# ==========================================
# 10. PLAYER CRUD (Edit/Delete)
# ==========================================

@app.route('/players')
@login_required
def players():
    all_players = Player.query.all()
    return render_template('players.html', players=all_players)

@app.route('/edit_player/<int:id>', methods=['GET', 'POST'])
@roles_required('super_admin', 'admin')
def edit_player(id):
    player = Player.query.get_or_404(id)
    if request.method == 'POST':
        player.full_name = request.form.get('full_name')
        player.status = request.form.get('status')
        player.base_price = int(request.form.get('base_price', 200))
        
        # Stats Update
        player.vpl_mat = int(request.form.get('vpl_mat', 0))
        player.vpl_runs = int(request.form.get('vpl_runs', 0))
        player.vpl_wkts = int(request.form.get('vpl_wkts', 0))
        
        db.session.commit()
        log_activity(f"Edited player {player.vpl_id}")
        return redirect(url_for('players'))
    return render_template('edit_player.html', player=player)

@app.route('/delete_player/<int:id>', methods=['POST'])
@roles_required('super_admin')
def delete_player(id):
    player = Player.query.get_or_404(id)
    name = player.full_name
    db.session.delete(player)
    db.session.commit()
    flash(f'Player {name} Deleted', 'success')
    log_activity(f"Deleted player {name}")
    return redirect(url_for('players'))

# ==========================================
# 11. AUCTION LOGIC & ROUTES
# ==========================================

# NOTE: This route handles BOTH the 'projector' view and the 'admin' view
# We use 'auction.html' because you mentioned you don't have 'auction.html'
@app.route('/auction')
def auction():
    # 1. Auction Control (Create if missing)
    control = AuctionControl.query.first()
    if not control:
        control = AuctionControl(current_player_id=None, status='Not Started')
        db.session.add(control)
        db.session.commit()

    # 2. Get Live Player (Safe for SQL Alchemy 2.0)
    if control.current_player_id:
        current_player = db.session.get(Player, control.current_player_id)
    else:
        current_player = None

    # 3. Global Stats
    # We use a robust filter: Anyone NOT Sold/Unsold is considered "Available"
    stats = {
        'pool': Player.query.count(),
        'sold': Player.query.filter_by(auction_status='Sold').count(),
        'unsold': Player.query.filter_by(auction_status='Unsold').count(),
        'available': Player.query.filter(Player.auction_status.notin_(['Sold', 'Unsold'])).count()
    }

    # 4. Team Dashboard Data
    teams = Team.query.all()
    dash_data = []
    MAX_PURSE = 10000 # Adjust if your max budget is different
    MAX_SLOTS = 15    # Adjust if your team size is different
    
    for t in teams:
        dash_data.append({
            'id': t.id,
            'name': t.name,
            'purse_rem': t.purse_rem,
            'purse_pct': (t.purse_rem / MAX_PURSE) * 100 if MAX_PURSE > 0 else 0,
            'slots_left': t.slots_left,
            'slots_pct': (t.slots_left / MAX_SLOTS) * 100 if MAX_SLOTS > 0 else 0,
            'max_bid': t.max_bid,
            'max_bid_pct': (t.max_bid / MAX_PURSE) * 100 if MAX_PURSE > 0 else 0,
            'rtm_count': t.rtm_count
        })

    # 5. Determine Roles (THE FIX IS HERE)
    # We check "is_authenticated" first. If False, Python stops reading and sets variables to False.
    is_admin = current_user.is_authenticated and current_user.role in ['admin', 'super_admin']
    is_captain = current_user.is_authenticated and current_user.role == 'captain'

    # 6. Fetch Sold/Unsold Lists
    sold_players = Player.query.filter_by(auction_status='Sold').order_by(Player.id.desc()).all()
    unsold_players = Player.query.filter_by(auction_status='Unsold').order_by(Player.id.desc()).all()

    # 7. CAPTAIN'S WISHLIST LOGIC
    wishlist_ids = []
    my_wishlist_players = []
    
    if is_captain:
        # This block only runs if the user is logged in AND is a captain
        wishlist_items = Wishlist.query.filter_by(user_id=current_user.id).all()
        wishlist_ids = [item.player_id for item in wishlist_items]
        
        if wishlist_ids:
            my_wishlist_players = Player.query.filter(Player.id.in_(wishlist_ids)).all()

    return render_template('auction.html', 
                           player=current_player, 
                           teams=dash_data, 
                           stats=stats, 
                           control=control,
                           is_admin=is_admin,
                           is_captain=is_captain,
                           sold_players=sold_players,
                           unsold_players=unsold_players,
                           wishlist_ids=wishlist_ids,
                           my_wishlist_players=my_wishlist_players)                           

@app.route('/auction_control/<action>')
@roles_required('admin', 'super_admin')
def auction_control(action):
    control = AuctionControl.query.first()
    
    if action == 'start':
        control.status = 'Live'
        db.session.commit()
        # Auto-pick first player if nobody is live
        if not Player.query.filter_by(auction_status='Live').first():
            return redirect(url_for('pick_random_player'))
            
    elif action == 'pause':
        control.status = 'Paused'
        db.session.commit()
        
    elif action == 'resume':
        control.status = 'Live'
        db.session.commit()
        
    return redirect(url_for('auction'))

@app.route('/revert_unsold', methods=['POST'])
@login_required
def revert_unsold():
    if current_user.role not in ['admin', 'super_admin']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('auction'))

    player_id = request.form.get('player_id')
    player = Player.query.get(player_id)

    if player and player.auction_status == 'Unsold':
        # Change status back to 'Approved' (or 'Pending') so they appear in Round 1 again
        player.auction_status = 'Approved'
        db.session.commit()
        flash(f'Reverted {player.full_name} from Unsold list.', 'success')
    else:
        flash('Player not found or not in Unsold list.', 'warning')

    return redirect(url_for('auction'))
    

@app.route('/pick_random_player')
@login_required
def pick_random_player():
    if current_user.role not in ['admin', 'super_admin']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('auction'))

    # --- DEBUG START ---
    print("--------------------------------------------------")
    print("🟢 DEBUG: STARTING PICK RANDOM PLAYER")

    # 1. Get or Create Auction Control (Prevents crashes if table is empty)
    control = AuctionControl.query.first()
    if not control:
        print("⚠️ DEBUG: No Control Row Found! Creating one...")
        control = AuctionControl(current_player_id=0)
        db.session.add(control)
        db.session.commit()
    
    current_id = control.current_player_id
    print(f"🧐 DEBUG: Current Player ID on Screen: {current_id}")

    next_player = None
    phase_message = ""
    
    # 2. Refresh Database (Important!)
    db.session.expire_all() 
    all_players = Player.query.all()
    print(f"📊 DEBUG: Total Players in DB: {len(all_players)}")

    role_priority = ['Keeper', 'Bat', 'All', 'Bowl']
    
    # =========================================================
    # ROUND 1: LOGIC
    # =========================================================
    
    for search_term in role_priority:
        candidates = []
        for p in all_players:
            # CLEAN DATA
            p_status = str(p.auction_status).lower().strip()
            p_role = str(p.role).lower().strip()
            search_key = search_term.lower()

            # FILTER LOGIC
            # 1. Skip if Sold/Unsold
            if p_status in ['sold', 'unsold']:
                continue 
            
            # 2. Skip if it is the current player (CRITICAL STEP)
            if p.id == current_id:
                continue 

            # 3. Check Role
            if search_key in p_role:
                candidates.append(p)
        
        # DEBUG: See what we found for this role
        print(f"🔎 DEBUG: Searching '{search_term}' -> Found {len(candidates)} candidates.")

        if candidates:
            next_player = random.choice(candidates)
            print(f"✅ DEBUG: Picked Next Player: {next_player.full_name} (ID: {next_player.id})")
            
            if 'keeper' in search_term.lower(): phase_message = "Round 1: Wicket-Keeper"
            elif 'bat' in search_term.lower():  phase_message = "Round 1: Batter"
            elif 'all' in search_term.lower():  phase_message = "Round 1: All-Rounder"
            elif 'bowl' in search_term.lower(): phase_message = "Round 1: Bowler"
            break 

    # =========================================================
    # ROUND 2: UNSOLD FALLBACK
    # =========================================================
    if not next_player:
        print("⚠️ DEBUG: No Fresh Players found. Checking Unsold...")
        unsold_candidates = [p for p in all_players if str(p.auction_status).lower().strip() == 'unsold']
        
        if unsold_candidates:
            next_player = random.choice(unsold_candidates)
            next_player.auction_status = 'Approved'
            db.session.commit()
            phase_message = f"Unsold Round: Bringing back {next_player.full_name}"
            print(f"♻️ DEBUG: Reviving Unsold Player: {next_player.full_name}")

    # =========================================================
    # EXECUTION
    # =========================================================
    if next_player:
        print(f"💾 DEBUG: Saving New ID {next_player.id} to Database...")
        control.current_player_id = next_player.id
        db.session.commit()
        
        if phase_message: flash(phase_message, 'info')
        print("🚀 DEBUG: Redirecting to Auction Page.")
        print("--------------------------------------------------")
        return redirect(url_for('auction'))
    else:
        print("🛑 DEBUG: NO PLAYERS LEFT! AUCTION COMPLETE.")
        control.current_player_id = None
        db.session.commit()
        flash('🏆 Auction Fully Complete! No players left.', 'success')
        return redirect(url_for('auction'))
        
        
        
import random

@app.route('/finalize_sale', methods=['POST'])
@login_required
def finalize_sale():
    # Security Check
    if current_user.role not in ['admin', 'super_admin']:
        flash('Unauthorized access.', 'danger')
        return redirect(url_for('auction'))

    # 1. Get Data from Form
    player_id = request.form.get('player_id')
    team_id = request.form.get('team_id')
    
    try:
        sold_price = int(request.form.get('sold_price'))
    except:
        sold_price = 0
        
    # 2. Fetch Objects (SQLAlchemy 2.0 safe)
    player = db.session.get(Player, player_id)
    team = db.session.get(Team, team_id)
    
    if player and team:
        # --- A. VALIDATE & SAVE SALE ---
        if sold_price > team.max_bid:
             flash("Team does not have enough budget for this bid!", "danger")
             return redirect(url_for('auction'))

        # Mark as Sold
        player.auction_status = 'Sold'
        player.sold_price = sold_price
        player.team_id = team.id
        
        team.spent_amount += sold_price
        team.players_count += 1
        
        # Save the sale first
        db.session.commit()

        # --- B. AUTO-PICK NEXT PLAYER (No Button Click Needed) ---
        # We run the robust picking logic right here, instantly.
        
        all_players = Player.query.all()
        role_priority = ['Keeper', 'Bat', 'All', 'Bowl']
        next_player = None
        
        # 1. Look for Fresh Players
        for search_term in role_priority:
            candidates = []
            for p in all_players:
                p_status = str(p.auction_status).lower().strip()
                p_role = str(p.role).lower().strip()
                
                # Filter: Must not be Sold or Unsold
                if p_status in ['sold', 'unsold']:
                    continue
                
                # Check Role
                if search_term.lower() in p_role:
                    candidates.append(p)
            
            if candidates:
                next_player = random.choice(candidates)
                break # Found one!
        
        # 2. If no Fresh Players, Look for Unsold
        if not next_player:
            unsold_candidates = [p for p in all_players if str(p.auction_status).lower().strip() == 'unsold']
            if unsold_candidates:
                next_player = random.choice(unsold_candidates)
                next_player.auction_status = 'Approved' # Bring back to life
                db.session.commit()

        # --- C. UPDATE SCREEN & MESSAGE ---
        control = AuctionControl.query.first()
        if not control:
            control = AuctionControl(current_player_id=0)
            db.session.add(control)
        
        if next_player:
            # Set the screen to the NEW player immediately
            control.current_player_id = next_player.id
            db.session.commit()
        else:
            # Auction Over
            control.current_player_id = None
            db.session.commit()

        # --- D. SHOW CELEBRATION ---
        message_text = f"Congrats {player.full_name} sold to {team.name} for {sold_price} points"
        
        if sold_price > 1000:
            flash(f"MEGA BID! {message_text}", "success")
        else:
            flash(message_text, "success")
        
        # Redirect to auction page
        # The page will load with the CONGRATS overlay on top
        # When closed, the NEXT player is already visible behind it.
        return redirect(url_for('auction'))
        
    return redirect(url_for('auction'))    
    
@app.route('/mark_unsold/<int:player_id>')
@roles_required('admin', 'super_admin')
def mark_unsold(player_id):
    player = Player.query.get(player_id)
    if player:
        player.auction_status = 'Unsold'
        db.session.commit()
        flash(f"{player.full_name} marked Unsold.", "info")
        return redirect(url_for('pick_random_player'))
    return redirect(url_for('auction'))

@app.route('/auction/revert', methods=['POST'])
@roles_required('super_admin')
def revert_last_sale():
    """Reverts the last 'Sold' player to 'Upcoming' and refunds money"""
    # Logic: Find the most recently updated 'Sold' player (using ID as proxy usually)
    # Since we don't track 'sold_time', we might just look for the specific player ID if passed,
    # or implementing a manual fix. Here is a safe implementation:
    player_id = request.form.get('player_id')
    player = Player.query.get(player_id)
    
    if player and player.auction_status == 'Sold':
        team = Team.query.get(player.team_id)
        if team:
            team.spent_amount -= player.sold_price
            team.players_count -= 1
            
        player.auction_status = 'Upcoming'
        player.sold_price = 0
        player.team_id = None
        db.session.commit()
        flash(f"Sale reverted for {player.full_name}", "warning")
        
    return redirect(url_for('auction'))

@app.route('/get_max_bid/<int:team_id>')
def get_max_bid(team_id):
    """API endpoint for frontend JavaScript to check bid limits"""
    team = Team.query.get(team_id)
    if team:
        return jsonify({"max_bid_allowed": team.max_bid})
    return jsonify({"max_bid_allowed": 0})

@app.route('/reset_auction', methods=['POST'])
@roles_required('super_admin')
def reset_auction():
    """Nuclear option: Resets EVERYTHING"""
    password = request.form.get('admin_password')
    if password != "RESET2026": 
        flash("Incorrect Password!", "danger")
        return redirect(url_for('auction'))

    try:
        # Reset Teams
        teams = Team.query.all()
        for t in teams:
            t.spent_amount = 0
            t.players_count = 0
        
        # Reset Players
        players = Player.query.all()
        for p in players:
            p.auction_status = 'Upcoming'
            p.current_bid = 0
            p.bid_team_name = None
            p.current_team = None
            p.team_id = None
            p.sold_price = 0
        
        # Reset Control
        control = AuctionControl.query.first()
        if control:
            control.status = 'Not Started'
        
        db.session.commit()
        flash("Auction RESET successful.", "success")
        log_activity("Auction RESET performed")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {e}", "danger")

    return redirect(url_for('auction'))

# ==========================================
# 12. TEAM STATUS & DETAIL PAGES
# ==========================================

# --- ADD THIS NEW EXPORT ROUTE ---
@app.route('/export_team_excel/<int:team_id>')
@login_required
def export_team_excel(team_id):
    team = Team.query.get_or_404(team_id)
    
    # Security Check: Only Admin or the Team's Captain can export
    if current_user.role != 'admin' and current_user.role != 'super_admin':
        # If not admin, check if they are the captain of THIS team
        if current_user.team != team.name:
            flash('You are not authorized to export this team data.', 'danger')
            return redirect(url_for('team_detail', id=team.id))

    # Generate CSV File
    si = StringIO()
    cw = csv.writer(si)
    
    # Headers
    cw.writerow(['Player Name', 'Role', 'Status', 'Sold Price'])
    
    # Data
    for p in team.players:
        cw.writerow([p.full_name, p.role, p.status, p.sold_price])

    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename={team.name}_Roster.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@app.route('/teams')
def teams():
    teams = Team.query.all()
    return render_template('teams.html', teams=teams)

@app.route('/team/<int:id>')
def team_detail(id):
    team = Team.query.get_or_404(id)
    captain = User.query.filter_by(team=team.name).first()
    
    show_secrets = False
    if current_user.is_authenticated:
        if current_user.role in ['admin', 'super_admin'] or current_user.team == team.name:
            show_secrets = True

    return render_template('team_detail.html', team=team, captain=captain, show_secrets=show_secrets)

# --- WISHLIST ROUTES ---

@app.route('/add_wishlist/<int:player_id>')
@login_required
def add_wishlist(player_id):
    # 1. Check if already in wishlist
    existing = Wishlist.query.filter_by(user_id=current_user.id, player_id=player_id).first()
    
    if not existing:
        # 2. Add to database
        new_item = Wishlist(user_id=current_user.id, player_id=player_id)
        db.session.add(new_item)
        db.session.commit()
        flash('Player added to your wishlist!', 'success')
    else:
        flash('Player is already in your wishlist.', 'info')
        
    # 3. Go back to stats page
    return redirect(url_for('stats'))

@app.route('/remove_wishlist/<int:player_id>')
@login_required
def remove_wishlist(player_id):
    item = Wishlist.query.filter_by(user_id=current_user.id, player_id=player_id).first()
    if item:
        db.session.delete(item)
        db.session.commit()
        flash('Player removed from wishlist.', 'warning')
    return redirect(url_for('stats'))

# ==========================================
# 13. APP ENTRY POINT
# ==========================================

if __name__ == '__main__':
    with app.app_context():
        # Auto-create tables if they don't exist
        db.create_all()
        
        # Initialize Auction Control
        if not AuctionControl.query.first():
            db.session.add(AuctionControl(status='Not Started'))
            db.session.commit()
            
        # Optional: Initialize Teams if table is empty (Helper)
        if Team.query.count() == 0:
            teams_list = [
                {'name': 'Chennai Kings', 'color': '#f9d423'},
                {'name': 'Cuddalore Warriors', 'color': '#00529b'},
                {'name': 'Neyveli Strikers', 'color': '#ed1c24'},
                {'name': 'Chidambaram Tigers', 'color': '#ff7e5f'},
                {'name': 'VPL Avengers', 'color': '#2af598'},
                {'name': 'Kattumannarkoil Lions', 'color': '#8e44ad'},
                {'name': 'Coastal Giants', 'color': '#00c6ff'},
                {'name': 'Delta Riders', 'color': '#f093fb'},
                {'name': 'Temple City Stars', 'color': '#eb3349'},
                {'name': 'Vallalar United', 'color': '#11998e'}
            ]
            for t_data in teams_list:
                db.session.add(Team(name=t_data['name'], color=t_data['color']))
            db.session.commit()
            
    app.run(debug=True)