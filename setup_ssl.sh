#!/bin/bash
# Script to set up SSL certificates for HTTPS support
# This script can generate self-signed certificates for development/testing
# or help configure Let's Encrypt certificates for production

set -e

SSL_DIR="./ssl"
NGINX_SSL_DIR="/etc/nginx/ssl"

echo "=== SSL Certificate Setup ==="
echo ""

# Check if running in Docker or on host
if [ -f /.dockerenv ] || [ -n "$DOCKER_CONTAINER" ]; then
    USE_DOCKER=true
    SSL_TARGET_DIR="$SSL_DIR"
else
    USE_DOCKER=false
    SSL_TARGET_DIR="$NGINX_SSL_DIR"
fi

# Function to generate self-signed certificate
generate_self_signed() {
    echo "Generating self-signed SSL certificate..."
    
    if [ "$USE_DOCKER" = true ]; then
        mkdir -p "$SSL_TARGET_DIR"
    else
        sudo mkdir -p "$SSL_TARGET_DIR"
    fi
    
    # Generate private key
    if [ "$USE_DOCKER" = true ]; then
        openssl genrsa -out "$SSL_TARGET_DIR/key.pem" 2048
    else
        sudo openssl genrsa -out "$SSL_TARGET_DIR/key.pem" 2048
    fi
    
    # Generate certificate signing request
    if [ "$USE_DOCKER" = true ]; then
        openssl req -new -key "$SSL_TARGET_DIR/key.pem" -out "$SSL_TARGET_DIR/cert.csr" \
            -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"
    else
        sudo openssl req -new -key "$SSL_TARGET_DIR/key.pem" -out "$SSL_TARGET_DIR/cert.csr" \
            -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"
    fi
    
    # Generate self-signed certificate (valid for 365 days)
    if [ "$USE_DOCKER" = true ]; then
        openssl x509 -req -days 365 -in "$SSL_TARGET_DIR/cert.csr" \
            -signkey "$SSL_TARGET_DIR/key.pem" -out "$SSL_TARGET_DIR/cert.pem"
        rm "$SSL_TARGET_DIR/cert.csr"
    else
        sudo openssl x509 -req -days 365 -in "$SSL_TARGET_DIR/cert.csr" \
            -signkey "$SSL_TARGET_DIR/key.pem" -out "$SSL_TARGET_DIR/cert.pem"
        sudo rm "$SSL_TARGET_DIR/cert.csr"
    fi
    
    echo "✓ Self-signed certificate generated successfully!"
    echo "  Certificate: $SSL_TARGET_DIR/cert.pem"
    echo "  Private Key: $SSL_TARGET_DIR/key.pem"
    echo ""
    echo "⚠️  WARNING: Self-signed certificates are for development/testing only!"
    echo "   Browsers will show a security warning. For production, use Let's Encrypt."
}

# Function to setup Let's Encrypt certificate
setup_letsencrypt() {
    echo "Setting up Let's Encrypt certificate..."
    
    read -p "Enter your domain name: " DOMAIN
    
    if [ -z "$DOMAIN" ]; then
        echo "Error: Domain name is required"
        exit 1
    fi
    
    # Check if certbot is installed
    if ! command -v certbot &> /dev/null; then
        echo "Installing certbot..."
        if command -v apt-get &> /dev/null; then
            sudo apt-get update
            sudo apt-get install -y certbot python3-certbot-nginx
        elif command -v yum &> /dev/null; then
            sudo yum install -y certbot python3-certbot-nginx
        else
            echo "Error: Cannot install certbot. Please install it manually."
            exit 1
        fi
    fi
    
    # Update nginx.conf with domain name (if using deploy.sh)
    if [ -f "nginx.conf" ]; then
        echo "Updating nginx.conf with domain name..."
        # Note: This is a simple update. For production, you may want to use certbot's nginx plugin
        sed -i "s/server_name _;/server_name $DOMAIN;/" nginx.conf 2>/dev/null || true
    fi
    
    # Get certificate using certbot
    echo "Obtaining SSL certificate from Let's Encrypt..."
    sudo certbot certonly --standalone -d "$DOMAIN" --non-interactive --agree-tos \
        --email "admin@$DOMAIN" || {
        echo "Error: Failed to obtain certificate. Make sure:"
        echo "  1. Domain points to this server"
        echo "  2. Port 80 is accessible"
        echo "  3. Firewall allows connections on port 80"
        exit 1
    }
    
    # Update nginx.conf to use Let's Encrypt certificates
    CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
    
    if [ -f "nginx.conf" ]; then
        echo "Updating nginx.conf with Let's Encrypt certificate paths..."
        sed -i "s|ssl_certificate /etc/nginx/ssl/cert.pem;|ssl_certificate $CERT_PATH;|" nginx.conf
        sed -i "s|ssl_certificate_key /etc/nginx/ssl/key.pem;|ssl_certificate_key $KEY_PATH;|" nginx.conf
    fi
    
    echo "✓ Let's Encrypt certificate installed successfully!"
    echo "  Certificate: $CERT_PATH"
    echo "  Private Key: $KEY_PATH"
    echo ""
    echo "Note: Certificates will auto-renew. Set up a cron job if needed:"
    echo "  sudo certbot renew --dry-run"
}

# Main menu
echo "Select SSL certificate type:"
echo "1) Generate self-signed certificate (for development/testing)"
echo "2) Setup Let's Encrypt certificate (for production)"
echo "3) Use existing certificates"
read -p "Enter choice [1-3]: " choice

case $choice in
    1)
        generate_self_signed
        ;;
    2)
        setup_letsencrypt
        ;;
    3)
        read -p "Enter path to certificate file: " CERT_PATH
        read -p "Enter path to private key file: " KEY_PATH
        
        if [ ! -f "$CERT_PATH" ] || [ ! -f "$KEY_PATH" ]; then
            echo "Error: Certificate or key file not found"
            exit 1
        fi
        
        if [ -f "nginx.conf" ]; then
            sed -i "s|ssl_certificate /etc/nginx/ssl/cert.pem;|ssl_certificate $CERT_PATH;|" nginx.conf
            sed -i "s|ssl_certificate_key /etc/nginx/ssl/key.pem;|ssl_certificate_key $KEY_PATH;|" nginx.conf
            echo "✓ nginx.conf updated with your certificate paths"
        fi
        ;;
    *)
        echo "Invalid choice"
        exit 1
        ;;
esac

echo ""
echo "Next steps:"
echo "1. Restart nginx: sudo systemctl restart nginx (or docker-compose restart nginx)"
echo "2. Test HTTPS: https://your-domain or https://localhost"
echo "3. For production, consider setting up HTTP to HTTPS redirect in nginx.conf"

