#!/bin/sh
# Script to initialize SSL certificates if they don't exist
# This is used in Docker containers to ensure SSL certificates are available

# Install openssl if not found
if ! command -v openssl >/dev/null 2>&1; then
    echo "openssl not found. Installing..."
    apk add --no-cache openssl
fi

SSL_DIR="/etc/nginx/ssl"
CERT_FILE="$SSL_DIR/cert.pem"
KEY_FILE="$SSL_DIR/key.pem"

# Create SSL directory if it doesn't exist
mkdir -p "$SSL_DIR"

# Check if certificate files exist
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "SSL certificates not found. Generating self-signed certificate..."
    
    # Generate private key
    openssl genrsa -out "$KEY_FILE" 2048
    if [ $? -ne 0 ]; then
        echo "Error generating private key"
        exit 1
    fi
    
    # Generate certificate signing request
    openssl req -new -key "$KEY_FILE" -out "$SSL_DIR/cert.csr" \
        -subj "/C=VN/ST=HoChiMinh/L=HoChiMinh/O=Inventory Management/CN=localhost"
    if [ $? -ne 0 ]; then
        echo "Error generating CSR"
        exit 1
    fi
    
    # Generate self-signed certificate (valid for 365 days)
    openssl x509 -req -days 365 -in "$SSL_DIR/cert.csr" \
        -signkey "$KEY_FILE" -out "$CERT_FILE"
    if [ $? -ne 0 ]; then
        echo "Error generating certificate"
        exit 1
    fi
    
    # Clean up CSR file
    rm -f "$SSL_DIR/cert.csr"
    
    echo "Self-signed SSL certificate generated successfully!"
    echo "Note: For production, replace these with proper certificates from Let's Encrypt or your CA."
else
    echo "SSL certificates already exist."
fi
