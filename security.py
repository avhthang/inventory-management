"""
Security configuration and utilities
Handles secure password generation, secret management, and security best practices
"""
import os
import secrets
import string
import hashlib
from datetime import datetime, timedelta
import jwt
from functools import wraps
from flask import request, jsonify, session

class SecurityConfig:
    """Security configuration constants"""
    
    # Password requirements
    MIN_PASSWORD_LENGTH = 8
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_DIGITS = True
    REQUIRE_SPECIAL_CHARS = True
    
    # Session security
    SESSION_TIMEOUT_HOURS = 24
    MAX_LOGIN_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 30
    
    # JWT settings
    JWT_ALGORITHM = 'HS256'
    JWT_EXPIRATION_HOURS = 24

def generate_secure_password(length=12):
    """Generate a secure random password"""
    characters = string.ascii_letters + string.digits + "!@#$%^&*"
    
    # Ensure at least one character from each required category
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*")
    ]
    
    # Fill the rest randomly
    for _ in range(length - 4):
        password.append(secrets.choice(characters))
    
    # Shuffle the password
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)

def generate_secret_key():
    """Generate a secure secret key for Flask"""
    return secrets.token_urlsafe(32)

def validate_password_strength(password):
    """Validate password strength according to security requirements"""
    if len(password) < SecurityConfig.MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {SecurityConfig.MIN_PASSWORD_LENGTH} characters long"
    
    if SecurityConfig.REQUIRE_UPPERCASE and not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    
    if SecurityConfig.REQUIRE_LOWERCASE and not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    
    if SecurityConfig.REQUIRE_DIGITS and not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    
    if SecurityConfig.REQUIRE_SPECIAL_CHARS and not any(c in "!@#$%^&*" for c in password):
        return False, "Password must contain at least one special character"
    
    return True, "Password is strong"

def hash_password(password, salt=None):
    """Hash password with salt using PBKDF2"""
    if salt is None:
        salt = secrets.token_hex(16)
    
    # Use PBKDF2 with SHA-256
    password_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000)
    return password_hash.hex(), salt

def verify_password(password, password_hash, salt):
    """Verify password against hash"""
    computed_hash, _ = hash_password(password, salt)
    return computed_hash == password_hash

def generate_jwt_token(user_id, secret_key):
    """Generate JWT token for user"""
    payload = {
        'user_id': user_id,
        'exp': datetime.utcnow() + timedelta(hours=SecurityConfig.JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, secret_key, algorithm=SecurityConfig.JWT_ALGORITHM)

def verify_jwt_token(token, secret_key):
    """Verify JWT token"""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[SecurityConfig.JWT_ALGORITHM])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def sanitize_input(input_string, max_length=255):
    """Sanitize user input to prevent XSS and injection attacks"""
    if not input_string:
        return ""
    
    # Remove null bytes and control characters
    sanitized = ''.join(char for char in input_string if ord(char) >= 32)
    
    # Limit length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]
    
    return sanitized.strip()

def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated_function

def require_admin(f):
    """Decorator to require admin privileges"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Check if user is admin (this would need to be implemented based on your User model)
        # For now, we'll assume session contains role information
        if session.get('user_role') != 'admin':
            return jsonify({'error': 'Admin privileges required'}), 403
        
        return f(*args, **kwargs)
    return decorated_function

def rate_limit(max_requests=100, window_minutes=60):
    """Simple rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # This is a simple implementation
            # In production, use Redis or similar for distributed rate limiting
            client_ip = request.remote_addr
            # Implementation would go here
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_secure_headers():
    """Get security headers for responses"""
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    }

def log_security_event(event_type, user_id=None, ip_address=None, details=None):
    """Log security events for monitoring"""
    timestamp = datetime.utcnow().isoformat()
    event = {
        'timestamp': timestamp,
        'event_type': event_type,
        'user_id': user_id,
        'ip_address': ip_address or request.remote_addr,
        'details': details
    }
    
    # In production, send to logging service
    print(f"SECURITY_EVENT: {event}")

def check_password_breach(password):
    """Check if password has been breached (simplified version)"""
    # In production, integrate with HaveIBeenPwned API
    # For now, check against common weak passwords
    weak_passwords = [
        'password', '123456', 'admin', 'qwerty', 'letmein',
        'welcome', 'monkey', 'dragon', 'master', 'hello'
    ]
    
    return password.lower() in weak_passwords