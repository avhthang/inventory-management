#!/bin/bash
# setup_ssl.sh - Complete Setup Script for Inventory Management System
# Handles Environment Setup, Secret Keys, and SSL Certificates

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Inventory Management System Setup ===${NC}"
echo ""

# Configuration
SSL_DIR="./ssl"
ENV_FILE=".env"
EXAMPLE_ENV_FILE=".env.example"

# --- 1. Environment Setup & Secret Key ---
echo -e "${YELLOW}Step 1: Checking Environment Configuration...${NC}"

if [ ! -f "$ENV_FILE" ]; then
    echo "Creating .env file from example..."
    if [ -f "$EXAMPLE_ENV_FILE" ]; then
        cp "$EXAMPLE_ENV_FILE" "$ENV_FILE"
    else
        echo -e "${RED}Error: .env.example not found!${NC}"
        # Create a basic .env if example is missing
        echo "FLASK_APP=app.py" > "$ENV_FILE"
        echo "FLASK_ENV=production" >> "$ENV_FILE"
    fi
else
    echo ".env file exists."
fi

# Check for SECRET_KEY
if grep -q "SECRET_KEY" "$ENV_FILE"; then
    CURRENT_KEY=$(grep "SECRET_KEY" "$ENV_FILE" | cut -d '=' -f2)
    if [ -z "$CURRENT_KEY" ] || [ "$CURRENT_KEY" = "your-secret-key-change-this" ]; then
        NEEDS_KEY=true
    else
        NEEDS_KEY=false
        echo "Valid SECRET_KEY found."
    fi
else
    NEEDS_KEY=true
fi

if [ "$NEEDS_KEY" = true ]; then
    echo "Generating new secure SECRET_KEY..."
    # Generate a random 64-character hex string
    NEW_KEY=$(openssl rand -hex 32)
    
    if grep -q "SECRET_KEY" "$ENV_FILE"; then
        # Replace existing but empty/default key
        # Use a temporary file to avoid issues with sed on different OS versions
        sed "s|SECRET_KEY=.*|SECRET_KEY=$NEW_KEY|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
    else
        # Append to end of file
        echo "" >> "$ENV_FILE"
        echo "SECRET_KEY=$NEW_KEY" >> "$ENV_FILE"
    fi
    echo -e "${GREEN}✓ New SECRET_KEY generated and saved to .env${NC}"
fi

# --- 2. SSL Certificate Setup ---
echo ""
echo -e "${YELLOW}Step 2: SSL Certificate Configuration${NC}"

mkdir -p "$SSL_DIR"

echo "Select SSL certificate type:"
echo "1) Generate Self-Signed Certificate (Development/Local)"
echo "2) Setup Let's Encrypt with Certbot (Production with Domain)"
echo "3) Skip (Use existing certificates)"
read -p "Enter choice [1-3]: " choice

case $choice in
    1)
        echo "Generating self-signed certificate..."
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$SSL_DIR/key.pem" \
            -out "$SSL_DIR/cert.pem" \
            -subj "/C=VN/ST=HCM/L=HCM/O=Inventory/CN=localhost"
        
        # Ensure Nginx config points to these
        if [ -f "nginx.conf" ]; then
            sed -i "s|ssl_certificate .*|ssl_certificate /etc/nginx/ssl/cert.pem;|" nginx.conf
            sed -i "s|ssl_certificate_key .*|ssl_certificate_key /etc/nginx/ssl/key.pem;|" nginx.conf
        fi
        
        echo -e "${GREEN}✓ Self-signed certificate generated in $SSL_DIR${NC}"
        echo -e "${YELLOW}Note: Accept the security warning in your browser when accessing https://localhost${NC}"
        ;;
        
    2)
        echo "Setting up Let's Encrypt..."
        read -p "Enter your domain name (e.g., mysite.com): " DOMAIN
        read -p "Enter your email for renewal: " EMAIL
        
        if [ -z "$DOMAIN" ]; then
            echo -e "${RED}Error: Domain is required.${NC}"
            exit 1
        fi

        # Check for Certbot
        if ! command -v certbot &> /dev/null; then
            echo "Certbot not found. Attempting to install..."
            if command -v apt-get &> /dev/null; then
                sudo apt-get update && sudo apt-get install -y certbot
            elif command -v yum &> /dev/null; then
                sudo yum install -y certbot
            else
                echo -e "${RED}Please install certbot manually and run this script again.${NC}"
                exit 1
            fi
        fi

        # Stop Nginx if running to free port 80
        echo "Stopping Nginx containers to free port 80..."
        docker-compose stop nginx || true

        echo "Obtaining certificate..."
        sudo certbot certonly --standalone -d "$DOMAIN" --prefer-challenges http \
             --agree-tos --email "$EMAIL" --non-interactive

        if [ $? -eq 0 ]; then
            # Copy/Link certs to local ssl dir or update nginx conf to map them
            # Approach: Update nginx.conf to use the /etc/letsencrypt path
            # AND ensure docker-compose maps /etc/letsencrypt
            
            echo -e "${GREEN}✓ Certificate obtained successfully!${NC}"
            
            # Update nginx.conf server_name
            sed -i "s|server_name _;|server_name $DOMAIN;|" nginx.conf
            
            # NOTE: For Docker, we need to map the Let's Encrypt host folder to the container.
            # But complicating docker-compose programmatically is risky.
            # SIMPLER APPROACH: Copy the certs to our ./ssl folder so the existing docker mapping works.
            
            echo "Copying certificates to $SSL_DIR for Docker usage..."
            sudo cp "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" "$SSL_DIR/cert.pem"
            sudo cp "/etc/letsencrypt/live/$DOMAIN/privkey.pem" "$SSL_DIR/key.pem"
            
            # Update permissions so current user can read them
            sudo chown -R $USER:$USER "$SSL_DIR"
            
            echo -e "${GREEN}✓ Certificates installed to $SSL_DIR${NC}"
            echo -e "${YELLOW}Note: You will need to re-run this copy step when certificates renew (every ~90 days).${NC}"
        else
            echo -e "${RED}Failed to obtain certificate.${NC}"
            exit 1
        fi
        ;;
        
    3)
        echo "Skipping SSL generation."
        ;;
    *)
        echo "Invalid choice."
        ;;
esac

# --- 3. Finalize ---
echo ""
echo -e "${GREEN}=== Setup Complete ===${NC}"
echo "To apply changes, run:"
echo "  docker-compose down"
echo "  docker-compose up -d --build"
echo ""
