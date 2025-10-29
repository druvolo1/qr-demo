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
PRODUCTS_FILE = 'data/products.json'
REQUESTS_FILE = 'data/requests.json'

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

# Background thread to check for expired requests
def cleanup_expired_requests():
    while True:
        time.sleep(5)  # Check every 5 seconds
        requests = load_requests()
        current_time = datetime.now()

        # Filter out expired requests
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

        # Save and emit if any expired
        if expired_count > 0:
            save_requests(active_requests)
            socketio.emit('requests_updated', {'requests': active_requests})

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

# WebSocket events
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    # Send current requests to newly connected client
    requests = load_requests()
    emit('requests_updated', {'requests': requests})

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
