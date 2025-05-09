import os
import uuid
import time
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, send_file, jsonify
import spleeter
from spleeter.separator import Separator

app = Flask(__name__)

# Constants
UPLOAD_FOLDER = '/tmp/uploads'
OUTPUT_FOLDER = '/tmp/outputs'
USE_GCS = os.environ.get('USE_GCS', 'false').lower() == 'true'
BUCKET_NAME = os.environ.get('GCS_BUCKET_NAME')
ALLOWED_EXTENSIONS = {'mp3', 'wav', 'flac', 'ogg', 'm4a', 'wma'}

# Create directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# Initialize GCS client if needed
storage_client = None
bucket = None
if USE_GCS:
    try:
        from google.cloud import storage
        if not BUCKET_NAME:
            print("Warning: GCS_BUCKET_NAME environment variable not set. GCS uploads will be disabled.")
            USE_GCS = False
        else:
            storage_client = storage.Client()
            bucket = storage_client.bucket(BUCKET_NAME)
    except ImportError:
        print("Warning: google-cloud-storage not installed. GCS uploads will be disabled.")
        USE_GCS = False

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if file and allowed_file(file.filename):
        # Generate unique ID for this separation job
        job_id = str(uuid.uuid4())
        
        # Create job directory
        job_dir = os.path.join(UPLOAD_FOLDER, job_id)
        os.makedirs(job_dir, exist_ok=True)
        
        # Save the uploaded file
        file_path = os.path.join(job_dir, file.filename)
        file.save(file_path)
        
        # Create output directory
        output_path = os.path.join(OUTPUT_FOLDER, job_id)
        os.makedirs(output_path, exist_ok=True)
        
        # Start separation in a background thread
        stems = request.form.get('stems', '2stems')
        separation_thread = SeparationThread(job_id, file_path, output_path, stems)
        separation_thread.start()
        
        return jsonify({
            'job_id': job_id,
            'status': 'processing'
        })
    
    return jsonify({'error': 'Invalid file type'}), 400

@app.route('/status/<job_id>')
def check_status(job_id):
    # Check if output exists
    output_path = os.path.join(OUTPUT_FOLDER, job_id)
    if not os.path.exists(output_path):
        return jsonify({'status': 'not_found'}), 404
    
    # Check if separation is complete
    status_file = os.path.join(output_path, 'status.txt')
    if os.path.exists(status_file):
        with open(status_file, 'r') as f:
            status = f.read().strip()
        
        if status == 'completed':
            # Get list of output files
            files = []
            for item in os.listdir(output_path):
                if os.path.isdir(os.path.join(output_path, item)):
                    for file in os.listdir(os.path.join(output_path, item)):
                        if file.endswith(('.wav', '.mp3')):
                            files.append(f"{item}/{file}")
            
            return jsonify({
                'status': 'completed',
                'files': files
            })
        elif status == 'error':
            error_details = "Unknown error occurred"
            error_file = os.path.join(output_path, 'error_details.txt')
            if os.path.exists(error_file):
                with open(error_file, 'r') as f:
                    error_details = f.read()
            
            return jsonify({
                'status': 'error',
                'error_details': error_details
            }), 500
    
    return jsonify({'status': 'processing'})

@app.route('/download/<job_id>/<path:filename>')
def download_file(job_id, filename):
    file_path = os.path.join(OUTPUT_FOLDER, job_id, filename)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

# Background processing thread
import threading

class SeparationThread(threading.Thread):
    def __init__(self, job_id, input_file, output_path, stems='2stems'):
        threading.Thread.__init__(self)
        self.job_id = job_id
        self.input_file = input_file
        self.output_path = output_path
        self.stems = stems
    
    def run(self):
        try:
            # Mark as processing
            with open(os.path.join(self.output_path, 'status.txt'), 'w') as f:
                f.write('processing')
            
            # Initialize separator
            separator = Separator(f'spleeter:{self.stems}')
            
            # Perform separation
            separator.separate_to_file(
                self.input_file, 
                self.output_path,
                codec='mp3'
            )
            
            # Upload results to GCS if enabled
            if USE_GCS and storage_client and bucket:
                input_filename = os.path.basename(self.input_file)
                base_filename = os.path.splitext(input_filename)[0]
                
                for root, dirs, files in os.walk(os.path.join(self.output_path, base_filename)):
                    for file in files:
                        if file.endswith(('.mp3', '.wav')):
                            local_path = os.path.join(root, file)
                            relative_path = os.path.relpath(local_path, self.output_path)
                            gcs_path = f"stems/{self.job_id}/{relative_path}"
                            
                            blob = bucket.blob(gcs_path)
                            blob.upload_from_filename(local_path)
            
            # Mark as completed
            with open(os.path.join(self.output_path, 'status.txt'), 'w') as f:
                f.write('completed')
                
        except Exception as e:
            error_msg = f"Error processing job {self.job_id}: {str(e)}"
            print(error_msg)
            
            # Log detailed error information to a file for debugging
            error_file_path = os.path.join(self.output_path, 'error_details.txt')
            with open(error_file_path, 'w') as f:
                import traceback
                f.write(f"Error processing audio file: {self.input_file}\n")
                f.write(f"Error message: {str(e)}\n\n")
                f.write("Traceback:\n")
                f.write(traceback.format_exc())
            
            # Mark as error
            with open(os.path.join(self.output_path, 'status.txt'), 'w') as f:
                f.write('error')

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080))) 