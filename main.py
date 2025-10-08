import os
import time
import shutil
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, flash, jsonify
import psutil
import subprocess

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# 配置
UPLOAD_FOLDER = '/path/to/your/bomb/disk'  # 更改为你的炸弹盘路径
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'zip', 'doc', 'docx', 'xls', 'xlsx', 'mp3', 'mp4'}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
CLEANUP_INTERVAL = 3600  # 清理间隔(秒)
FILE_LIFETIME = 3 * 24 * 3600  # 文件保留时间(3天)

# 确保上传目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_disk_usage():
    try:
        usage = shutil.disk_usage(UPLOAD_FOLDER)
        return {
            'total': usage.total,
            'used': usage.used,
            'free': usage.free,
            'percent_used': round(usage.used / usage.total * 100, 1)
        }
    except Exception:
        return {'total': 0, 'used': 0, 'free': 0, 'percent_used': 0}


def get_smart_data():
    """获取硬盘真实SMART数据"""
    try:
        # 尝试执行smartctl命令
        result = subprocess.run(
            ['smartctl', '-a', '/dev/sda'],  # 根据您的硬盘设备路径修改
            capture_output=True,
            text=True,
            check=True
        )
        output = result.stdout
        
        # 解析SMART数据
        data = {
            'bad_blocks': 'NaN',
            'reallocated_sectors': 'NaN',
            'uncorrectable_errors': 'NaN',
            'temperature': 'NaN',
            'power_on_hours': 'NaN',
            'health_status': 'UNKNOWN'
        }
        
        # 解析关键指标
        for line in output.split('\n'):
            if 'Reallocated_Sector_Ct' in line:
                data['reallocated_sectors'] = line.split()[9]
            elif 'Current_Pending_Sector' in line:
                data['bad_blocks'] = line.split()[9]
            elif 'Offline_Uncorrectable' in line:
                data['uncorrectable_errors'] = line.split()[9]
            elif 'Temperature_Celsius' in line:
                data['temperature'] = line.split()[9]
            elif 'Power_On_Hours' in line:
                data['power_on_hours'] = line.split()[9]
            elif 'SMART overall-health self-assessment test result' in line:
                data['health_status'] = line.split(':')[1].strip()
        
        return data
    
    except FileNotFoundError:
        # smartctl未安装
        print("错误: smartctl工具未安装，无法获取硬盘SMART数据")
        return {
            'bad_blocks': 'NaN',
            'reallocated_sectors': 'NaN',
            'uncorrectable_errors': 'NaN',
            'temperature': 'NaN',
            'power_on_hours': 'NaN',
            'health_status': 'SMARTCTL未安装'
        }
    except subprocess.CalledProcessError as e:
        # 命令执行失败
        print(f"获取SMART数据失败: {e}")
        return {
            'bad_blocks': 'NaN',
            'reallocated_sectors': 'NaN',
            'uncorrectable_errors': 'NaN',
            'temperature': 'NaN',
            'power_on_hours': 'NaN',
            'health_status': '获取失败'
        }
    except Exception as e:
        # 其他异常
        print(f"解析SMART数据时出错: {e}")
        return {
            'bad_blocks': 'NaN',
            'reallocated_sectors': 'NaN',
            'uncorrectable_errors': 'NaN',
            'temperature': 'NaN',
            'power_on_hours': 'NaN',
            'health_status': '解析错误'
        }

def cleanup_old_files():
    """清理超过3天的文件"""
    current_time = time.time()
    
    for filename in os.listdir(UPLOAD_FOLDER):
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(filepath):
            mtime = os.path.getmtime(filepath)
            if current_time - mtime > FILE_LIFETIME:
                try:
                    os.remove(filepath)
                    print(f"Deleted old file: {filename}")
                except Exception as e:
                    print(f"Error deleting file {filename}: {str(e)}")

def background_cleanup():
    """后台清理任务"""
    while True:
        time.sleep(CLEANUP_INTERVAL)
        print("Running cleanup task...")
        cleanup_old_files()

# 启动后台清理线程
cleanup_thread = threading.Thread(target=background_cleanup, daemon=True)
cleanup_thread.start()

def get_file_info():
    """获取目录中所有文件的信息"""
    files = []
    for filename in os.listdir(UPLOAD_FOLDER):
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            files.append({
                'name': filename,
                'size': stat.st_size,
                'upload_time': datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                'expire_time': datetime.fromtimestamp(stat.st_ctime + FILE_LIFETIME).strftime('%Y-%m-%d %H:%M:%S'),
                'download_url': url_for('download_file', filename=filename)
            })
    return files

@app.route('/')
def index():
    disk_usage = get_disk_usage()
    smart_data = get_smart_data()
    files = get_file_info()
    
    return render_template('index.html', 
                           disk_usage=disk_usage,
                           smart_data=smart_data,
                           files=files,
                           max_file_size=MAX_FILE_SIZE,
                           allowed_extensions=', '.join(ALLOWED_EXTENSIONS))

@app.route('/upload', methods=['POST'])
def upload_file():
    # 检查文件是否在请求中
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'No file part'})
    
    file = request.files['file']
    
    # 如果用户没有选择文件
    if file.filename == '':
        return jsonify({'success': False, 'message': 'No selected file'})
    
    # 检查文件扩展名
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': 'File type not allowed'})
    
    # 检查文件大小
    file.seek(0, os.SEEK_END)
    file_length = file.tell()
    file.seek(0)
    
    if file_length > MAX_FILE_SIZE:
        return jsonify({'success': False, 'message': 'File too large'})
    
    # 保存文件
    filename = file.filename
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    
    try:
        file.save(filepath)
        return jsonify({
            'success': True,
            'message': f'File {filename} successfully uploaded!',
            'filename': filename,
            'size': os.path.getsize(filepath),
            'upload_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'expire_time': (datetime.now() + timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S'),
            'download_url': url_for('download_file', filename=filename)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error saving file: {str(e)}'})

@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    except Exception as e:
        flash(f'Error downloading file: {str(e)}')
        return redirect(url_for('index'))

@app.route('/file_info')
def file_info():
    return jsonify(get_file_info())

@app.route('/disk_info')
def disk_info():
    return jsonify({
        'disk_usage': get_disk_usage(),
        'smart_data': get_smart_data()
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)