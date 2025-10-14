#!/bin/bash

# Inventory Management System - Automated Deployment Script
# Similar to Vercel's deployment process

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="inventory-management"
APP_DIR="/var/www/$APP_NAME"
VENV_DIR="$APP_DIR/venv"
SERVICE_NAME="inventory"
NGINX_CONFIG="/etc/nginx/sites-available/$APP_NAME"
NGINX_ENABLED="/etc/nginx/sites-enabled/$APP_NAME"
SYSTEMD_SERVICE="/etc/systemd/system/$SERVICE_NAME.service"

# Functions
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}✅ $1${NC}"
}

warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

error() {
    echo -e "${RED}❌ $1${NC}"
    exit 1
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        error "This script should not be run as root. Please run as a regular user with sudo privileges."
    fi
}

check_dependencies() {
    log "Checking system dependencies..."
    
    # Check if required commands exist
    local missing_deps=()
    
    if ! command -v python3 &> /dev/null; then
        missing_deps+=("python3")
    fi
    
    if ! command -v pip3 &> /dev/null; then
        missing_deps+=("pip3")
    fi
    
    if ! command -v nginx &> /dev/null; then
        missing_deps+=("nginx")
    fi
    
    if ! command -v systemctl &> /dev/null; then
        missing_deps+=("systemd")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        error "Missing dependencies: ${missing_deps[*]}. Please install them first."
    fi
    
    success "All dependencies found"
}

install_system_dependencies() {
    log "Installing system dependencies..."
    
    sudo apt-get update
    sudo apt-get install -y \
        python3 \
        python3-pip \
        python3-venv \
        nginx \
        postgresql-client \
        git \
        curl \
        unzip \
        supervisor
    
    success "System dependencies installed"
}

setup_application_directory() {
    log "Setting up application directory..."
    
    # Create application directory
    sudo mkdir -p $APP_DIR
    sudo chown $USER:$USER $APP_DIR
    
    # Clone or update repository
    if [ -d "$APP_DIR/.git" ]; then
        log "Updating existing repository..."
        cd $APP_DIR
        git pull origin main
    else
        log "Cloning repository..."
        git clone https://github.com/avhthang/inventory-management.git $APP_DIR
        cd $APP_DIR
    fi
    
    success "Application directory ready"
}

setup_python_environment() {
    log "Setting up Python virtual environment..."
    
    cd $APP_DIR
    
    # Create virtual environment
    python3 -m venv $VENV_DIR
    
    # Activate virtual environment
    source $VENV_DIR/bin/activate
    
    # Upgrade pip
    pip install --upgrade pip
    
    # Install dependencies
    pip install -r requirements.txt
    
    success "Python environment ready"
}

setup_environment_config() {
    log "Setting up environment configuration..."
    
    cd $APP_DIR
    
    # Create .env file if it doesn't exist
    if [ ! -f ".env" ]; then
        log "Creating .env file from template..."
        cp .env.example .env
        
        # Generate secure secret key
        SECRET_KEY=$(python3 -c "from security import generate_secret_key; print(generate_secret_key())")
        ADMIN_PASSWORD=$(python3 -c "from security import generate_secure_password; print(generate_secure_password())")
        
        # Update .env file
        sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env
        sed -i "s/ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$ADMIN_PASSWORD/" .env
        sed -i "s/FLASK_ENV=.*/FLASK_ENV=production/" .env
        
        warning "Generated SECRET_KEY and ADMIN_PASSWORD. Please update .env file with your database configuration."
    else
        log ".env file already exists, skipping generation"
    fi
    
    success "Environment configuration ready"
}

setup_database() {
    log "Setting up database..."
    
    cd $APP_DIR
    source $VENV_DIR/bin/activate
    
    # Check if DATABASE_URL is set
    if [ -z "$DATABASE_URL" ]; then
        warning "DATABASE_URL not set. Using SQLite for development."
        export DATABASE_URL="sqlite:///inventory.db"
    fi
    
    # Initialize database
    python3 init_database.py
    
    success "Database initialized"
}

setup_nginx() {
    log "Setting up Nginx configuration..."
    
    # Create Nginx configuration
    sudo tee $NGINX_CONFIG > /dev/null <<EOF
server {
    listen 80;
    server_name _;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
    
    location /static {
        alias $APP_DIR/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "no-referrer-when-downgrade" always;
    add_header Content-Security-Policy "default-src 'self' http: https: data: blob: 'unsafe-inline'" always;
}
EOF
    
    # Enable site
    sudo ln -sf $NGINX_CONFIG $NGINX_ENABLED
    
    # Test Nginx configuration
    sudo nginx -t
    
    # Reload Nginx
    sudo systemctl reload nginx
    
    success "Nginx configured"
}

setup_systemd_service() {
    log "Setting up systemd service..."
    
    # Create systemd service file
    sudo tee $SYSTEMD_SERVICE > /dev/null <<EOF
[Unit]
Description=Inventory Management System
After=network.target

[Service]
Type=exec
User=$USER
Group=$USER
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_DIR/bin
Environment=DATABASE_URL=\${DATABASE_URL:-sqlite:///inventory.db}
Environment=FLASK_ENV=production
ExecStart=$VENV_DIR/bin/gunicorn --workers 4 --bind 127.0.0.1:8000 app:app
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
    
    # Reload systemd and enable service
    sudo systemctl daemon-reload
    sudo systemctl enable $SERVICE_NAME
    
    success "Systemd service configured"
}

start_services() {
    log "Starting services..."
    
    # Start the application service
    sudo systemctl start $SERVICE_NAME
    
    # Check if service is running
    if sudo systemctl is-active --quiet $SERVICE_NAME; then
        success "Application service started"
    else
        error "Failed to start application service"
    fi
    
    # Check if Nginx is running
    if sudo systemctl is-active --quiet nginx; then
        success "Nginx is running"
    else
        warning "Nginx is not running, starting it..."
        sudo systemctl start nginx
    fi
}

setup_ssl() {
    log "Setting up SSL with Let's Encrypt..."
    
    # Check if certbot is installed
    if ! command -v certbot &> /dev/null; then
        log "Installing certbot..."
        sudo apt-get install -y certbot python3-certbot-nginx
    fi
    
    # Get domain name from user
    read -p "Enter your domain name (or press Enter to skip SSL): " DOMAIN
    
    if [ -n "$DOMAIN" ]; then
        # Update Nginx configuration with domain
        sudo sed -i "s/server_name _;/server_name $DOMAIN;/" $NGINX_CONFIG
        sudo nginx -t && sudo systemctl reload nginx
        
        # Get SSL certificate
        sudo certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN
        
        success "SSL certificate installed for $DOMAIN"
    else
        warning "Skipping SSL setup"
    fi
}

setup_backup() {
    log "Setting up automated backup..."
    
    cd $APP_DIR
    
    # Create backup script
    cat > backup.sh << 'EOF'
#!/bin/bash
# Automated backup script

APP_DIR="/var/www/inventory-management"
BACKUP_DIR="/var/backups/inventory"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

cd $APP_DIR
source venv/bin/activate

# Create backup
python3 backup_restore.py backup $BACKUP_DIR/backup_$DATE.zip

# Upload to S3 if configured
if [ -n "$BACKUP_S3_BUCKET" ]; then
    python3 backup_restore.py backup-s3 $BACKUP_DIR/backup_$DATE.zip
fi

# Cleanup old backups (keep last 7 days)
find $BACKUP_DIR -name "backup_*.zip" -mtime +7 -delete

echo "Backup completed: backup_$DATE.zip"
EOF
    
    chmod +x backup.sh
    
    # Add to crontab
    (crontab -l 2>/dev/null; echo "0 2 * * * $APP_DIR/backup.sh") | crontab -
    
    success "Automated backup configured"
}

show_status() {
    log "Deployment Status:"
    echo "=================="
    
    # Check service status
    if sudo systemctl is-active --quiet $SERVICE_NAME; then
        success "Application service: Running"
    else
        error "Application service: Not running"
    fi
    
    # Check Nginx status
    if sudo systemctl is-active --quiet nginx; then
        success "Nginx: Running"
    else
        warning "Nginx: Not running"
    fi
    
    # Show application info
    echo ""
    echo "Application Information:"
    echo "======================="
    echo "Application Directory: $APP_DIR"
    echo "Service Name: $SERVICE_NAME"
    echo "Nginx Config: $NGINX_CONFIG"
    echo "Service Config: $SYSTEMD_SERVICE"
    echo ""
    echo "Useful Commands:"
    echo "==============="
    echo "sudo systemctl status $SERVICE_NAME    # Check service status"
    echo "sudo systemctl restart $SERVICE_NAME   # Restart service"
    echo "sudo journalctl -u $SERVICE_NAME -f    # View logs"
    echo "sudo nginx -t                          # Test Nginx config"
    echo "sudo systemctl reload nginx            # Reload Nginx"
    echo ""
    echo "To update the application:"
    echo "cd $APP_DIR && git pull && sudo systemctl restart $SERVICE_NAME"
}

# Main deployment function
main() {
    log "Starting deployment of $APP_NAME..."
    
    check_root
    check_dependencies
    install_system_dependencies
    setup_application_directory
    setup_python_environment
    setup_environment_config
    setup_database
    setup_nginx
    setup_systemd_service
    start_services
    
    # Optional steps
    read -p "Do you want to setup SSL? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_ssl
    fi
    
    read -p "Do you want to setup automated backup? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        setup_backup
    fi
    
    show_status
    
    success "Deployment completed successfully!"
    log "Your application should be accessible at http://$(curl -s ifconfig.me) or your domain name"
}

# Handle command line arguments
case "${1:-}" in
    "update")
        log "Updating application..."
        cd $APP_DIR
        git pull origin main
        source $VENV_DIR/bin/activate
        pip install -r requirements.txt
        sudo systemctl restart $SERVICE_NAME
        success "Application updated"
        ;;
    "restart")
        log "Restarting services..."
        sudo systemctl restart $SERVICE_NAME
        sudo systemctl reload nginx
        success "Services restarted"
        ;;
    "logs")
        sudo journalctl -u $SERVICE_NAME -f
        ;;
    "status")
        show_status
        ;;
    *)
        main
        ;;
esac