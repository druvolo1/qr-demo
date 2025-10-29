from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO, emit
import qrcode
import json
import os
import io
import threading
import time
from datetime import datetime, timedelta
import base64
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
socketio = SocketIO(app, cors_allowed_origins="*")

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

# File paths for flat file storage
PRODUCTS_FILE = 'data/products.json'  # Shoe try-on products
REQUESTS_FILE = 'data/requests.json'  # Shoe try-on requests
CATALOG_FILE = 'data/catalog.json'    # Product catalog for help system
HELP_REQUESTS_FILE = 'data/help_requests.json'  # Help/associate requests

# Initialize data files and folders if they don't exist
def init_data_files():
    if not os.path.exists('data'):
        os.makedirs('data')
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    if not os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'w') as f:
            json.dump([], f)
    if not os.path.exists(REQUESTS_FILE):
        with open(REQUESTS_FILE, 'w') as f:
            json.dump([], f)
    if not os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, 'w') as f:
            json.dump([], f)
    if not os.path.exists(HELP_REQUESTS_FILE):
        with open(HELP_REQUESTS_FILE, 'w') as f:
            json.dump([], f)

init_data_files()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def crop_and_resize_image(image_path, output_path, size=(300, 300)):
    """Crop image to center square and resize, respecting EXIF orientation."""
    try:
        # Open the image
        img = Image.open(image_path)

        # Fix orientation based on EXIF data (handles portrait/landscape rotation)
        img = ImageOps.exif_transpose(img)

        # Convert to RGB if necessary (handles RGBA, etc.)
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Get dimensions
        width, height = img.size

        # Calculate center crop to square
        min_dimension = min(width, height)
        left = (width - min_dimension) // 2
        top = (height - min_dimension) // 2
        right = left + min_dimension
        bottom = top + min_dimension

        # Crop to center square
        img_cropped = img.crop((left, top, right, bottom))

        # Resize to target size
        img_resized = img_cropped.resize(size, Image.Resampling.LANCZOS)

        # Save the result
        img_resized.save(output_path, 'JPEG', quality=85)
        return True

    except Exception as e:
        print(f"Error processing image: {e}")
        return False

# Helper functions for data management
def load_products():
    with open(PRODUCTS_FILE, 'r') as f:
        return json.load(f)

def save_products(products):
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(products, f, indent=2)

def load_requests():
    with open(REQUESTS_FILE, 'r') as f:
        return json.load(f)

def save_requests(requests):
    with open(REQUESTS_FILE, 'w') as f:
        json.dump(requests, f, indent=2)

def load_catalog():
    with open(CATALOG_FILE, 'r') as f:
        return json.load(f)

def save_catalog(catalog):
    with open(CATALOG_FILE, 'w') as f:
        json.dump(catalog, f, indent=2)

def load_help_requests():
    with open(HELP_REQUESTS_FILE, 'r') as f:
        return json.load(f)

def save_help_requests(help_requests):
    with open(HELP_REQUESTS_FILE, 'w') as f:
        json.dump(help_requests, f, indent=2)

# Background thread to check for expired requests
def cleanup_expired_requests():
    while True:
        time.sleep(5)  # Check every 5 seconds
        current_time = datetime.now()

        # Clean up shoe try-on requests
        requests = load_requests()
        active_requests = []
        expired_count = 0

        for req in requests:
            created_at = datetime.fromisoformat(req['created_at'])
            timeout_minutes = req.get('timeout_minutes', 30)
            expiry_time = created_at + timedelta(minutes=timeout_minutes)

            if current_time < expiry_time:
                active_requests.append(req)
            else:
                # Delete associated selfie file
                if req.get('selfie'):
                    selfie_path = os.path.join(app.config['UPLOAD_FOLDER'], req['selfie'])
                    if os.path.exists(selfie_path):
                        try:
                            os.remove(selfie_path)
                        except Exception as e:
                            print(f"Error deleting selfie: {e}")
                expired_count += 1

        if expired_count > 0:
            save_requests(active_requests)
            socketio.emit('requests_updated', {'requests': active_requests})

        # Clean up help requests
        help_requests = load_help_requests()
        active_help_requests = []
        expired_help_count = 0

        for req in help_requests:
            created_at = datetime.fromisoformat(req['created_at'])
            timeout_minutes = req.get('timeout_minutes', 30)
            expiry_time = created_at + timedelta(minutes=timeout_minutes)

            if current_time < expiry_time:
                active_help_requests.append(req)
            else:
                # Delete associated selfie file
                if req.get('selfie'):
                    selfie_path = os.path.join(app.config['UPLOAD_FOLDER'], req['selfie'])
                    if os.path.exists(selfie_path):
                        try:
                            os.remove(selfie_path)
                        except Exception as e:
                            print(f"Error deleting selfie: {e}")
                expired_help_count += 1

        if expired_help_count > 0:
            save_help_requests(active_help_requests)
            socketio.emit('help_requests_updated', {'requests': active_help_requests})

# Start cleanup thread
cleanup_thread = threading.Thread(target=cleanup_expired_requests, daemon=True)
cleanup_thread.start()

# Routes
@app.route('/')
def index():
    """Management portal"""
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    """Dashboard to view all requests"""
    return render_template('dashboard.html')

@app.route('/api/products', methods=['GET'])
def get_products():
    """Get all products"""
    products = load_products()
    return jsonify(products)

@app.route('/api/products', methods=['POST'])
def add_product():
    """Add a new product"""
    data = request.json
    products = load_products()

    # Generate unique ID
    product_id = str(int(time.time() * 1000))

    product = {
        'id': product_id,
        'name': data['name'],
        'message': data['message'],
        'qr_size_type': data.get('qr_size_type', 'percentage'),  # 'percentage' or 'pixels'
        'qr_size_value': data.get('qr_size_value', 50),  # 50% or 500px
        'qr_offset_x': data.get('qr_offset_x', 0),
        'qr_offset_y': data.get('qr_offset_y', 0),
        'timeout_minutes': data.get('timeout_minutes', 30),
        'show_product_info': data.get('show_product_info', True),
        'created_at': datetime.now().isoformat()
    }

    products.append(product)
    save_products(products)

    return jsonify(product), 201

@app.route('/api/products/<product_id>', methods=['PUT'])
def update_product(product_id):
    """Update a product"""
    data = request.json
    products = load_products()

    for i, product in enumerate(products):
        if product['id'] == product_id:
            products[i].update({
                'name': data['name'],
                'message': data['message'],
                'qr_size_type': data.get('qr_size_type', 'percentage'),
                'qr_size_value': data.get('qr_size_value', 50),
                'qr_offset_x': data.get('qr_offset_x', 0),
                'qr_offset_y': data.get('qr_offset_y', 0),
                'timeout_minutes': data.get('timeout_minutes', 30),
                'show_product_info': data.get('show_product_info', True)
            })
            save_products(products)
            return jsonify(products[i])

    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/products/<product_id>', methods=['DELETE'])
def delete_product(product_id):
    """Delete a product"""
    products = load_products()
    products = [p for p in products if p['id'] != product_id]
    save_products(products)
    return '', 204

@app.route('/qr/<product_id>')
def qr_display(product_id):
    """Display QR code for a product"""
    products = load_products()
    product = next((p for p in products if p['id'] == product_id), None)

    if not product:
        return "Product not found", 404

    return render_template('qr_display.html', product=product)

@app.route('/api/qr/<product_id>')
def generate_qr(product_id):
    """Generate QR code image"""
    # Get the base URL from request
    base_url = request.url_root
    form_url = f"{base_url}form/{product_id}"

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(form_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to bytes
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)

    return send_file(img_io, mimetype='image/png')

@app.route('/form/<product_id>')
def product_form(product_id):
    """Display form for product request"""
    products = load_products()
    product = next((p for p in products if p['id'] == product_id), None)

    if not product:
        return "Product not found", 404

    return render_template('product_form.html', product=product)

@app.route('/api/requests', methods=['POST'])
def submit_request():
    """Submit a new request"""
    # Handle form data instead of JSON due to file upload
    product_id = request.form.get('product_id')
    size = request.form.get('size')
    name = request.form.get('name')

    requests_list = load_requests()

    # Get product to fetch timeout setting
    products = load_products()
    product = next((p for p in products if p['id'] == product_id), None)

    if not product:
        return jsonify({'error': 'Product not found'}), 404

    # Generate unique ID
    request_id = str(int(time.time() * 1000))

    # Handle selfie upload
    selfie_filename = None
    if 'selfie' in request.files:
        file = request.files['selfie']
        if file and file.filename != '' and allowed_file(file.filename):
            # Generate unique filename
            ext = file.filename.rsplit('.', 1)[1].lower()
            original_filename = f"{request_id}_original.{ext}"
            cropped_filename = f"{request_id}.{ext}"

            original_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
            cropped_path = os.path.join(app.config['UPLOAD_FOLDER'], cropped_filename)

            # Save original file
            file.save(original_path)

            # Crop and resize image
            if crop_and_resize_image(original_path, cropped_path):
                selfie_filename = cropped_filename
                # Delete original after cropping
                os.remove(original_path)
            else:
                # If cropping fails, use original
                os.rename(original_path, cropped_path)
                selfie_filename = cropped_filename

    new_request = {
        'id': request_id,
        'product_id': product_id,
        'product_name': product['name'],
        'name': name,
        'size': size,
        'selfie': selfie_filename,
        'created_at': datetime.now().isoformat(),
        'timeout_minutes': product['timeout_minutes']
    }

    requests_list.append(new_request)
    save_requests(requests_list)

    # Emit to all connected clients
    socketio.emit('requests_updated', {'requests': requests_list})

    return jsonify(new_request), 201

@app.route('/api/requests', methods=['GET'])
def get_requests():
    """Get all active requests"""
    requests = load_requests()
    return jsonify(requests)

@app.route('/api/requests/<request_id>', methods=['DELETE'])
def delete_request(request_id):
    """Manually delete a request"""
    requests = load_requests()

    # Find and delete associated selfie file
    for req in requests:
        if req['id'] == request_id and req.get('selfie'):
            selfie_path = os.path.join(app.config['UPLOAD_FOLDER'], req['selfie'])
            if os.path.exists(selfie_path):
                os.remove(selfie_path)

    requests = [r for r in requests if r['id'] != request_id]
    save_requests(requests)

    # Emit to all connected clients
    socketio.emit('requests_updated', {'requests': requests})

    return '', 204

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded selfie images"""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))

# ===== PRODUCT CATALOG & HELP SYSTEM ROUTES =====

@app.route('/catalog')
def catalog_management():
    """Product catalog management page"""
    return render_template('products_catalog.html')

@app.route('/api/catalog', methods=['GET'])
def get_catalog():
    """Get all catalog products"""
    catalog = load_catalog()
    return jsonify(catalog)

@app.route('/api/catalog', methods=['POST'])
def add_catalog_product():
    """Add a new catalog product"""
    data = request.json
    catalog = load_catalog()

    product_id = str(int(time.time() * 1000))

    product = {
        'id': product_id,
        'barcode': data['barcode'],
        'brand': data['brand'],
        'description': data['description'],
        'price': data['price'],
        'inventory': data['inventory'],
        'created_at': datetime.now().isoformat()
    }

    catalog.append(product)
    save_catalog(catalog)

    return jsonify(product), 201

@app.route('/api/catalog/<product_id>', methods=['PUT'])
def update_catalog_product(product_id):
    """Update a catalog product"""
    data = request.json
    catalog = load_catalog()

    for i, product in enumerate(catalog):
        if product['id'] == product_id:
            catalog[i].update({
                'barcode': data['barcode'],
                'brand': data['brand'],
                'description': data['description'],
                'price': data['price'],
                'inventory': data['inventory']
            })
            save_catalog(catalog)
            return jsonify(catalog[i])

    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/catalog/<product_id>', methods=['DELETE'])
def delete_catalog_product(product_id):
    """Delete a catalog product"""
    catalog = load_catalog()
    catalog = [p for p in catalog if p['id'] != product_id]
    save_catalog(catalog)
    return '', 204

@app.route('/api/catalog/barcode/<barcode>', methods=['GET'])
def get_product_by_barcode(barcode):
    """Get product by barcode"""
    catalog = load_catalog()
    product = next((p for p in catalog if p['barcode'] == barcode), None)

    if product:
        return jsonify(product)
    else:
        return jsonify({'error': 'Product not found'}), 404

@app.route('/help-qr')
def help_qr():
    """Display help/associate request QR code"""
    return render_template('help_qr.html')

@app.route('/api/help-qr')
def generate_help_qr():
    """Generate QR code for help system"""
    base_url = request.url_root
    form_url = f"{base_url}help"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(form_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)

    return send_file(img_io, mimetype='image/png')

@app.route('/help')
def help_form():
    """Help request form"""
    return render_template('help_form.html')

@app.route('/help-dashboard')
def help_dashboard():
    """Help requests dashboard"""
    return render_template('help_dashboard.html')

@app.route('/api/help-requests', methods=['POST'])
def submit_help_request():
    """Submit a new help request"""
    request_type = request.form.get('request_type')  # 'associate' or 'product'
    name = request.form.get('name')
    barcode = request.form.get('barcode', '')

    help_requests = load_help_requests()
    request_id = str(int(time.time() * 1000))

    # Handle selfie upload
    selfie_filename = None
    if 'selfie' in request.files:
        file = request.files['selfie']
        if file and file.filename != '' and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            original_filename = f"{request_id}_original.{ext}"
            cropped_filename = f"{request_id}.{ext}"

            original_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
            cropped_path = os.path.join(app.config['UPLOAD_FOLDER'], cropped_filename)

            file.save(original_path)

            if crop_and_resize_image(original_path, cropped_path):
                selfie_filename = cropped_filename
                os.remove(original_path)
            else:
                os.rename(original_path, cropped_path)
                selfie_filename = cropped_filename

    new_request = {
        'id': request_id,
        'request_type': request_type,
        'name': name,
        'barcode': barcode,
        'product_info': None,
        'selfie': selfie_filename,
        'created_at': datetime.now().isoformat(),
        'timeout_minutes': 30
    }

    # If barcode provided, fetch product info
    if barcode:
        catalog = load_catalog()
        product = next((p for p in catalog if p['barcode'] == barcode), None)
        if product:
            new_request['product_info'] = {
                'brand': product['brand'],
                'description': product['description'],
                'price': product['price'],
                'inventory': product['inventory']
            }

    help_requests.append(new_request)
    save_help_requests(help_requests)

    socketio.emit('help_requests_updated', {'requests': help_requests})

    return jsonify(new_request), 201

@app.route('/api/help-requests', methods=['GET'])
def get_help_requests():
    """Get all help requests"""
    help_requests = load_help_requests()
    return jsonify(help_requests)

@app.route('/api/help-requests/<request_id>', methods=['DELETE'])
def delete_help_request(request_id):
    """Manually delete a help request"""
    help_requests = load_help_requests()

    # Find and delete associated selfie file
    for req in help_requests:
        if req['id'] == request_id and req.get('selfie'):
            selfie_path = os.path.join(app.config['UPLOAD_FOLDER'], req['selfie'])
            if os.path.exists(selfie_path):
                os.remove(selfie_path)

    help_requests = [r for r in help_requests if r['id'] != request_id]
    save_help_requests(help_requests)

    socketio.emit('help_requests_updated', {'requests': help_requests})

    return '', 204

# WebSocket events
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    # Send current requests to newly connected client
    requests = load_requests()
    help_requests = load_help_requests()
    emit('requests_updated', {'requests': requests})
    emit('help_requests_updated', {'requests': help_requests})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
