"""
GX Schedule Optimizer - Production Version v3
- Shows ALL arena formats (not just trainer-detected)
- Google OAuth (restricted to @curefit.com)
- Metabase query links for data download
- Dance → DF mapping
"""

from flask import Flask, render_template_string, request, jsonify, send_file, redirect, url_for, session
from functools import wraps
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tempfile
import os

from ortools.sat.python import cp_model

# =============================================================================
# APP SETUP
# =============================================================================

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
ALLOWED_DOMAIN = 'curefit.com'
DEV_MODE = os.environ.get('DEV_MODE', 'true').lower() == 'true'

# =============================================================================
# METABASE QUERY LINKS (Update these with your actual links)
# =============================================================================

METABASE_LINKS = {
    'center_data': os.environ.get('METABASE_CENTER_LINK', 'https://metabase.curefit.co/question/XXXXX'),
    'trainer_data': os.environ.get('METABASE_TRAINER_LINK', 'https://metabase.curefit.co/question/XXXXX'),
    'historical_data': os.environ.get('METABASE_HISTORICAL_LINK', 'https://metabase.curefit.co/question/XXXXX'),
}

# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    DAY_TO_NUM = {d: i+1 for i, d in enumerate(DAYS)}
    
    # All formats available per arena type
    ARENA_FORMATS = {
        1.0: ['HRX', 'S&C', 'DF', 'Yoga', 'Burn'],
        1.5: ['HRX', 'S&C', 'DF', 'Yoga', 'Boxing', 'Burn'],
        2.0: ['HRX', 'S&C', 'DF', 'Yoga', 'Burn'],
        3.0: ['HRX', 'S&C', 'DF', 'Yoga', 'Boxing', 'Burn'],
        4.0: ['HRX', 'S&C', 'DF', 'Yoga', 'Boxing', 'Burn'],
    }
    
    # Format name mappings (trainer file → standard name)
    FORMAT_MAPPING = {
        'Dance': 'DF',
        'dance': 'DF',
        'DANCE': 'DF',
        'Dance Fitness': 'DF',
        'S&C': 'S&C',
        'SC': 'S&C',
        'Strength': 'S&C',
        'HRX': 'HRX',
        'Yoga': 'Yoga',
        'yoga': 'Yoga',
        'Burn': 'Burn',
        'Boxing': 'Boxing',
    }
    
    ARENA_CAPACITY = {1.0: 1, 1.5: 2, 2.0: 2, 3.0: 3, 4.0: 4}
    ARENA_DAILY_MAX = {
        1.0: {'Yoga': 2, 'HRX_SC': 5, 'DF': 2, 'Boxing_Burn': 0},
        1.5: {'Yoga': 2, 'HRX_SC': 5, 'DF': 2, 'Boxing_Burn': 5},
        2.0: {'Yoga': 4, 'HRX_SC': 10, 'DF': 4, 'Boxing_Burn': 0},
        3.0: {'Yoga': 5, 'HRX_SC': 10, 'DF': 4, 'Boxing_Burn': 5},
        4.0: {'Yoga': 10, 'HRX_SC': 15, 'DF': 8, 'Boxing_Burn': 10},
    }
    OPEN_HOUR, CLOSE_HOUR = 6, 22
    DEAD_ZONE_START, DEAD_ZONE_END = 11, 16
    PEAK_HOURS = [7, 8, 9, 10, 18, 19, 20, 21]
    SOLVER_TIMEOUT = 60

STATE = {}

# =============================================================================
# AUTHENTICATION
# =============================================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if DEV_MODE:
            return f(*args, **kwargs)
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_current_user():
    if DEV_MODE:
        return {'email': 'dev@curefit.com', 'name': 'Dev User'}
    return session.get('user', {})

# =============================================================================
# HTML TEMPLATES
# =============================================================================

LOGIN_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Login - GX Schedule Optimizer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .login-container { background: white; border-radius: 20px; padding: 50px; text-align: center; box-shadow: 0 25px 50px rgba(0,0,0,0.3); max-width: 420px; width: 90%; }
        .logo { font-size: 48px; margin-bottom: 10px; }
        h1 { color: #E53935; font-size: 28px; margin-bottom: 10px; }
        p { color: #666; margin-bottom: 30px; line-height: 1.6; }
        .google-btn { display: inline-flex; align-items: center; gap: 12px; background: white; border: 2px solid #E53935; color: #333; padding: 14px 28px; border-radius: 50px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; text-decoration: none; }
        .google-btn:hover { background: #E53935; color: white; transform: translateY(-2px); box-shadow: 0 10px 20px rgba(229,57,53,0.3); }
        .google-btn img { width: 24px; height: 24px; }
        .domain-note { margin-top: 25px; padding: 15px; background: #FFF3E0; border-radius: 10px; font-size: 14px; color: #E65100; }
        .error { background: #FFEBEE; color: #C62828; padding: 15px; border-radius: 10px; margin-bottom: 20px; }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="logo">📅</div>
        <h1>GX Schedule Optimizer</h1>
        <p>Sign in with your Cult.fit Google account to access the schedule optimization tool.</p>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <a href="{{ auth_url }}" class="google-btn">
            <img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" alt="Google">
            Sign in with Google
        </a>
        <div class="domain-note">🔒 Only @curefit.com accounts can access this tool</div>
    </div>
</body>
</html>
'''

MAIN_PAGE = '''
<!DOCTYPE html>
<html>
<head>
    <title>GX Schedule Optimizer - Cult.fit</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --red: #E53935; --red-dark: #C62828; --dark: #171A26; --dark-light: #2D3142;
            --green: #4CAF50; --blue: #2196F3; --orange: #FF9800; --gray: #f8f9fa; --gray-dark: #6c757d;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', sans-serif; background: var(--gray); color: #333; line-height: 1.6; }
        
        .header { background: linear-gradient(135deg, var(--dark) 0%, var(--dark-light) 100%); color: white; position: sticky; top: 0; z-index: 100; box-shadow: 0 4px 20px rgba(0,0,0,0.2); }
        .header-content { max-width: 1400px; margin: 0 auto; padding: 20px 30px; display: flex; justify-content: space-between; align-items: center; }
        .brand { display: flex; align-items: center; gap: 15px; }
        .brand-icon { width: 50px; height: 50px; background: var(--red); border-radius: 12px; display: flex; align-items: center; justify-content: center; font-size: 24px; }
        .brand h1 { font-size: 24px; font-weight: 700; }
        .brand p { font-size: 13px; opacity: 0.8; margin-top: 2px; }
        .user-info { display: flex; align-items: center; gap: 15px; }
        .user-avatar { width: 40px; height: 40px; background: var(--red); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 600; }
        .logout-btn { background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); color: white; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; transition: all 0.3s; text-decoration: none; }
        .logout-btn:hover { background: rgba(255,255,255,0.2); }
        
        .container { max-width: 1400px; margin: 0 auto; padding: 30px; }
        
        .step { background: white; border-radius: 16px; margin-bottom: 24px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); overflow: hidden; }
        .step:hover { box-shadow: 0 8px 25px rgba(0,0,0,0.1); }
        .step-header { background: var(--dark); color: white; padding: 20px 25px; display: flex; align-items: center; gap: 15px; }
        .step-number { width: 36px; height: 36px; background: var(--red); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: 700; }
        .step-title { font-size: 18px; font-weight: 600; }
        .step-content { padding: 25px; }
        
        /* Metabase Links Section */
        .data-links { background: linear-gradient(135deg, #E3F2FD 0%, #BBDEFB 100%); border-radius: 12px; padding: 20px; margin-bottom: 25px; border: 1px solid #90CAF9; }
        .data-links h3 { color: #1565C0; margin-bottom: 15px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
        .data-links-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
        .data-link { display: flex; align-items: center; gap: 10px; padding: 12px 16px; background: white; border-radius: 8px; text-decoration: none; color: #1565C0; font-weight: 500; transition: all 0.3s; border: 1px solid #90CAF9; }
        .data-link:hover { background: #1565C0; color: white; transform: translateY(-2px); box-shadow: 0 4px 12px rgba(21,101,192,0.3); }
        .data-link-icon { font-size: 20px; }
        
        .upload-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 25px; }
        .upload-box { border: 2px dashed #ddd; border-radius: 12px; padding: 30px; text-align: center; transition: all 0.3s; cursor: pointer; position: relative; }
        .upload-box:hover { border-color: var(--red); background: #FFF5F5; }
        .upload-box.has-file { border-color: var(--green); background: #F1F8E9; }
        .upload-box input[type="file"] { position: absolute; top: 0; left: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
        .upload-icon { font-size: 40px; margin-bottom: 10px; }
        .upload-label { font-weight: 600; color: var(--dark); margin-bottom: 5px; }
        .upload-hint { font-size: 13px; color: var(--gray-dark); }
        .file-name { margin-top: 10px; font-size: 13px; color: var(--green); font-weight: 500; }
        
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 10px; padding: 14px 28px; border: none; border-radius: 10px; font-size: 16px; font-weight: 600; cursor: pointer; transition: all 0.3s; text-decoration: none; }
        .btn-primary { background: linear-gradient(135deg, var(--red) 0%, var(--red-dark) 100%); color: white; box-shadow: 0 4px 15px rgba(229,57,53,0.3); }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(229,57,53,0.4); }
        .btn-primary:disabled { background: #ccc; box-shadow: none; cursor: not-allowed; transform: none; }
        .btn-secondary { background: white; color: var(--dark); border: 2px solid #e0e0e0; }
        .btn-secondary:hover { border-color: var(--dark); }
        .btn-lg { padding: 18px 36px; font-size: 18px; }
        .btn-full { width: 100%; }
        
        .status { padding: 20px; border-radius: 12px; margin-top: 20px; display: flex; align-items: flex-start; gap: 15px; }
        .status-icon { font-size: 24px; flex-shrink: 0; }
        .status.success { background: linear-gradient(135deg, #E8F5E9 0%, #C8E6C9 100%); border: 1px solid #A5D6A7; }
        .status.error { background: linear-gradient(135deg, #FFEBEE 0%, #FFCDD2 100%); border: 1px solid #EF9A9A; }
        .status h4 { margin-bottom: 5px; font-size: 16px; }
        .status p { font-size: 14px; color: #555; }
        
        .summary-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-top: 20px; }
        .summary-card { background: var(--gray); border-radius: 12px; padding: 20px; text-align: center; }
        .summary-card .value { font-size: 32px; font-weight: 700; color: var(--red); }
        .summary-card .label { font-size: 13px; color: var(--gray-dark); margin-top: 5px; }
        
        .table-container { overflow-x: auto; margin-top: 20px; border-radius: 12px; border: 1px solid #e0e0e0; }
        .center-table { width: 100%; border-collapse: collapse; }
        .center-table th { background: var(--dark); color: white; padding: 14px 16px; text-align: left; font-weight: 600; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }
        .center-table td { padding: 14px 16px; border-bottom: 1px solid #f0f0f0; font-size: 14px; vertical-align: middle; }
        .center-table tr:hover { background: #FAFAFA; }
        .center-table tr:last-child td { border-bottom: none; }
        
        .checkbox-wrapper { display: flex; align-items: center; justify-content: center; }
        .checkbox-wrapper input[type="checkbox"] { width: 20px; height: 20px; accent-color: var(--red); cursor: pointer; }
        
        .multiselect-container { position: relative; min-width: 220px; }
        .multiselect-display { display: flex; flex-wrap: wrap; gap: 4px; padding: 8px 12px; border: 2px solid #e0e0e0; border-radius: 8px; background: white; cursor: pointer; min-height: 42px; align-items: center; transition: all 0.3s; }
        .multiselect-display:hover { border-color: var(--red); }
        .multiselect-display.open { border-color: var(--red); box-shadow: 0 0 0 3px rgba(229,57,53,0.1); }
        .multiselect-display .tag { display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .multiselect-display .tag.hrx { background: #FFEBEE; color: #C62828; }
        .multiselect-display .tag.sc { background: #E3F2FD; color: #1565C0; }
        .multiselect-display .tag.yoga { background: #F3E5F5; color: #7B1FA2; }
        .multiselect-display .tag.df { background: #FFF3E0; color: #E65100; }
        .multiselect-display .tag.burn { background: #FCE4EC; color: #AD1457; }
        .multiselect-display .tag.boxing { background: #E8F5E9; color: #2E7D32; }
        .multiselect-placeholder { color: #999; font-size: 13px; }
        .multiselect-dropdown { position: absolute; top: 100%; left: 0; right: 0; background: white; border: 2px solid var(--red); border-radius: 8px; margin-top: 4px; box-shadow: 0 8px 25px rgba(0,0,0,0.15); z-index: 1000; display: none; }
        .multiselect-dropdown.show { display: block; }
        .multiselect-option { display: flex; align-items: center; gap: 10px; padding: 10px 14px; cursor: pointer; transition: background 0.2s; }
        .multiselect-option:hover { background: #f5f5f5; }
        .multiselect-option input { width: 18px; height: 18px; accent-color: var(--red); }
        .multiselect-option label { flex: 1; cursor: pointer; font-size: 14px; display: flex; align-items: center; gap: 8px; }
        .multiselect-option .format-dot { width: 10px; height: 10px; border-radius: 50%; }
        .multiselect-option .format-dot.hrx { background: #C62828; }
        .multiselect-option .format-dot.sc { background: #1565C0; }
        .multiselect-option .format-dot.yoga { background: #7B1FA2; }
        .multiselect-option .format-dot.df { background: #E65100; }
        .multiselect-option .format-dot.burn { background: #AD1457; }
        .multiselect-option .format-dot.boxing { background: #2E7D32; }
        .multiselect-option .trainer-badge { font-size: 10px; padding: 2px 6px; border-radius: 10px; background: #E8F5E9; color: #2E7D32; margin-left: auto; }
        .multiselect-option .no-trainer-badge { font-size: 10px; padding: 2px 6px; border-radius: 10px; background: #FFF3E0; color: #E65100; margin-left: auto; }
        
        .select-wrapper select { padding: 10px 35px 10px 15px; border: 2px solid #e0e0e0; border-radius: 8px; background: white url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23333' d='M6 8L1 3h10z'/%3E%3C/svg%3E") no-repeat right 12px center; appearance: none; font-size: 14px; font-weight: 500; cursor: pointer; min-width: 180px; transition: all 0.3s; }
        .select-wrapper select:hover { border-color: var(--red); }
        .select-wrapper select:focus { outline: none; border-color: var(--red); box-shadow: 0 0 0 3px rgba(229,57,53,0.1); }
        .select-wrapper select:disabled { background-color: #f5f5f5; color: #999; cursor: not-allowed; }
        
        .action-row { display: flex; gap: 10px; margin-bottom: 20px; }
        
        .progress-log { background: var(--dark); border-radius: 12px; padding: 20px; margin-top: 20px; font-family: 'Monaco', 'Menlo', monospace; font-size: 13px; max-height: 250px; overflow-y: auto; }
        .progress-log .log-entry { padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.1); color: #ccc; }
        .progress-log .log-entry:last-child { border-bottom: none; }
        .progress-log .success { color: #69F0AE; }
        .progress-log .error { color: #FF5252; }
        .progress-log .pending { color: #64B5F6; }
        
        .results-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .result-card { background: linear-gradient(135deg, #fff 0%, var(--gray) 100%); border-radius: 16px; padding: 25px; text-align: center; border: 1px solid #e0e0e0; }
        .result-card .icon { font-size: 32px; margin-bottom: 10px; }
        .result-card .value { font-size: 36px; font-weight: 800; color: var(--dark); }
        .result-card .label { font-size: 13px; color: var(--gray-dark); margin-top: 5px; text-transform: uppercase; letter-spacing: 0.5px; }
        .result-card.highlight { background: linear-gradient(135deg, var(--red) 0%, var(--red-dark) 100%); border: none; }
        .result-card.highlight .value, .result-card.highlight .label, .result-card.highlight .icon { color: white; }
        
        .download-section { text-align: center; padding: 30px; background: linear-gradient(135deg, #F5F5F5 0%, #EEEEEE 100%); border-radius: 16px; margin-top: 20px; }
        .download-btn { display: inline-flex; align-items: center; gap: 12px; background: linear-gradient(135deg, var(--green) 0%, #388E3C 100%); color: white; padding: 20px 40px; border-radius: 12px; font-size: 18px; font-weight: 700; text-decoration: none; box-shadow: 0 8px 25px rgba(76,175,80,0.3); transition: all 0.3s; }
        .download-btn:hover { transform: translateY(-3px); box-shadow: 0 12px 35px rgba(76,175,80,0.4); }
        
        .spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }
        
        .hidden { display: none !important; }
        
        @media (max-width: 768px) {
            .header-content { flex-direction: column; gap: 15px; text-align: center; }
            .container { padding: 15px; }
            .summary-grid { grid-template-columns: 1fr; }
            .action-row { flex-direction: column; }
        }
    </style>
</head>
<body>
    <header class="header">
        <div class="header-content">
            <div class="brand">
                <div class="brand-icon">📅</div>
                <div>
                    <h1>GX Schedule Optimizer</h1>
                    <p>Self-serve tool for generating optimized class schedules</p>
                </div>
            </div>
            <div class="user-info">
                <div class="user-avatar">{{ user_initial }}</div>
                <span class="user-name">{{ user_name }}</span>
                {% if not dev_mode %}<a href="/logout" class="logout-btn">Sign Out</a>{% endif %}
            </div>
        </div>
    </header>
    
    <div class="container">
        <!-- Step 1 -->
        <div class="step">
            <div class="step-header">
                <div class="step-number">1</div>
                <span class="step-title">Upload Data Files</span>
            </div>
            <div class="step-content">
                <!-- Metabase Links -->
                <div class="data-links">
                    <h3>📊 Download Data from Metabase</h3>
                    <p style="margin-bottom: 15px; color: #1565C0; font-size: 14px;">Click each link below, run the query, and download as CSV:</p>
                    <div class="data-links-grid">
                        <a href="{{ metabase_links.center_data }}" target="_blank" class="data-link">
                            <span class="data-link-icon">🏢</span>
                            <span>Center Data Query</span>
                        </a>
                        <a href="{{ metabase_links.trainer_data }}" target="_blank" class="data-link">
                            <span class="data-link-icon">👥</span>
                            <span>Trainer Data Query</span>
                        </a>
                        <a href="{{ metabase_links.historical_data }}" target="_blank" class="data-link">
                            <span class="data-link-icon">📈</span>
                            <span>Historical Data Query</span>
                        </a>
                    </div>
                </div>
                
                <form id="uploadForm" enctype="multipart/form-data">
                    <div class="upload-grid">
                        <div class="upload-box" id="box-center">
                            <input type="file" name="center_file" accept=".csv" id="center_file">
                            <div class="upload-icon">📊</div>
                            <div class="upload-label">Center Data</div>
                            <div class="upload-hint">CSV with center_name, arena</div>
                            <div class="file-name" id="name-center"></div>
                        </div>
                        <div class="upload-box" id="box-trainer">
                            <input type="file" name="trainer_file" accept=".csv" id="trainer_file">
                            <div class="upload-icon">👥</div>
                            <div class="upload-label">Trainer Data</div>
                            <div class="upload-hint">CSV with trainer details</div>
                            <div class="file-name" id="name-trainer"></div>
                        </div>
                        <div class="upload-box" id="box-historical">
                            <input type="file" name="historical_file" accept=".csv" id="historical_file">
                            <div class="upload-icon">📈</div>
                            <div class="upload-label">Historical Data</div>
                            <div class="upload-hint">CSV with past class data</div>
                            <div class="file-name" id="name-historical"></div>
                        </div>
                    </div>
                    <button type="submit" class="btn btn-primary btn-lg btn-full" id="uploadBtn">📊 Load & Validate Files</button>
                </form>
                <div id="uploadStatus"></div>
            </div>
        </div>
        
        <!-- Step 2 -->
        <div class="step hidden" id="step2">
            <div class="step-header">
                <div class="step-number">2</div>
                <span class="step-title">Configure Centers</span>
            </div>
            <div class="step-content">
                <p style="margin-bottom: 15px; color: #666;">Select centers, choose formats to include (including freelancer formats), and configure Yoga Monday preferences:</p>
                <div class="action-row">
                    <button type="button" class="btn btn-secondary" onclick="selectAll(true)">✅ Select All</button>
                    <button type="button" class="btn btn-secondary" onclick="selectAll(false)">❌ Clear All</button>
                </div>
                <div class="table-container">
                    <table class="center-table">
                        <thead>
                            <tr>
                                <th style="width: 50px;">Select</th>
                                <th>Center Name</th>
                                <th style="width: 70px;">Arena</th>
                                <th style="width: 280px;">Formats to Include</th>
                                <th style="width: 180px;">Yoga Monday</th>
                            </tr>
                        </thead>
                        <tbody id="centerTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>
        
        <!-- Step 3 -->
        <div class="step hidden" id="step3">
            <div class="step-header">
                <div class="step-number">3</div>
                <span class="step-title">Generate Optimized Schedule</span>
            </div>
            <div class="step-content">
                <p style="margin-bottom: 20px; color: #666;">
                    Click below to run optimization. <strong>~30-60 seconds per center.</strong>
                </p>
                <button type="button" class="btn btn-primary btn-lg btn-full" id="generateBtn" onclick="runOptimization()">
                    🚀 Generate Optimized Schedule
                </button>
                <div id="progressLog" class="progress-log hidden"></div>
            </div>
        </div>
        
        <!-- Results -->
        <div class="step hidden" id="results">
            <div class="step-header" style="background: linear-gradient(135deg, var(--green) 0%, #388E3C 100%);">
                <div class="step-number" style="background: white; color: var(--green);">✓</div>
                <span class="step-title">Optimization Complete!</span>
            </div>
            <div class="step-content">
                <div class="results-grid" id="resultsGrid"></div>
                <div class="download-section">
                    <a href="/api/download" class="download-btn">📥 Download Optimized Schedule (Excel)</a>
                </div>
                <div class="table-container" style="margin-top: 30px;">
                    <table class="center-table">
                        <thead>
                            <tr><th>Center</th><th>Status</th><th>Classes</th><th>Avg Utilization</th><th>DF Boosted Days</th></tr>
                        </thead>
                        <tbody id="resultsTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let centerData = [];
        
        ['center', 'trainer', 'historical'].forEach(type => {
            const input = document.getElementById(`${type}_file`);
            const box = document.getElementById(`box-${type}`);
            const nameEl = document.getElementById(`name-${type}`);
            input.addEventListener('change', function() {
                if (this.files.length > 0) {
                    box.classList.add('has-file');
                    nameEl.textContent = '✓ ' + this.files[0].name;
                } else {
                    box.classList.remove('has-file');
                    nameEl.textContent = '';
                }
            });
        });
        
        document.addEventListener('click', function(e) {
            if (!e.target.closest('.multiselect-container')) {
                document.querySelectorAll('.multiselect-dropdown').forEach(d => d.classList.remove('show'));
                document.querySelectorAll('.multiselect-display').forEach(d => d.classList.remove('open'));
            }
        });
        
        document.getElementById('uploadForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            const btn = document.getElementById('uploadBtn');
            const status = document.getElementById('uploadStatus');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Processing...';
            
            try {
                const response = await fetch('/api/upload', { method: 'POST', body: new FormData(this) });
                const data = await response.json();
                
                if (data.success) {
                    status.innerHTML = `
                        <div class="status success">
                            <span class="status-icon">✅</span>
                            <div><h4>Files Loaded Successfully!</h4><p>Ready to configure centers.</p></div>
                        </div>
                        <div class="summary-grid">
                            <div class="summary-card"><div class="value">${data.summary.centers}</div><div class="label">Centers</div></div>
                            <div class="summary-card"><div class="value">${data.summary.trainers}</div><div class="label">Trainers</div></div>
                            <div class="summary-card"><div class="value">${data.summary.records.toLocaleString()}</div><div class="label">Historical Records</div></div>
                        </div>`;
                    centerData = data.centers;
                    renderCenterTable();
                    document.getElementById('step2').classList.remove('hidden');
                    document.getElementById('step3').classList.remove('hidden');
                    document.getElementById('step2').scrollIntoView({ behavior: 'smooth' });
                } else {
                    status.innerHTML = `<div class="status error"><span class="status-icon">❌</span><div><h4>Error</h4><p>${data.error}</p></div></div>`;
                }
            } catch (err) {
                status.innerHTML = `<div class="status error"><span class="status-icon">❌</span><div><h4>Error</h4><p>${err.message}</p></div></div>`;
            }
            btn.disabled = false;
            btn.innerHTML = '📊 Load & Validate Files';
        });
        
        function getFormatClass(f) {
            return {'HRX': 'hrx', 'S&C': 'sc', 'Yoga': 'yoga', 'DF': 'df', 'Burn': 'burn', 'Boxing': 'boxing'}[f] || '';
        }
        
        function toggleDropdown(idx) {
            const dropdown = document.getElementById(`formats_dropdown_${idx}`);
            const display = document.getElementById(`formats_display_${idx}`);
            const isOpen = dropdown.classList.contains('show');
            
            document.querySelectorAll('.multiselect-dropdown').forEach(d => d.classList.remove('show'));
            document.querySelectorAll('.multiselect-display').forEach(d => d.classList.remove('open'));
            
            if (!isOpen) {
                dropdown.classList.add('show');
                display.classList.add('open');
            }
        }
        
        function updateFormatDisplay(idx) {
            const display = document.getElementById(`formats_display_${idx}`);
            const checkboxes = document.querySelectorAll(`#formats_dropdown_${idx} input[type="checkbox"]:checked`);
            const selected = Array.from(checkboxes).map(cb => cb.value);
            
            if (selected.length === 0) {
                display.innerHTML = '<span class="multiselect-placeholder">Select formats...</span>';
            } else {
                display.innerHTML = selected.map(f => `<span class="tag ${getFormatClass(f)}">${f}</span>`).join('');
            }
            
            const yogaCell = document.getElementById(`yoga_cell_${idx}`);
            if (yogaCell) {
                if (selected.includes('Yoga')) {
                    yogaCell.innerHTML = `<div class="select-wrapper">
                        <select id="yoga_${idx}">
                            <option value="Optimizer">🤖 Optimizer Decides</option>
                            <option value="Morning">🌅 Morning (6-10 AM)</option>
                            <option value="Evening">🌆 Evening (4-9 PM)</option>
                        </select>
                    </div>`;
                } else {
                    yogaCell.innerHTML = '<span style="color: #999; font-size: 13px;">N/A</span>';
                }
            }
        }
        
        function renderCenterTable() {
            const tbody = document.getElementById('centerTableBody');
            tbody.innerHTML = '';
            
            centerData.forEach((center, idx) => {
                const allFormats = center.all_formats;  // All formats for this arena
                const trainerFormats = center.trainer_formats;  // Formats with trainers
                const hasYoga = allFormats.includes('Yoga');
                
                // Build format options with trainer/no-trainer badges
                const options = allFormats.map(f => {
                    const hasTrainer = trainerFormats.includes(f);
                    const badge = hasTrainer 
                        ? '<span class="trainer-badge">Has trainer</span>'
                        : '<span class="no-trainer-badge">Freelancer</span>';
                    const checked = hasTrainer ? 'checked' : '';  // Pre-select only formats with trainers
                    return `
                        <div class="multiselect-option">
                            <input type="checkbox" id="fmt_${idx}_${f}" value="${f}" ${checked} onchange="updateFormatDisplay(${idx})">
                            <label for="fmt_${idx}_${f}">
                                <span class="format-dot ${getFormatClass(f)}"></span>
                                ${f}
                            </label>
                            ${badge}
                        </div>
                    `;
                }).join('');
                
                // Initial display - only trainer formats
                const initialTags = trainerFormats.length > 0
                    ? trainerFormats.map(f => `<span class="tag ${getFormatClass(f)}">${f}</span>`).join('')
                    : '<span class="multiselect-placeholder">Select formats...</span>';
                
                const formatSelectHtml = `
                    <div class="multiselect-container">
                        <div class="multiselect-display" id="formats_display_${idx}" onclick="toggleDropdown(${idx})">
                            ${initialTags}
                        </div>
                        <div class="multiselect-dropdown" id="formats_dropdown_${idx}">
                            ${options}
                        </div>
                    </div>
                `;
                
                const hasYogaTrainer = trainerFormats.includes('Yoga');
                const yogaHtml = hasYogaTrainer 
                    ? `<div class="select-wrapper">
                        <select id="yoga_${idx}">
                            <option value="Optimizer">🤖 Optimizer Decides</option>
                            <option value="Morning">🌅 Morning (6-10 AM)</option>
                            <option value="Evening">🌆 Evening (4-9 PM)</option>
                        </select>
                       </div>`
                    : '<span style="color: #999; font-size: 13px;">N/A</span>';
                
                tbody.innerHTML += `
                    <tr>
                        <td><div class="checkbox-wrapper"><input type="checkbox" id="select_${idx}" ${trainerFormats.length > 0 ? 'checked' : ''}></div></td>
                        <td><strong>${center.name}</strong></td>
                        <td style="text-align: center; font-weight: 600;">${center.arena}</td>
                        <td>${formatSelectHtml}</td>
                        <td id="yoga_cell_${idx}">${yogaHtml}</td>
                    </tr>
                `;
            });
        }
        
        function selectAll(checked) {
            centerData.forEach((center, idx) => {
                const cb = document.getElementById(`select_${idx}`);
                if (cb) cb.checked = checked;
            });
        }
        
        function getSelectedFormats(idx) {
            const checkboxes = document.querySelectorAll(`#formats_dropdown_${idx} input[type="checkbox"]:checked`);
            return Array.from(checkboxes).map(cb => cb.value);
        }
        
        async function runOptimization() {
            const btn = document.getElementById('generateBtn');
            const log = document.getElementById('progressLog');
            
            const selectedCenters = [];
            centerData.forEach((center, idx) => {
                if (document.getElementById(`select_${idx}`)?.checked) {
                    const formats = getSelectedFormats(idx);
                    const yogaEl = document.getElementById(`yoga_${idx}`);
                    if (formats.length > 0) {
                        selectedCenters.push({
                            name: center.name,
                            formats: formats,
                            yoga_pref: yogaEl ? yogaEl.value : 'Optimizer'
                        });
                    }
                }
            });
            
            if (selectedCenters.length === 0) {
                alert('Please select at least one center with formats.');
                return;
            }
            
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Optimizing...';
            log.classList.remove('hidden');
            log.innerHTML = '<div class="log-entry pending">⏳ Starting optimization...</div>';
            
            try {
                const response = await fetch('/api/optimize', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ centers: selectedCenters })
                });
                const data = await response.json();
                
                if (data.success) {
                    log.innerHTML = data.log.map(l => {
                        const cls = l.includes('✅') ? 'success' : (l.includes('❌') ? 'error' : 'pending');
                        return `<div class="log-entry ${cls}">${l}</div>`;
                    }).join('');
                    
                    document.getElementById('results').classList.remove('hidden');
                    document.getElementById('resultsGrid').innerHTML = `
                        <div class="result-card"><div class="icon">🎯</div><div class="value">${data.summary.successful}/${data.summary.total}</div><div class="label">Centers</div></div>
                        <div class="result-card highlight"><div class="icon">📅</div><div class="value">${data.summary.total_classes}</div><div class="label">Classes</div></div>
                        <div class="result-card"><div class="icon">📈</div><div class="value">${data.summary.avg_util}%</div><div class="label">Avg Util</div></div>
                    `;
                    
                    const tbody = document.getElementById('resultsTableBody');
                    tbody.innerHTML = data.results.map(r => {
                        const icon = r.status.includes('OPTIMAL') || r.status.includes('FEASIBLE') ? '✅' : '❌';
                        return `<tr><td><strong>${r.center}</strong></td><td>${icon} ${r.status}</td><td style="text-align:center;font-weight:600;">${r.classes}</td><td style="text-align:center;">${r.avg_util}</td><td>${r.df_boosted || '-'}</td></tr>`;
                    }).join('');
                    
                    document.getElementById('results').scrollIntoView({ behavior: 'smooth' });
                } else {
                    log.innerHTML = `<div class="log-entry error">❌ ${data.error}</div>`;
                }
            } catch (err) {
                log.innerHTML = `<div class="log-entry error">❌ ${err.message}</div>`;
            }
            btn.disabled = false;
            btn.innerHTML = '🚀 Generate Optimized Schedule';
        }
    </script>
</body>
</html>
'''

# =============================================================================
# DATA FUNCTIONS
# =============================================================================

def normalize_format(fmt):
    """Normalize format names (Dance → DF, etc.)"""
    fmt = str(fmt).strip()
    return Config.FORMAT_MAPPING.get(fmt, fmt)

def detect_trainer_formats(center_name, trainer_df, arena):
    """Detect formats that have trainers assigned"""
    if 'home_center_2' in trainer_df.columns:
        trainers = trainer_df[(trainer_df['home_center'] == center_name) | (trainer_df['home_center_2'] == center_name)]
    else:
        trainers = trainer_df[trainer_df['home_center'] == center_name]
    
    formats = set()
    for fmt in trainers['format'].unique():
        normalized = normalize_format(fmt)
        formats.add(normalized)
        if normalized == 'S&C':
            formats.add('HRX')
            if arena in [1.0, 2.0]:
                formats.add('Burn')
        if normalized == 'Boxing':
            formats.add('Burn')
    
    arena_fmts = set(Config.ARENA_FORMATS.get(arena, Config.ARENA_FORMATS[1.0]))
    return sorted(formats & arena_fmts)

def get_all_arena_formats(arena):
    """Get all possible formats for an arena type"""
    return Config.ARENA_FORMATS.get(arena, Config.ARENA_FORMATS[1.0])

def build_intelligence(df):
    df = df.copy()
    df['class_date'] = pd.to_datetime(df['class_date'], format='%B %d, %Y', errors='coerce')
    df['util'] = (df['total_attendance'] / df['total_capacity'] * 100).clip(0, 100)
    df['day_name'] = df['day_of_week'].map({i: d for i, d in enumerate(Config.DAYS, 1)})
    # Normalize format names in historical data
    df['format'] = df['format'].apply(normalize_format)
    cutoff = df['class_date'].max() - timedelta(weeks=8)
    recent = df[df['class_date'] >= cutoff]
    scores = {key: grp['util'].mean() for key, grp in recent.groupby(['center_name', 'day_name', 'class_start_hour', 'format'])}
    affinity = {key: grp['util'].mean() for key, grp in recent.groupby(['class_start_hour', 'format'])}
    return {'scores': scores, 'affinity': affinity}

def build_validator(center_df, trainer_df):
    arenas = dict(zip(center_df['center_name'], center_df['arena']))
    offs, counts, female = {}, {}, {}
    for _, row in trainer_df.iterrows():
        centers = [row['home_center']]
        if 'home_center_2' in row.index and row['home_center_2'] and row['home_center_2'] != 'nan':
            centers.append(row['home_center_2'])
        fmt = normalize_format(row['format'])
        for center in centers:
            if not center or center == 'nan': continue
            key = (center, fmt)
            offs.setdefault(key, []).append(row['weekly_off'])
            counts[key] = counts.get(key, 0) + 1
            if row['gender'] == 'Female':
                female.setdefault(center, set()).add(fmt)
    return {'arenas': arenas, 'offs': offs, 'counts': counts, 'female': female}

def get_score(intel, c, d, h, f):
    return intel['scores'].get((c, d, h, f), intel['affinity'].get((h, f), 50.0))

def is_off(validator, c, f, d):
    arena = validator['arenas'].get(c, 1.0)
    dm = Config.ARENA_DAILY_MAX.get(arena, Config.ARENA_DAILY_MAX[1.0])
    check = 'S&C' if f == 'HRX' else ('S&C' if f == 'Burn' and dm['Boxing_Burn'] == 0 else ('Boxing' if f == 'Burn' else f))
    key = (c, check)
    return key in validator['offs'] and validator['counts'].get(key, 1) == 1 and d in validator['offs'][key]

# =============================================================================
# OPTIMIZER
# =============================================================================

def optimize_center(center, formats, yoga_pref, intel, validator):
    if not formats:
        return {'schedule': [], 'status': 'NO_FORMATS', 'df_boosted': []}
    
    arena = validator['arenas'].get(center, 1.0)
    cap = Config.ARENA_CAPACITY.get(arena, 1)
    hours = [h for h in range(Config.OPEN_HOUR, Config.CLOSE_HOUR) if h < Config.DEAD_ZONE_START or h >= Config.DEAD_ZONE_END]
    
    model = cp_model.CpModel()
    x = {(d, h, f): model.NewBoolVar(f'x_{d}_{h}_{f}') for d in Config.DAYS for h in hours for f in formats}
    ym = {d: model.NewBoolVar(f'ym_{d}') for d in Config.DAYS}
    dfb = {d: model.NewBoolVar(f'dfb_{d}') for d in Config.DAYS}
    
    for d in Config.DAYS:
        for h in hours: model.Add(sum(x[(d, h, f)] for f in formats) <= cap)
    
    for d in Config.DAYS:
        for i, h in enumerate(hours[:-1]):
            if hours[i+1] == h + 1:
                for f in formats: model.Add(x[(d, h, f)] + x[(d, hours[i+1], f)] <= 1)
    
    hsb = [f for f in ['HRX', 'S&C', 'Burn'] if f in formats]
    if len(hsb) > 1:
        for d in Config.DAYS:
            for h in hours: model.Add(sum(x[(d, h, f)] for f in hsb) <= 1)
    
    if arena == 1.5:
        for d in Config.DAYS:
            for h in hours:
                hs = sum(x[(d, h, f)] for f in ['HRX', 'S&C'] if f in formats)
                for bb in ['Boxing', 'Burn']:
                    if bb in formats: model.Add(x[(d, h, bb)] <= hs)
                for solo in ['Yoga', 'DF']:
                    if solo in formats:
                        for other in [f for f in formats if f != solo]:
                            model.Add(x[(d, h, solo)] + x[(d, h, other)] <= 1)
    
    if 'Yoga' in formats:
        morn, eve = [h for h in hours if h <= 10], [h for h in hours if h >= 16]
        for d in Config.DAYS:
            for h in eve: model.Add(x[(d, h, 'Yoga')] == 0).OnlyEnforceIf(ym[d])
            for h in morn: model.Add(x[(d, h, 'Yoga')] == 0).OnlyEnforceIf(ym[d].Not())
        yoga_days = [d for d in Config.DAYS if not is_off(validator, center, 'Yoga', d)]
        for i in range(len(yoga_days) - 1): model.Add(ym[yoga_days[i]] + ym[yoga_days[i+1]] == 1)
        if yoga_pref == 'Morning' and 'Monday' in yoga_days: model.Add(ym['Monday'] == 1)
        elif yoga_pref == 'Evening' and 'Monday' in yoga_days: model.Add(ym['Monday'] == 0)
    
    for d in Config.DAYS:
        for f in formats:
            if is_off(validator, center, f, d):
                for h in hours: model.Add(x[(d, h, f)] == 0)
    
    if 'DF' in formats and arena in [1.0, 1.5]:
        model.Add(sum(dfb[d] for d in Config.DAYS) == 2)
        for i in range(7): model.Add(dfb[Config.DAYS[i]] + dfb[Config.DAYS[(i+1) % 7]] <= 1)
        for d in Config.DAYS:
            if is_off(validator, center, 'DF', d): model.Add(dfb[d] == 0)
    
    dm = Config.ARENA_DAILY_MAX.get(arena, Config.ARENA_DAILY_MAX[1.0])
    for d in Config.DAYS:
        if 'Yoga' in formats: model.Add(sum(x[(d, h, 'Yoga')] for h in hours) <= dm['Yoga'])
        if 'DF' in formats and arena in [1.0, 1.5]:
            df_cnt = sum(x[(d, h, 'DF')] for h in hours)
            if not is_off(validator, center, 'DF', d):
                model.Add(df_cnt == 3).OnlyEnforceIf(dfb[d])
                model.Add(df_cnt <= 2).OnlyEnforceIf(dfb[d].Not())
        hs_fmts = [f for f in ['HRX', 'S&C'] if f in formats]
        if dm['Boxing_Burn'] == 0 and 'Burn' in formats: hs_fmts.append('Burn')
        if hs_fmts: model.Add(sum(x[(d, h, f)] for h in hours for f in hs_fmts) <= dm['HRX_SC'])
    
    if 'HRX' in formats and 'S&C' in formats and arena in [1.0, 2.0]:
        t_hrx = sum(x[(d, h, 'HRX')] for d in Config.DAYS for h in hours)
        t_sc = sum(x[(d, h, 'S&C')] for d in Config.DAYS for h in hours)
        model.Add(t_sc * 100 >= t_hrx * 80); model.Add(t_sc * 100 <= t_hrx * 120)
        if 'Burn' in formats:
            t_burn = sum(x[(d, h, 'Burn')] for d in Config.DAYS for h in hours)
            model.Add(t_burn * 100 >= (t_hrx + t_sc) * 15); model.Add(t_burn * 100 <= (t_hrx + t_sc) * 30)
        for d in Config.DAYS:
            d_hrx, d_sc = sum(x[(d, h, 'HRX')] for h in hours), sum(x[(d, h, 'S&C')] for h in hours)
            model.Add(d_sc * 100 >= d_hrx * 60); model.Add(d_sc * 100 <= d_hrx * 160)
            if 'Burn' in formats:
                d_burn = sum(x[(d, h, 'Burn')] for h in hours)
                model.Add(d_burn <= d_sc); model.Add(d_burn <= d_hrx)
    
    peak = Config.PEAK_HOURS
    obj = []
    for d in Config.DAYS:
        for h in hours:
            for f in formats:
                u = get_score(intel, center, d, h, f)
                s = int((u ** 2) * 10) + (2000 if h in peak else 0)
                if u >= 70: s += 3000
                elif u >= 50: s += 1000
                obj.append(x[(d, h, f)] * s)
    model.Maximize(sum(obj))
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = Config.SOLVER_TIMEOUT
    solver.parameters.num_search_workers = 4
    status = solver.Solve(model)
    
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        schedule, boosted = [], []
        for d in Config.DAYS:
            if 'DF' in formats and arena in [1.0, 1.5] and solver.Value(dfb[d]) == 1: boosted.append(d)
            for h in hours:
                for f in formats:
                    if solver.Value(x[(d, h, f)]) == 1:
                        schedule.append({'center_name': center, 'day_of_week': Config.DAY_TO_NUM[d], 'day_name': d,
                                        'class_start_hour': h, 'format': f, 'predicted_utilisation': round(get_score(intel, center, d, h, f), 1), 'arena': arena})
        return {'schedule': schedule, 'status': 'OPTIMAL' if status == cp_model.OPTIMAL else 'FEASIBLE', 'df_boosted': boosted}
    return {'schedule': [], 'status': 'INFEASIBLE', 'df_boosted': []}

def create_excel(results):
    temp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    with pd.ExcelWriter(temp.name, engine='openpyxl') as writer:
        summary, all_sched = [], []
        for center, result in results.items():
            sched = result['schedule']; all_sched.extend(sched)
            if sched:
                df = pd.DataFrame(sched)
                summary.append({'Center': center, 'Status': result['status'], 'Classes': len(df),
                               'Avg Util %': round(df['predicted_utilisation'].mean(), 1), 'DF Boosted': ', '.join(result['df_boosted']) or '-'})
            else:
                summary.append({'Center': center, 'Status': result['status'], 'Classes': 0, 'Avg Util %': '-', 'DF Boosted': '-'})
        pd.DataFrame(summary).to_excel(writer, sheet_name='Summary', index=False)
        if all_sched:
            pd.DataFrame(all_sched).sort_values(['center_name', 'day_of_week', 'class_start_hour']).to_excel(writer, sheet_name='Full Schedule', index=False)
        for center, result in results.items():
            if result['schedule']:
                df = pd.DataFrame(result['schedule'])
                df['display'] = df['format'] + ' (' + df['predicted_utilisation'].astype(str) + '%)'
                pivot = df.pivot_table(index='class_start_hour', columns='day_name', values='display', aggfunc=lambda x: ' | '.join(x))
                cols = [d for d in Config.DAYS if d in pivot.columns]
                if cols: pivot[cols].to_excel(writer, sheet_name=center[:28].replace('/', '-').replace(',', ''))
    return temp.name

# =============================================================================
# FLASK ROUTES
# =============================================================================

@app.route('/')
@login_required
def index():
    user = get_current_user()
    return render_template_string(MAIN_PAGE, 
        user_name=user.get('name', 'User'), 
        user_initial=user.get('name', 'U')[0].upper(), 
        dev_mode=DEV_MODE,
        metabase_links=METABASE_LINKS
    )

@app.route('/login')
def login():
    if DEV_MODE: return redirect(url_for('index'))
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_CLIENT_ID}&redirect_uri={request.url_root}callback&response_type=code&scope=email%20profile&hd={ALLOWED_DOMAIN}"
    return render_template_string(LOGIN_PAGE, auth_url=auth_url, error=request.args.get('error'))

@app.route('/callback')
def callback():
    if DEV_MODE: return redirect(url_for('index'))
    code = request.args.get('code')
    if not code: return redirect(url_for('login', error='Authentication failed'))
    import requests
    token_response = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code, 'client_id': GOOGLE_CLIENT_ID, 'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': f"{request.url_root}callback", 'grant_type': 'authorization_code'
    })
    if token_response.status_code != 200: return redirect(url_for('login', error='Failed to get token'))
    access_token = token_response.json().get('access_token')
    user_response = requests.get('https://www.googleapis.com/oauth2/v2/userinfo', headers={'Authorization': f'Bearer {access_token}'})
    if user_response.status_code != 200: return redirect(url_for('login', error='Failed to get user info'))
    user_data = user_response.json()
    email = user_data.get('email', '')
    if not email.endswith(f'@{ALLOWED_DOMAIN}'): return redirect(url_for('login', error=f'Only @{ALLOWED_DOMAIN} accounts allowed'))
    session['user'] = {'email': email, 'name': user_data.get('name', email.split('@')[0])}
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/api/upload', methods=['POST'])
@login_required
def upload():
    try:
        center_df = pd.read_csv(request.files['center_file'])
        center_df['arena'] = pd.to_numeric(center_df['arena'], errors='coerce').fillna(1.0)
        trainer_df = pd.read_csv(request.files['trainer_file'])
        for col in ['format', 'trainer_name', 'home_center', 'weekly_off', 'gender']:
            trainer_df[col] = trainer_df[col].astype(str).str.strip()
        if 'home_center_2' in trainer_df.columns:
            trainer_df['home_center_2'] = trainer_df['home_center_2'].fillna('').astype(str).str.strip()
        trainer_df['weekly_off'] = trainer_df['weekly_off'].apply(lambda x: x if x in Config.DAYS else 'Sunday')
        historical_df = pd.read_csv(request.files['historical_file'])
        
        STATE['center_df'] = center_df
        STATE['trainer_df'] = trainer_df
        STATE['historical_df'] = historical_df
        STATE['intel'] = build_intelligence(historical_df)
        STATE['validator'] = build_validator(center_df, trainer_df)
        
        centers = []
        for _, row in center_df.iterrows():
            c = row['center_name']
            arena = row['arena']
            trainer_formats = detect_trainer_formats(c, trainer_df, arena)
            all_formats = get_all_arena_formats(arena)
            centers.append({
                'name': c, 
                'arena': arena, 
                'trainer_formats': trainer_formats,  # Formats with trainers
                'all_formats': all_formats  # All possible formats for this arena
            })
        STATE['center_info'] = {c['name']: c for c in centers}
        
        return jsonify({'success': True, 'summary': {'centers': len(center_df), 'trainers': len(trainer_df), 'records': len(historical_df)}, 'centers': centers})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/optimize', methods=['POST'])
@login_required
def optimize():
    try:
        data = request.json
        selected = data.get('centers', [])
        results, log = {}, []
        
        for item in selected:
            center = item['name']
            formats = item.get('formats', [])
            yoga_pref = item.get('yoga_pref', 'Optimizer')
            
            log.append(f"⏳ Optimizing {center} ({len(formats)} formats: {', '.join(formats)})...")
            result = optimize_center(center, formats, yoga_pref, STATE['intel'], STATE['validator'])
            results[center] = result
            
            if result['status'] in ['OPTIMAL', 'FEASIBLE']:
                log[-1] = f"✅ {center}: {result['status']} - {len(result['schedule'])} classes"
            else:
                log[-1] = f"❌ {center}: {result['status']}"
        
        STATE['output_file'] = create_excel(results)
        
        total_classes = sum(len(r['schedule']) for r in results.values())
        successful = sum(1 for r in results.values() if r['status'] in ['OPTIMAL', 'FEASIBLE'])
        all_utils = [s['predicted_utilisation'] for r in results.values() for s in r['schedule']]
        avg_util = round(np.mean(all_utils), 1) if all_utils else 0
        
        results_list = [{'center': c, 'status': r['status'], 'classes': len(r['schedule']),
                        'avg_util': f"{pd.DataFrame(r['schedule'])['predicted_utilisation'].mean():.1f}%" if r['schedule'] else '-',
                        'df_boosted': ', '.join(r['df_boosted']) if r['df_boosted'] else '-'} for c, r in results.items()]
        
        return jsonify({'success': True, 'log': log, 'summary': {'total': len(results), 'successful': successful, 'total_classes': total_classes, 'avg_util': avg_util}, 'results': results_list})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/download')
@login_required
def download():
    if STATE.get('output_file') and os.path.exists(STATE['output_file']):
        return send_file(STATE['output_file'], as_attachment=True, download_name=f'optimized_schedule_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx')
    return "No file available", 404

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print("🚀 Starting GX Schedule Optimizer...")
    print(f"📍 Open http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
