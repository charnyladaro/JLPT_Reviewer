from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import sqlite3
import os
from datetime import datetime, timedelta
import uuid
import fitz
import base64
import io
from PIL import Image
import json
from flask import Response
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Allowed file extensions
ALLOWED_EXTENSIONS = {
    'books': {'pdf', 'epub', 'mobi', 'txt'},
    'reviewers': {'pdf', 'doc', 'docx', 'txt'},
    'audio': {'mp3', 'wav', 'm4a', 'ogg', 'flac'}
}

def allowed_file(filename, category):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS.get(category, set())

def init_db():
    """Initialize the database with required tables"""
    conn = sqlite3.connect('jlpt_study.db')
    cursor = conn.cursor()
    
    # Create admin users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admin_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create regular users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create courses table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            jlpt_level TEXT NOT NULL,
            instructor_name TEXT,
            duration_weeks INTEGER,
            max_students INTEGER,
            status TEXT DEFAULT 'active',
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES admin_users (id)
        )
    ''')
    
    # Create course materials table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS course_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            order_index INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (material_id) REFERENCES study_materials (id) ON DELETE CASCADE
        )
    ''')
    
    # Create enrollments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP NULL,
            approved_by INTEGER NULL,
            notes TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
            FOREIGN KEY (approved_by) REFERENCES admin_users (id),
            UNIQUE(user_id, course_id)
        )
    ''')
    
    # Create study materials table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS study_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            jlpt_level TEXT NOT NULL,
            category TEXT NOT NULL,
            file_size INTEGER,
            file_type TEXT,
            uploaded_by INTEGER,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            description TEXT,
            FOREIGN KEY (uploaded_by) REFERENCES admin_users (id)
        )
    ''')
    
    # Create default admin user if not exists
    cursor.execute('SELECT COUNT(*) FROM admin_users WHERE username = ?', ('admin',))
    if cursor.fetchone()[0] == 0:
        password_hash = generate_password_hash('admin123')
        cursor.execute('''
            INSERT INTO admin_users (username, password_hash, email) 
            VALUES (?, ?, ?)
        ''', ('admin', password_hash, 'admin@jlpt.com'))
    
    conn.commit()
    conn.close()

def get_db_connection():
    """Get database connection"""
    conn = sqlite3.connect('jlpt_study.db')
    conn.row_factory = sqlite3.Row
    return conn

def parse_datetime(date_string):
    """Parse datetime string safely"""
    if not date_string:
        return datetime.now()
    
    if isinstance(date_string, datetime):
        return date_string
    
    try:
        # Try different datetime formats
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d']:
            try:
                return datetime.strptime(date_string, fmt)
            except ValueError:
                continue
        # If all formats fail, return current time
        return datetime.now()
    except:
        return datetime.now()

# Decorator for login required
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# Decorator for admin required
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login_page'))
        return f(*args, **kwargs)
    return decorated_function

# Make session available in all templates
@app.context_processor
def inject_session():
    return dict(session=session)

# ===============================
# USER ROUTES
# ===============================

@app.route('/')
def index():
    """Main user interface - no authentication required"""
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        full_name = request.form['full_name']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        
        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('register'))
        
        conn = get_db_connection()
        existing = conn.execute('SELECT * FROM users WHERE username = ? OR email = ?', (username, email)).fetchone()
        if existing:
            flash('Username or email already exists!', 'error')
            conn.close()
            return redirect(url_for('register'))
        
        password_hash = generate_password_hash(password)
        conn.execute('INSERT INTO users (username, email, password_hash, full_name) VALUES (?, ?, ?, ?)',
                     (username, email, password_hash, full_name))
        conn.commit()
        conn.close()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE username = ? OR email = ?', (username, username)).fetchone()
        conn.close()
        
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Login successful!', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        else:
            flash('Invalid username/email or password!', 'error')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    session.pop('role', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


@app.route('/courses')
def courses():
    """Display all available courses with enrollment information"""
    try:
        conn = get_db_connection()
        
        # Get all active courses with enrollment count and creator info
        courses_query = '''
            SELECT c.*, 
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as enrolled_count,
                   (SELECT username FROM admin_users WHERE id = c.created_by) as created_by_name
            FROM courses c
            WHERE c.status = 'active'
            ORDER BY c.created_at DESC
        '''
        courses_list = conn.execute(courses_query).fetchall()
        
        # Get user's enrollments if logged in
        user_enrollments = []
        if 'user_id' in session:
            user_enrollments_query = '''
                SELECT course_id, status, enrolled_at, approved_at
                FROM enrollments
                WHERE user_id = ?
            '''
            user_enrollments = conn.execute(user_enrollments_query, (session['user_id'],)).fetchall()
        
        # Convert to list of dicts for easier template handling
        courses_data = []
        for course in courses_list:
            course_dict = dict(course)
            # Parse datetime safely
            if course_dict.get('created_at'):
                course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            courses_data.append(course_dict)
        
        # Convert user enrollments to list of dicts
        enrollments_data = []
        for enrollment in user_enrollments:
            enrollment_dict = dict(enrollment)
            if enrollment_dict.get('enrolled_at'):
                enrollment_dict['enrolled_at'] = parse_datetime(enrollment_dict['enrolled_at']).strftime('%Y-%m-%d %H:%M:%S')
            if enrollment_dict.get('approved_at'):
                enrollment_dict['approved_at'] = parse_datetime(enrollment_dict['approved_at']).strftime('%Y-%m-%d %H:%M:%S')
            enrollments_data.append(enrollment_dict)
        
        conn.close()
        
        return render_template('course_list.html', 
                             courses=courses_data,
                             user_enrollments=enrollments_data)
                             
    except Exception as e:
        app.logger.error(f'Error loading courses: {str(e)}')
        flash('Error loading courses. Please try again.', 'error')
        return redirect(url_for('index'))

@app.route('/courses/<int:course_id>')
def course_detail(course_id):
    """Display detailed course information with materials and enrollment status"""
    try:
        conn = get_db_connection()
        
        # Get course details
        course_query = '''
            SELECT c.*,
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as enrolled_count,
                   (SELECT username FROM admin_users WHERE id = c.created_by) as created_by_name
            FROM courses c
            WHERE c.id = ?
        '''
        course = conn.execute(course_query, (course_id,)).fetchone()
        
        if not course:
            flash('Course not found!', 'error')
            conn.close()
            return redirect(url_for('courses'))
        
        # Get course materials with proper ordering
        materials_query = '''
            SELECT sm.*, cm.order_index, cm.added_at
            FROM study_materials sm
            JOIN course_materials cm ON sm.id = cm.material_id
            WHERE cm.course_id = ?
            ORDER BY cm.order_index ASC, cm.added_at ASC
        '''
        materials = conn.execute(materials_query, (course_id,)).fetchall()
        
        # Check if user is enrolled and get enrollment status
        user_enrollment = None
        if 'user_id' in session:
            enrollment_query = '''
                SELECT *, 
                       (SELECT username FROM admin_users WHERE id = approved_by) as approved_by_name
                FROM enrollments 
                WHERE user_id = ? AND course_id = ?
            '''
            user_enrollment = conn.execute(enrollment_query, (session['user_id'], course_id)).fetchone()
        
        # Get recent enrollments for display (last 5 approved)
        recent_enrollments_query = '''
            SELECT u.username, u.full_name, e.approved_at
            FROM enrollments e
            JOIN users u ON e.user_id = u.id
            WHERE e.course_id = ? AND e.status = 'approved'
            ORDER BY e.approved_at DESC
            LIMIT 5
        '''
        recent_enrollments = conn.execute(recent_enrollments_query, (course_id,)).fetchall()
        
        # Convert course to dict and parse datetime
        course_dict = dict(course)
        if course_dict.get('created_at'):
            course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d %H:%M:%S')
        if course_dict.get('updated_at'):
            course_dict['updated_at'] = parse_datetime(course_dict['updated_at']).strftime('%Y-%m-%d %H:%M:%S')
        
        # Convert materials to list of dicts
        materials_data = []
        for material in materials:
            material_dict = dict(material)
            if material_dict.get('upload_date'):
                material_dict['upload_date'] = parse_datetime(material_dict['upload_date'])
            if material_dict.get('added_at'):
                material_dict['added_at'] = parse_datetime(material_dict['added_at'])
            materials_data.append(material_dict)
        
        # Convert enrollment data
        enrollment_data = None
        if user_enrollment:
            enrollment_data = dict(user_enrollment)
            if enrollment_data.get('enrolled_at'):
                enrollment_data['enrolled_at'] = parse_datetime(enrollment_data['enrolled_at']).strftime('%Y-%m-%d %H:%M:%S')
            if enrollment_data.get('approved_at'):
                enrollment_data['approved_at'] = parse_datetime(enrollment_data['approved_at']).strftime('%Y-%m-%d %H:%M:%S')
        
        # Convert recent enrollments
        recent_enrollments_data = []
        for enrollment in recent_enrollments:
            enrollment_dict = dict(enrollment)
            if enrollment_dict.get('approved_at'):
                enrollment_dict['approved_at'] = parse_datetime(enrollment_dict['approved_at']).strftime('%Y-%m-%d %H:%M:%S')
            recent_enrollments_data.append(enrollment_dict)
        
        conn.close()
        
        return render_template('course_detail.html', 
                             course=course_dict, 
                             materials=materials_data,
                             user_enrollment=enrollment_data,
                             recent_enrollments=recent_enrollments_data)
                             
    except Exception as e:
        app.logger.error(f'Error loading course {course_id}: {str(e)}')
        flash('Error loading course details. Please try again.', 'error')
        return redirect(url_for('courses'))

@app.route('/enroll/<int:course_id>', methods=['POST'])
@login_required
def enroll_course(course_id):
    """Handle course enrollment with enhanced validation"""
    try:
        conn = get_db_connection()
        
        # Get course details with current enrollment count
        course_query = '''
            SELECT c.*,
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as current_enrolled
            FROM courses c
            WHERE c.id = ? AND c.status = 'active'
        '''
        course = conn.execute(course_query, (course_id,)).fetchone()
        
        if not course:
            flash('Course not found or not available for enrollment!', 'error')
            conn.close()
            return redirect(url_for('courses'))
        
        # Check if user is already enrolled
        existing_query = '''
            SELECT * FROM enrollments WHERE user_id = ? AND course_id = ?
        '''
        existing = conn.execute(existing_query, (session['user_id'], course_id)).fetchone()
        
        if existing:
            if existing['status'] == 'pending':
                flash('You already have a pending enrollment request for this course!', 'info')
            elif existing['status'] == 'approved':
                flash('You are already enrolled in this course!', 'info')
            else:  # rejected
                flash('Your previous enrollment request was rejected. Please contact an administrator.', 'warning')
            
            conn.close()
            return redirect(url_for('course_detail', course_id=course_id))
        
        # Check if course is full
        if course['max_students'] and course['current_enrolled'] >= course['max_students']:
            flash('This course is full! No more enrollments are being accepted.', 'error')
            conn.close()
            return redirect(url_for('course_detail', course_id=course_id))
        
        # Create enrollment request
        enrollment_query = '''
            INSERT INTO enrollments (user_id, course_id, status, enrolled_at)
            VALUES (?, ?, 'pending', ?)
        '''
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(enrollment_query, (session['user_id'], course_id, current_time))
        
        conn.commit()
        conn.close()
        
        flash(f'✅ Enrollment request submitted successfully for "{course["title"]}"! Please wait for admin approval.', 'success')
        return redirect(url_for('course_detail', course_id=course_id))
        
    except Exception as e:
        app.logger.error(f'Error enrolling user {session.get("user_id")} in course {course_id}: {str(e)}')
        flash('An error occurred while processing your enrollment. Please try again.', 'error')
        return redirect(url_for('course_detail', course_id=course_id))
# API endpoint for course search (optional - for AJAX search)
@app.route('/api/courses/search')
def search_courses():
    """API endpoint for searching courses"""
    try:
        query = request.args.get('q', '').strip()
        level = request.args.get('level', '')
        instructor = request.args.get('instructor', '')
        
        conn = get_db_connection()
        
        # Build search query
        search_query = '''
            SELECT c.*, 
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as enrolled_count,
                   (SELECT username FROM admin_users WHERE id = c.created_by) as created_by_name
            FROM courses c
            WHERE c.status = 'active'
        '''
        params = []
        
        if query:
            search_query += ' AND (c.title LIKE ? OR c.description LIKE ? OR c.instructor_name LIKE ?)'
            query_param = f'%{query}%'
            params.extend([query_param, query_param, query_param])
        
        if level:
            search_query += ' AND c.jlpt_level = ?'
            params.append(level)
        
        if instructor:
            search_query += ' AND c.instructor_name = ?'
            params.append(instructor)
        
        search_query += ' ORDER BY c.created_at DESC'
        
        courses = conn.execute(search_query, params).fetchall()
        conn.close()
        
        # Convert to JSON-serializable format
        courses_data = []
        for course in courses:
            course_dict = dict(course)
            if course_dict.get('created_at'):
                course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d')
            courses_data.append(course_dict)
        
        return jsonify({
            'courses': courses_data,
            'count': len(courses_data)
        })
        
    except Exception as e:
        app.logger.error(f'Error searching courses: {str(e)}')
        return jsonify({'error': 'Search failed'}), 500


@app.route('/submissions')
@login_required
def submission_list():
    return render_template('submission_list.html')

# ===============================
# ADMIN ROUTES
# ===============================

@app.route('/admin')
def admin_login_page():
    """Admin login page - completely separate from main site"""
    if 'admin_id' in session:
        return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/admin/login', methods=['POST'])
def admin_login():
    """Handle admin login"""
    username = request.form['username']
    password = request.form['password']
    
    conn = get_db_connection()
    admin = conn.execute(
        'SELECT * FROM admin_users WHERE username = ?', (username,)
    ).fetchone()
    conn.close()
    
    if admin and check_password_hash(admin['password_hash'], password):
        session['admin_id'] = admin['id']
        session['admin_username'] = admin['username']
        flash('Login successful!', 'success')
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid username or password!', 'error')
        return redirect(url_for('admin_login_page'))

# Add this route to your app.py file in the ADMIN ROUTES section

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    """Enhanced admin dashboard with comprehensive statistics"""
    try:
        conn = get_db_connection()
        
        # Get comprehensive statistics
        stats = {}
        
        # Material statistics
        stats['total_materials'] = conn.execute('SELECT COUNT(*) FROM study_materials').fetchone()[0]
        stats['materials_by_level'] = {}
        for level in ['N5', 'N4', 'N3', 'N2', 'N1']:
            stats['materials_by_level'][level] = conn.execute(
                'SELECT COUNT(*) FROM study_materials WHERE jlpt_level = ?', (level,)
            ).fetchone()[0]
        
        stats['materials_by_category'] = {}
        for category in ['books', 'reviewers', 'audio']:
            stats['materials_by_category'][category] = conn.execute(
                'SELECT COUNT(*) FROM study_materials WHERE category = ?', (category,)
            ).fetchone()[0]
        
        # User statistics
        stats['total_users'] = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        stats['new_users_this_week'] = conn.execute(
            'SELECT COUNT(*) FROM users WHERE created_at > datetime("now", "-7 days")'
        ).fetchone()[0]
        
        # Course statistics
        stats['total_courses'] = conn.execute('SELECT COUNT(*) FROM courses').fetchone()[0]
        stats['active_courses'] = conn.execute(
            'SELECT COUNT(*) FROM courses WHERE status = "active"'
        ).fetchone()[0]
        
        # Enrollment statistics
        stats['total_enrollments'] = conn.execute('SELECT COUNT(*) FROM enrollments').fetchone()[0]
        stats['pending_enrollments'] = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE status = "pending"'
        ).fetchone()[0]
        stats['approved_enrollments'] = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE status = "approved"'
        ).fetchone()[0]
        
        # Storage statistics
        storage_result = conn.execute('SELECT SUM(file_size) FROM study_materials').fetchone()[0]
        stats['storage_used'] = round((storage_result or 0) / (1024 * 1024), 1)  # MB
        
        # Recent activity
        recent_materials = conn.execute('''
            SELECT original_filename, jlpt_level, category, upload_date
            FROM study_materials
            ORDER BY upload_date DESC
            LIMIT 5
        ''').fetchall()
        
        recent_users = conn.execute('''
            SELECT username, full_name, email, created_at
            FROM users
            ORDER BY created_at DESC
            LIMIT 5
        ''').fetchall()
        
        recent_enrollments = conn.execute('''
            SELECT u.username, u.full_name, c.title, e.enrolled_at, e.status
            FROM enrollments e
            JOIN users u ON e.user_id = u.id
            JOIN courses c ON e.course_id = c.id
            ORDER BY e.enrolled_at DESC
            LIMIT 5
        ''').fetchall()
        
        # Top courses by enrollment
        popular_courses = conn.execute('''
            SELECT c.title, c.jlpt_level, COUNT(e.id) as enrollment_count
            FROM courses c
            LEFT JOIN enrollments e ON c.id = e.course_id
            WHERE e.status = 'approved'
            GROUP BY c.id
            ORDER BY enrollment_count DESC
            LIMIT 5
        ''').fetchall()
        
        conn.close()
        
        return render_template('admin_dashboard.html', 
                             stats=stats,
                             recent_materials=recent_materials,
                             recent_users=recent_users,
                             recent_enrollments=recent_enrollments,
                             popular_courses=popular_courses)
                             
    except Exception as e:
        app.logger.error(f'Error loading admin dashboard: {str(e)}')
        flash('Error loading dashboard. Please try again.', 'error')
        return redirect(url_for('admin_login_page'))

@app.route('/admin/logout')
def admin_logout():
    """Admin logout"""
    session.pop('admin_id', None)
    session.pop('admin_username', None)
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin_login_page'))

# Enhanced dashboard route to show course recommendations
@app.route('/dashboard')
@login_required
def dashboard():
    """Enhanced user dashboard with course recommendations"""
    try:
        conn = get_db_connection()
        
        # Get user's enrolled courses with detailed status
        enrolled_courses_query = '''
            SELECT c.*, e.status as enrollment_status, e.enrolled_at, e.approved_at, e.notes,
                   (SELECT COUNT(*) FROM course_materials WHERE course_id = c.id) as material_count,
                   a.username as approved_by_name
            FROM courses c
            JOIN enrollments e ON c.id = e.course_id
            LEFT JOIN admin_users a ON e.approved_by = a.id
            WHERE e.user_id = ?
            ORDER BY e.enrolled_at DESC
        '''
        enrolled_courses = conn.execute(enrolled_courses_query, (session['user_id'],)).fetchall()
        
        # Get available courses (not enrolled) with recommendations
        available_courses_query = '''
            SELECT c.*, 
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as enrolled_count,
                   (SELECT username FROM admin_users WHERE id = c.created_by) as created_by_name
            FROM courses c
            WHERE c.status = 'active'
            AND c.id NOT IN (
                SELECT course_id FROM enrollments WHERE user_id = ?
            )
            ORDER BY c.created_at DESC
        '''
        available_courses = conn.execute(available_courses_query, (session['user_id'],)).fetchall()
        
        # Get pending enrollments count
        pending_count_query = '''
            SELECT COUNT(*) FROM enrollments 
            WHERE user_id = ? AND status = 'pending'
        '''
        pending_count = conn.execute(pending_count_query, (session['user_id'],)).fetchone()[0]
        
        # Convert data with proper datetime parsing
        enrolled_courses_data = []
        for course in enrolled_courses:
            course_dict = dict(course)
            if course_dict.get('enrolled_at'):
                course_dict['enrolled_at'] = parse_datetime(course_dict['enrolled_at']).strftime('%Y-%m-%d %H:%M:%S')
            if course_dict.get('approved_at'):
                course_dict['approved_at'] = parse_datetime(course_dict['approved_at']).strftime('%Y-%m-%d %H:%M:%S')
            enrolled_courses_data.append(course_dict)
        
        available_courses_data = []
        for course in available_courses:
            course_dict = dict(course)
            if course_dict.get('created_at'):
                course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            available_courses_data.append(course_dict)
        
        conn.close()
        
        return render_template('user_dashboard.html', 
                             enrolled_courses=enrolled_courses_data,
                             available_courses=available_courses_data,
                             pending_count=pending_count)
                             
    except Exception as e:
        app.logger.error(f'Error loading dashboard for user {session.get("user_id")}: {str(e)}')
        flash('Error loading dashboard. Please try again.', 'error')
        return redirect(url_for('index'))

@app.route('/admin/courses')
@admin_required
def admin_courses():
    """Enhanced admin courses management with comprehensive statistics"""
    try:
        conn = get_db_connection()
        
        courses = conn.execute('''
            SELECT c.*, 
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'approved') as enrolled_count,
                   (SELECT COUNT(*) FROM enrollments WHERE course_id = c.id AND status = 'pending') as pending_count,
                   (SELECT COUNT(*) FROM course_materials WHERE course_id = c.id) as material_count,
                   a.username as created_by_name
            FROM courses c
            LEFT JOIN admin_users a ON c.created_by = a.id
            ORDER BY c.created_at DESC
        ''').fetchall()
        
        # Convert to list of dicts for easier template handling
        courses_list = []
        for course in courses:
            course_dict = dict(course)
            if course_dict.get('created_at'):
                course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            if course_dict.get('updated_at'):
                course_dict['updated_at'] = parse_datetime(course_dict['updated_at']).strftime('%Y-%m-%d %H:%M:%S')
            courses_list.append(course_dict)
        
        conn.close()
        
        return render_template('admin_courses_list.html', courses=courses_list)
        
    except Exception as e:
        app.logger.error(f'Error loading courses for admin: {str(e)}')
        flash('Error loading courses. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/courses/create', methods=['GET', 'POST'])
@admin_required
def admin_create_course():
    """Create new course with enhanced validation"""
    if request.method == 'POST':
        try:
            title = request.form['title'].strip()
            description = request.form['description'].strip()
            jlpt_level = request.form['jlpt_level']
            instructor_name = request.form.get('instructor_name', '').strip()
            duration_weeks = request.form.get('duration_weeks', type=int)
            max_students = request.form.get('max_students', type=int)
            
            # Validation
            if not title or len(title) < 5:
                flash('Course title must be at least 5 characters long.', 'error')
                return redirect(url_for('admin_create_course'))
            
            if not description or len(description) < 20:
                flash('Course description must be at least 20 characters long.', 'error')
                return redirect(url_for('admin_create_course'))
            
            if jlpt_level not in ['N5', 'N4', 'N3', 'N2', 'N1']:
                flash('Invalid JLPT level selected.', 'error')
                return redirect(url_for('admin_create_course'))
            
            # Optional validation
            if duration_weeks and (duration_weeks < 1 or duration_weeks > 52):
                flash('Duration must be between 1 and 52 weeks.', 'error')
                return redirect(url_for('admin_create_course'))
            
            if max_students and max_students < 1:
                flash('Maximum students must be at least 1.', 'error')
                return redirect(url_for('admin_create_course'))
            
            conn = get_db_connection()
            
            # Check for duplicate titles
            existing = conn.execute(
                'SELECT id FROM courses WHERE title = ? AND jlpt_level = ?', 
                (title, jlpt_level)
            ).fetchone()
            
            if existing:
                flash(f'A course with this title already exists for {jlpt_level} level.', 'error')
                conn.close()
                return redirect(url_for('admin_create_course'))
            
            # Create course
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            course_id = conn.execute('''
                INSERT INTO courses (title, description, jlpt_level, instructor_name, 
                                   duration_weeks, max_students, status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ''', (title, description, jlpt_level, instructor_name or None, 
                  duration_weeks, max_students, session['admin_id'], current_time, current_time)).lastrowid
            
            conn.commit()
            conn.close()
            
            flash(f'✅ Course "{title}" created successfully!', 'success')
            return redirect(url_for('admin_course_detail', course_id=course_id))
            
        except Exception as e:
            app.logger.error(f'Error creating course: {str(e)}')
            flash('Error creating course. Please try again.', 'error')
            return redirect(url_for('admin_create_course'))
    
    return render_template('admin_create_course.html')

@app.route('/admin/courses/<int:course_id>')
@admin_required
def admin_course_detail(course_id):
    """Admin course detail and management"""
    conn = get_db_connection()
    
    # Get course details
    course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
    if not course:
        flash('Course not found!', 'error')
        return redirect(url_for('admin_courses'))
    
    # Get course materials
    materials = conn.execute('''
        SELECT sm.*, cm.order_index, cm.id as course_material_id
        FROM study_materials sm
        JOIN course_materials cm ON sm.id = cm.material_id
        WHERE cm.course_id = ?
        ORDER BY cm.order_index, sm.upload_date
    ''', (course_id,)).fetchall()
    
    # Get available materials to add
    available_materials = conn.execute('''
        SELECT * FROM study_materials 
        WHERE jlpt_level = ? 
        AND id NOT IN (
            SELECT material_id FROM course_materials WHERE course_id = ?
        )
        ORDER BY category, original_filename
    ''', (course['jlpt_level'], course_id)).fetchall()
    
    # Get enrollments
    enrollments = conn.execute('''
        SELECT e.*, u.username, u.full_name, u.email
        FROM enrollments e
        JOIN users u ON e.user_id = u.id
        WHERE e.course_id = ?
        ORDER BY e.enrolled_at DESC
    ''', (course_id,)).fetchall()
    
    conn.close()
    
    return render_template('admin_course_detail.html', 
                         course=course, 
                         materials=materials,
                         available_materials=available_materials,
                         enrollments=enrollments)

@app.route('/admin/courses/<int:course_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_course(course_id):
    """Edit course with enhanced data fetching"""
    if request.method == 'POST':
        return admin_update_course(course_id)
    
    try:
        conn = get_db_connection()
        
        # Get course details
        course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
        if not course:
            flash('Course not found!', 'error')
            conn.close()
            return redirect(url_for('admin_courses'))
        
        # Get additional statistics
        enrolled_count = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE course_id = ? AND status = "approved"', 
            (course_id,)
        ).fetchone()[0]
        
        pending_count = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE course_id = ? AND status = "pending"', 
            (course_id,)
        ).fetchone()[0]
        
        material_count = conn.execute(
            'SELECT COUNT(*) FROM course_materials WHERE course_id = ?', 
            (course_id,)
        ).fetchone()[0]
        
        conn.close()
        
        # Convert course to dict
        course_dict = dict(course)
        if course_dict.get('created_at'):
            course_dict['created_at'] = parse_datetime(course_dict['created_at']).strftime('%Y-%m-%d %H:%M:%S')
        if course_dict.get('updated_at'):
            course_dict['updated_at'] = parse_datetime(course_dict['updated_at']).strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template('admin_course_edit.html', 
                             course=course_dict,
                             enrolled_count=enrolled_count,
                             pending_count=pending_count,
                             material_count=material_count)
                             
    except Exception as e:
        app.logger.error(f'Error loading course {course_id} for editing: {str(e)}')
        flash('Error loading course. Please try again.', 'error')
        return redirect(url_for('admin_courses'))

@app.route('/admin/courses/<int:course_id>/update', methods=['POST'])
@admin_required
def admin_update_course(course_id):
    """Update course with enhanced validation and error handling"""
    try:
        title = request.form['title'].strip()
        description = request.form['description'].strip()
        jlpt_level = request.form['jlpt_level']
        instructor_name = request.form.get('instructor_name', '').strip()
        duration_weeks = request.form.get('duration_weeks', type=int)
        max_students = request.form.get('max_students', type=int)
        status = request.form['status']
        
        # Validation
        if not title or len(title) < 5:
            flash('Course title must be at least 5 characters long.', 'error')
            return redirect(url_for('admin_edit_course', course_id=course_id))
        
        if jlpt_level not in ['N5', 'N4', 'N3', 'N2', 'N1']:
            flash('Invalid JLPT level selected.', 'error')
            return redirect(url_for('admin_edit_course', course_id=course_id))
        
        if status not in ['active', 'inactive', 'draft']:
            flash('Invalid status selected.', 'error')
            return redirect(url_for('admin_edit_course', course_id=course_id))
        
        if duration_weeks and (duration_weeks < 1 or duration_weeks > 52):
            flash('Duration must be between 1 and 52 weeks.', 'error')
            return redirect(url_for('admin_edit_course', course_id=course_id))
        
        conn = get_db_connection()
        
        # Check if course exists
        course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
        if not course:
            flash('Course not found!', 'error')
            conn.close()
            return redirect(url_for('admin_courses'))
        
        # Check for duplicate titles (excluding current course)
        existing = conn.execute(
            'SELECT id FROM courses WHERE title = ? AND jlpt_level = ? AND id != ?', 
            (title, jlpt_level, course_id)
        ).fetchone()
        
        if existing:
            flash(f'Another course with this title already exists for {jlpt_level} level.', 'error')
            conn.close()
            return redirect(url_for('admin_edit_course', course_id=course_id))
        
        # Check if reducing max_students below current enrollment
        if max_students:
            current_enrolled = conn.execute(
                'SELECT COUNT(*) FROM enrollments WHERE course_id = ? AND status = "approved"', 
                (course_id,)
            ).fetchone()[0]
            
            if max_students < current_enrolled:
                flash(f'Warning: Maximum students set to {max_students}, but {current_enrolled} students are already enrolled.', 'warning')
        
        # Update course
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('''
            UPDATE courses 
            SET title = ?, description = ?, jlpt_level = ?, instructor_name = ?, 
                duration_weeks = ?, max_students = ?, status = ?, updated_at = ?
            WHERE id = ?
        ''', (title, description, jlpt_level, instructor_name or None, 
              duration_weeks, max_students, status, current_time, course_id))
        
        conn.commit()
        conn.close()
        
        flash(f'✅ Course "{title}" updated successfully!', 'success')
        return redirect(url_for('admin_course_detail', course_id=course_id))
        
    except Exception as e:
        app.logger.error(f'Error updating course {course_id}: {str(e)}')
        flash('Error updating course. Please try again.', 'error')
        return redirect(url_for('admin_edit_course', course_id=course_id))
    
@app.route('/admin/courses/<int:course_id>/delete', methods=['POST'])
@admin_required
def admin_delete_course(course_id):
    """Delete a course with comprehensive cleanup"""
    try:
        conn = get_db_connection()
        course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
        
        if not course:
            flash('Course not found!', 'error')
            conn.close()
            return redirect(url_for('admin_courses'))
        
        # Get counts for confirmation message
        enrolled_count = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE course_id = ?', (course_id,)
        ).fetchone()[0]
        
        material_count = conn.execute(
            'SELECT COUNT(*) FROM course_materials WHERE course_id = ?', (course_id,)
        ).fetchone()[0]
        
        # Delete course (cascade will handle related records)
        conn.execute('DELETE FROM courses WHERE id = ?', (course_id,))
        conn.commit()
        conn.close()
        
        flash(f'✅ Course "{course["title"]}" deleted successfully! Removed {enrolled_count} enrollments and {material_count} material assignments.', 'success')
        
    except Exception as e:
        app.logger.error(f'Error deleting course {course_id}: {str(e)}')
        flash('Error deleting course. Please try again.', 'error')
    
    return redirect(url_for('admin_courses'))

@app.route('/admin/courses/bulk-status', methods=['POST'])
@admin_required
def admin_bulk_status_change():
    """Bulk status change for courses"""
    try:
        data = request.get_json()
        course_ids = data.get('course_ids', [])
        new_status = data.get('status')
        
        if not course_ids or new_status not in ['active', 'inactive', 'draft']:
            return jsonify({'success': False, 'error': 'Invalid request data'}), 400
        
        conn = get_db_connection()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Update all selected courses
        for course_id in course_ids:
            conn.execute(
                'UPDATE courses SET status = ?, updated_at = ? WHERE id = ?',
                (new_status, current_time, course_id)
            )
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Updated {len(course_ids)} courses'})
        
    except Exception as e:
        app.logger.error(f'Error in bulk status change: {str(e)}')
        return jsonify({'success': False, 'error': 'Failed to update courses'}), 500
    
@app.route('/admin/courses/bulk-delete', methods=['POST'])
@admin_required
def admin_bulk_delete_courses():
    """Bulk delete courses"""
    try:
        data = request.get_json()
        course_ids = data.get('course_ids', [])
        
        if not course_ids:
            return jsonify({'success': False, 'error': 'No courses selected'}), 400
        
        conn = get_db_connection()
        
        # Delete all selected courses
        for course_id in course_ids:
            conn.execute('DELETE FROM courses WHERE id = ?', (course_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'Deleted {len(course_ids)} courses'})
        
    except Exception as e:
        app.logger.error(f'Error in bulk delete: {str(e)}')
        return jsonify({'success': False, 'error': 'Failed to delete courses'}), 500

@app.route('/admin/courses/<int:course_id>/add-material', methods=['POST'])
@admin_required
def admin_add_course_material(course_id):
    """Add material to course"""
    material_id = request.form['material_id']
    order_index = request.form.get('order_index', 0, type=int)
    
    conn = get_db_connection()
    
    # Check if material is already in course
    existing = conn.execute('''
        SELECT * FROM course_materials 
        WHERE course_id = ? AND material_id = ?
    ''', (course_id, material_id)).fetchone()
    
    if existing:
        flash('Material is already in this course!', 'error')
    else:
        conn.execute('''
            INSERT INTO course_materials (course_id, material_id, order_index)
            VALUES (?, ?, ?)
        ''', (course_id, material_id, order_index))
        conn.commit()
        flash('Material added to course successfully!', 'success')
    
    conn.close()
    return redirect(url_for('admin_course_detail', course_id=course_id))

@app.route('/admin/courses/<int:course_id>/remove-material/<int:course_material_id>', methods=['POST'])
@admin_required
def admin_remove_course_material(course_id, course_material_id):
    """Remove material from course"""
    conn = get_db_connection()
    conn.execute('DELETE FROM course_materials WHERE id = ?', (course_material_id,))
    conn.commit()
    conn.close()
    
    flash('Material removed from course successfully!', 'success')
    return redirect(url_for('admin_course_detail', course_id=course_id))

@app.route('/admin/enrollments')
@admin_required
def admin_enrollments():
    """Manage all enrollments with enhanced data fetching"""
    try:
        conn = get_db_connection()
        
        # Get all enrollments with comprehensive details
        enrollments_query = '''
            SELECT e.*, 
                   u.username, u.full_name, u.email, 
                   c.title as course_title, c.jlpt_level,
                   a.username as approved_by_name
            FROM enrollments e
            JOIN users u ON e.user_id = u.id
            JOIN courses c ON e.course_id = c.id
            LEFT JOIN admin_users a ON e.approved_by = a.id
            ORDER BY 
                CASE e.status 
                    WHEN 'pending' THEN 1 
                    WHEN 'approved' THEN 2 
                    WHEN 'rejected' THEN 3 
                END,
                e.enrolled_at DESC
        '''
        enrollments = conn.execute(enrollments_query).fetchall()
        
        # Convert to list of dicts for easier template handling
        enrollments_list = []
        for enrollment in enrollments:
            enrollment_dict = dict(enrollment)
            # Parse datetime fields safely
            if enrollment_dict.get('enrolled_at'):
                enrollment_dict['enrolled_at'] = parse_datetime(enrollment_dict['enrolled_at']).strftime('%Y-%m-%d %H:%M:%S')
            if enrollment_dict.get('approved_at'):
                enrollment_dict['approved_at'] = parse_datetime(enrollment_dict['approved_at']).strftime('%Y-%m-%d %H:%M:%S')
            enrollments_list.append(enrollment_dict)
        
        conn.close()
        
        return render_template('admin_enrollments.html', enrollments=enrollments_list)
        
    except Exception as e:
        app.logger.error(f'Error loading enrollments: {str(e)}')
        flash('Error loading enrollments. Please try again.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/enrollments/<int:enrollment_id>/approve', methods=['POST'])
@admin_required
def admin_approve_enrollment(enrollment_id):
    """Approve enrollment with enhanced error handling"""
    try:
        notes = request.form.get('notes', '').strip()
        
        conn = get_db_connection()
        
        # Check if enrollment exists and get all related info
        enrollment_query = '''
            SELECT e.*, c.max_students, c.title as course_title, u.username, u.full_name
            FROM enrollments e
            JOIN courses c ON e.course_id = c.id
            JOIN users u ON e.user_id = u.id
            WHERE e.id = ?
        '''
        enrollment = conn.execute(enrollment_query, (enrollment_id,)).fetchone()
        
        if not enrollment:
            flash('Enrollment request not found!', 'error')
            conn.close()
            return redirect(url_for('admin_enrollments'))
        
        if enrollment['status'] != 'pending':
            flash(f'Enrollment is already {enrollment["status"]}!', 'warning')
            conn.close()
            return redirect(url_for('admin_enrollments'))
        
        # Check if course is full (only if max_students is set)
        if enrollment['max_students']:
            current_enrolled = conn.execute('''
                SELECT COUNT(*) FROM enrollments 
                WHERE course_id = ? AND status = 'approved'
            ''', (enrollment['course_id'],)).fetchone()[0]
            
            if current_enrolled >= enrollment['max_students']:
                flash(f'Course "{enrollment["course_title"]}" is full! Cannot approve more enrollments.', 'error')
                conn.close()
                return redirect(url_for('admin_enrollments'))
        
        # Approve enrollment
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('''
            UPDATE enrollments 
            SET status = 'approved', approved_at = ?, approved_by = ?, notes = ?
            WHERE id = ?
        ''', (current_time, session['admin_id'], notes, enrollment_id))
        
        conn.commit()
        conn.close()
        
        student_name = enrollment['full_name'] or enrollment['username']
        flash(f'✅ Successfully approved enrollment for {student_name} in "{enrollment["course_title"]}"!', 'success')
        
        # Check if this is an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Enrollment approved successfully'})
        
        return redirect(url_for('admin_enrollments'))
        
    except Exception as e:
        # Log the error for debugging
        app.logger.error(f'Error approving enrollment {enrollment_id}: {str(e)}')
        flash('An error occurred while approving the enrollment. Please try again.', 'error')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'An error occurred'}), 500
        
        return redirect(url_for('admin_enrollments'))


@app.route('/admin/enrollments/<int:enrollment_id>/reject', methods=['POST'])
@admin_required
def admin_reject_enrollment(enrollment_id):
    """Reject enrollment with enhanced error handling"""
    try:
        notes = request.form.get('notes', '').strip()
        
        conn = get_db_connection()
        
        # Get enrollment info for the flash message
        enrollment_query = '''
            SELECT e.*, c.title as course_title, u.username, u.full_name
            FROM enrollments e
            JOIN courses c ON e.course_id = c.id
            JOIN users u ON e.user_id = u.id
            WHERE e.id = ?
        '''
        enrollment = conn.execute(enrollment_query, (enrollment_id,)).fetchone()
        
        if not enrollment:
            flash('Enrollment request not found!', 'error')
            conn.close()
            return redirect(url_for('admin_enrollments'))
        
        if enrollment['status'] != 'pending':
            flash(f'Enrollment is already {enrollment["status"]}!', 'warning')
            conn.close()
            return redirect(url_for('admin_enrollments'))
        
        # Reject enrollment
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('''
            UPDATE enrollments 
            SET status = 'rejected', approved_at = ?, approved_by = ?, notes = ?
            WHERE id = ?
        ''', (current_time, session['admin_id'], notes, enrollment_id))
        
        conn.commit()
        conn.close()
        
        student_name = enrollment['full_name'] or enrollment['username']
        reason_text = f' (Reason: {notes})' if notes else ''
        flash(f'❌ Rejected enrollment for {student_name} in "{enrollment["course_title"]}"{reason_text}', 'info')
        
        # Check if this is an AJAX request
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True, 'message': 'Enrollment rejected'})
        
        return redirect(url_for('admin_enrollments'))
        
    except Exception as e:
        # Log the error for debugging
        app.logger.error(f'Error rejecting enrollment {enrollment_id}: {str(e)}')
        flash('An error occurred while rejecting the enrollment. Please try again.', 'error')
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'An error occurred'}), 500
        
        return redirect(url_for('admin_enrollments'))

@app.route('/admin/upload')
@admin_required
def admin_upload_page():
    """Admin file upload page"""
    return render_template('admin_upload.html')

@app.route('/admin/upload', methods=['POST'])
@admin_required
def admin_upload_file():
    """Handle file upload by admin"""
    jlpt_level = request.form['jlpt_level']
    category = request.form['category']
    description = request.form.get('description', '')
    
    if 'file' not in request.files:
        flash('No file selected!', 'error')
        return redirect(url_for('admin_upload_page'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected!', 'error')
        return redirect(url_for('admin_upload_page'))
    
    if file and allowed_file(file.filename, category):
        # Generate unique filename
        original_filename = secure_filename(file.filename)
        file_extension = original_filename.rsplit('.', 1)[1].lower()
        unique_filename = f"{uuid.uuid4().hex}.{file_extension}"
        
        # Save file
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        
        # Get file size
        file_size = os.path.getsize(file_path)
        
        # Save to database
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO study_materials 
            (filename, original_filename, jlpt_level, category, file_size, file_type, uploaded_by, description, upload_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (unique_filename, original_filename, jlpt_level, category, file_size, 
              file_extension, session['admin_id'], description, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()
        
        flash(f'File "{original_filename}" uploaded successfully!', 'success')
        return redirect(url_for('admin_dashboard'))
    else:
        flash('Invalid file type for this category!', 'error')
        return redirect(url_for('admin_upload_page'))

@app.route('/admin/manage')
@admin_required
def admin_manage():
    """Admin material management page"""
    conn = get_db_connection()
    materials = conn.execute('''
        SELECT m.*, a.username as uploaded_by_name
        FROM study_materials m
        LEFT JOIN admin_users a ON m.uploaded_by = a.id
        ORDER BY m.upload_date DESC
    ''').fetchall()
    
    # Convert materials to list of dicts with proper datetime handling
    materials_list = []
    for material in materials:
        material_dict = dict(material)
        material_dict['upload_date'] = parse_datetime(material_dict['upload_date'])
        materials_list.append(material_dict)
    conn.close()
    
    return render_template('admin_manage.html', materials=materials_list)

@app.route('/admin/delete/<int:material_id>', methods=['POST'])
@admin_required
def admin_delete_material(material_id):
    """Delete a study material"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    
    if material:
        # Delete file from filesystem
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
        if os.path.exists(file_path):
            os.remove(file_path)
        
        # Delete from database
        conn.execute('DELETE FROM study_materials WHERE id = ?', (material_id,))
        conn.commit()
        flash('Material deleted successfully!', 'success')
    else:
        flash('Material not found!', 'error')
    
    conn.close()
    return redirect(url_for('admin_manage'))

@app.route('/admin/users')
@admin_required
def admin_users():
    """Admin users management page"""
    conn = get_db_connection()
    users = conn.execute('''
        SELECT id, username, email, full_name, role, created_at
        FROM users
        ORDER BY created_at DESC
    ''').fetchall()
    conn.close()
    
    return render_template('admin_users.html', users=users)

@app.route('/admin/settings')
@admin_required
def admin_settings():
    """Admin settings page"""
    return render_template('admin_settings.html')

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def admin_delete_user(user_id):
    """Delete a user account"""
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if user:
        # Don't allow deleting admin users
        if user['role'] == 'admin':
            flash('Cannot delete admin users!', 'error')
        else:
            conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
            conn.commit()
            flash(f'User "{user["username"]}" deleted successfully!', 'success')
    else:
        flash('User not found!', 'error')
    
    conn.close()
    return redirect(url_for('admin_users'))

# ===============================
# API ROUTES
# ===============================

@app.route('/api/admin/stats')
def get_admin_stats():
    """Get admin statistics with enrollment data"""
    if 'admin_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        conn = get_db_connection()
        
        # Total materials
        total_materials = conn.execute('SELECT COUNT(*) FROM study_materials').fetchone()[0]
        
        # Total users
        total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        
        # Total courses
        total_courses = conn.execute('SELECT COUNT(*) FROM courses').fetchone()[0]
        
        # Pending enrollments
        pending_enrollments = conn.execute(
            'SELECT COUNT(*) FROM enrollments WHERE status = "pending"'
        ).fetchone()[0]
        
        # Recent uploads (last 7 days)
        from datetime import timedelta
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        recent_uploads = conn.execute(
            'SELECT COUNT(*) FROM study_materials WHERE upload_date > ?', (week_ago,)
        ).fetchone()[0]
        
        # Storage used (in MB)
        storage_result = conn.execute('SELECT SUM(file_size) FROM study_materials').fetchone()[0]
        storage_used = round((storage_result or 0) / (1024 * 1024), 1)
        
        conn.close()
        
        return jsonify({
            'total_materials': total_materials,
            'total_users': total_users,
            'total_courses': total_courses,
            'pending_enrollments': pending_enrollments,
            'recent_uploads': recent_uploads,
            'storage_used': storage_used
        })
        
    except Exception as e:
        app.logger.error(f'Error getting admin stats: {str(e)}')
        return jsonify({'error': 'Failed to load statistics'}), 500


# Additional utility function for better datetime handling
def parse_datetime(date_string):
    """Parse datetime string safely with multiple format support"""
    if not date_string:
        return datetime.now()
    
    if isinstance(date_string, datetime):
        return date_string
    
    try:
        # Try different datetime formats
        formats = [
            '%Y-%m-%d %H:%M:%S.%f',
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d',
            '%m/%d/%Y %H:%M:%S',
            '%m/%d/%Y'
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(str(date_string), fmt)
            except ValueError:
                continue
                
        # If all formats fail, try to parse as ISO format
        from dateutil import parser
        return parser.parse(str(date_string))
        
    except Exception as e:
        app.logger.warning(f'Could not parse datetime {date_string}: {str(e)}')
        return datetime.now()

@app.route('/api/admin/system-info')
def get_system_info():
    """Get system information"""
    if 'admin_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        # Get database file size
        db_size = 0
        if os.path.exists('jlpt_study.db'):
            db_size = round(os.path.getsize('jlpt_study.db') / 1024, 2)  # KB
        
        # Get upload directory size
        upload_size = 0
        if os.path.exists(app.config['UPLOAD_FOLDER']):
            for dirpath, dirnames, filenames in os.walk(app.config['UPLOAD_FOLDER']):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    upload_size += os.path.getsize(filepath)
            upload_size = round(upload_size / (1024 * 1024), 2)  # MB
        
        return jsonify({
            'database_size': f'{db_size} KB',
            'upload_size': f'{upload_size} MB',
            'server_status': 'online',
            'last_backup': 'never'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pdf-info/<int:material_id>')
def get_pdf_info(material_id):
    """Get basic PDF information without loading pages"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    conn.close()
    
    if not material or material['file_type'].lower() != 'pdf':
        return jsonify({'error': 'PDF not found'}), 404
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        # Open PDF with PyMuPDF to get basic info only
        doc = fitz.open(file_path)
        page_count = len(doc)
        doc.close()
        
        return jsonify({
            'total_pages': page_count,
            'filename': material['original_filename'],
            'file_size': material['file_size']
        })
        
    except Exception as e:
        return jsonify({'error': 'Failed to read PDF info'}), 500

@app.route('/api/pdf-page/<int:material_id>/<int:page_num>')
def get_pdf_page(material_id, page_num):
    """Get a single PDF page as image with caching"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    conn.close()
    
    if not material or material['file_type'].lower() != 'pdf':
        return jsonify({'error': 'PDF not found'}), 404
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Get quality parameter (default to medium)
    quality = request.args.get('quality', 'medium')
    
    # Set zoom based on quality
    zoom_levels = {
        'low': 1.0,
        'medium': 1.5,
        'high': 2.0
    }
    zoom = zoom_levels.get(quality, 1.5)
    
    try:
        # Open PDF with PyMuPDF
        doc = fitz.open(file_path)
        
        # Check if page number is valid
        if page_num < 1 or page_num > len(doc):
            doc.close()
            return jsonify({'error': 'Invalid page number'}), 400
        
        page = doc.load_page(page_num - 1)  # PyMuPDF uses 0-based indexing
        
        # Render page as image
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        img_data = pix.tobytes("png")
        
        doc.close()
        
        # Return image directly as response
        return Response(img_data, mimetype='image/png', headers={
            'Cache-Control': 'public, max-age=3600',  # Cache for 1 hour
            'Content-Length': str(len(img_data))
        })
        
    except Exception as e:
        return jsonify({'error': 'Failed to render PDF page'}), 500

@app.route('/api/pdf-pages-batch/<int:material_id>')
def get_pdf_pages_batch(material_id):
    """Get multiple PDF pages in a single request"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    conn.close()
    
    if not material or material['file_type'].lower() != 'pdf':
        return jsonify({'error': 'PDF not found'}), 404
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
    
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    
    # Get parameters
    start_page = int(request.args.get('start', 1))
    count = min(int(request.args.get('count', 5)), 10)  # Max 10 pages per batch
    quality = request.args.get('quality', 'medium')
    
    # Set zoom based on quality
    zoom_levels = {
        'low': 1.0,
        'medium': 1.5,
        'high': 2.0
    }
    zoom = zoom_levels.get(quality, 1.5)
    
    try:
        # Open PDF with PyMuPDF
        doc = fitz.open(file_path)
        total_pages = len(doc)
        
        pages = []
        end_page = min(start_page + count - 1, total_pages)
        
        for page_num in range(start_page, end_page + 1):
            if page_num <= total_pages:
                page = doc.load_page(page_num - 1)
                # Use lower quality for batch loading to improve speed
                mat = fitz.Matrix(zoom * 0.8, zoom * 0.8)  # Slightly reduce quality for batch
                pix = page.get_pixmap(matrix=mat)
                img_data = pix.tobytes("png")
                
                # Convert to base64 for JSON response
                img_base64 = base64.b64encode(img_data).decode()
                pages.append({
                    'page_number': page_num,
                    'image': f"data:image/png;base64,{img_base64}"
                })
        
        doc.close()
        
        return jsonify({
            'pages': pages,
            'start_page': start_page,
            'end_page': end_page,
            'total_pages': total_pages,
            'has_more': end_page < total_pages
        })
        
    except Exception as e:
        return jsonify({'error': 'Failed to process PDF batch'}), 500

@app.route('/api/materials/<jlpt_level>')
def get_materials(jlpt_level):
    """API endpoint to get materials for a specific JLPT level"""
    conn = get_db_connection()
    materials = conn.execute('''
        SELECT m.*, a.username as uploaded_by_name
        FROM study_materials m
        LEFT JOIN admin_users a ON m.uploaded_by = a.id
        WHERE m.jlpt_level = ?
        ORDER BY m.upload_date DESC
    ''', (jlpt_level,)).fetchall()
    conn.close()
    
    # Group materials by category
    result = {
        'books': [],
        'reviewers': [],
        'audio': []
    }
    
    for material in materials:
        result[material['category']].append({
            'id': material['id'],
            'filename': material['original_filename'],
            'description': material['description'],
            'file_size': material['file_size'],
            'upload_date': material['upload_date'],
            'uploaded_by': material['uploaded_by_name']
        })
    
    return jsonify(result)

@app.route('/api/download/<int:material_id>')
def download_material(material_id):
    """Download a study material"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    conn.close()
    
    if material:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
        if os.path.exists(file_path):
            return send_file(file_path, as_attachment=True, 
                           download_name=material['original_filename'])
    
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/view/<int:material_id>')
def view_material(material_id):
    """View a study material in browser"""
    conn = get_db_connection()
    material = conn.execute(
        'SELECT * FROM study_materials WHERE id = ?', (material_id,)
    ).fetchone()
    conn.close()
    
    if material:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], material['filename'])
        if os.path.exists(file_path):
            # For PDFs and text files, display inline
            file_extension = material['original_filename'].rsplit('.', 1)[1].lower()
            if file_extension in ['pdf', 'txt']:
                return send_file(file_path, as_attachment=False)
            else:
                # For other files, still download
                return send_file(file_path, as_attachment=True, 
                               download_name=material['original_filename'])
    
    return jsonify({'error': 'File not found'}), 404

# ===============================
# ERROR HANDLERS
# ===============================

@app.errorhandler(413)
def too_large(e):
    return "File is too large", 413

if __name__ == '__main__':
    init_db()
    app.run(debug=True)