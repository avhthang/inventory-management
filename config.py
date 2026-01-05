"""
Configuration management for the inventory management system
Supports multiple database backends and environment-based configuration
"""
import os
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Base configuration class"""
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        if os.environ.get('FLASK_ENV') == 'production':
            raise ValueError("SECRET_KEY must be set in production environment")
        else:
            # Generate a random key for development
            from security import generate_secret_key
            SECRET_KEY = generate_secret_key()
            print(f"WARNING: Using generated SECRET_KEY for development: {SECRET_KEY}")
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Backup configuration
    BACKUP_ENABLED = os.environ.get('BACKUP_ENABLED', 'False').lower() == 'true'
    BACKUP_S3_BUCKET = os.environ.get('BACKUP_S3_BUCKET')
    BACKUP_S3_REGION = os.environ.get('BACKUP_S3_REGION', 'us-east-1')
    
    # AWS credentials for backup
    AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    
    # Email configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    @staticmethod
    def init_app(app):
        pass

class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///inventory.db'

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    
    # HTTPS/Proxy configuration
    # Trust proxy headers (X-Forwarded-Proto, X-Forwarded-For, etc.)
    # This is important when running behind nginx reverse proxy
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')
    
    @classmethod
    def init_app(cls, app):
        Config.init_app(app)
        
        # Configure Flask to trust proxy headers
        # This allows Flask to correctly detect HTTPS when behind nginx
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_proto=1,  # Trust X-Forwarded-Proto header
            x_host=1,   # Trust X-Forwarded-Host header
            x_port=1,   # Trust X-Forwarded-Port header
            x_for=1     # Trust X-Forwarded-For header
        )
        
        # Log to syslog
        import logging
        from logging.handlers import SysLogHandler
        syslog_handler = SysLogHandler()
        syslog_handler.setLevel(logging.WARNING)
        app.logger.addHandler(syslog_handler)

class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///:memory:'

# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

def get_database_info():
    """Extract database information from DATABASE_URL"""
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///inventory.db')
    
    if database_url.startswith('sqlite'):
        return {
            'type': 'sqlite',
            'file': database_url.replace('sqlite:///', ''),
            'host': None,
            'port': None,
            'database': None,
            'username': None
        }
    
    try:
        parsed = urlparse(database_url)
        return {
            'type': parsed.scheme,
            'host': parsed.hostname,
            'port': parsed.port,
            'database': parsed.path[1:],  # Remove leading slash
            'username': parsed.username,
            'password': parsed.password
        }
    except Exception as e:
        print(f"Error parsing database URL: {e}")
        return {
            'type': 'sqlite',
            'file': 'inventory.db',
            'host': None,
            'port': None,
            'database': None,
            'username': None
        }

def is_external_database():
    """Check if using external database (not SQLite)"""
    db_info = get_database_info()
    return db_info['type'] != 'sqlite'