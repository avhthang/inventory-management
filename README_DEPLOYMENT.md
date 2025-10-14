# Inventory Management System - Production Deployment

## 🚀 Quick Start

### Option 1: Automated Deployment (Recommended)

```bash
# Clone the repository
git clone https://github.com/avhthang/inventory-management.git
cd inventory-management

# Make deployment script executable
chmod +x deploy.sh

# Run automated deployment
./deploy.sh
```

### Option 2: Docker Deployment

```bash
# Copy environment file
cp production.env .env

# Update .env with your configuration
nano .env

# Start with Docker Compose
docker-compose up -d
```

### Option 3: Manual Deployment

Follow the detailed steps in [DEPLOYMENT.md](DEPLOYMENT.md)

## 🔧 Configuration

### Environment Variables

Copy `production.env` to `.env` and update the following:

```bash
# Required
SECRET_KEY=your-super-secret-key-here
DATABASE_URL=postgresql://user:pass@host:port/db
ADMIN_PASSWORD=your-secure-admin-password

# Optional
BACKUP_S3_BUCKET=your-backup-bucket
AWS_ACCESS_KEY_ID=your-aws-key
AWS_SECRET_ACCESS_KEY=your-aws-secret
```

### Database Setup

#### PostgreSQL (Recommended)
```bash
# Using the setup script
python3 setup_postgres.py

# Or manually
createdb inventory_db
psql inventory_db < schema.sql
```

#### Migration from SQLite
```bash
# Backup current data
python3 backup_restore.py backup

# Setup PostgreSQL
python3 setup_postgres.py

# Migrate data
python3 migrate_to_postgres.py --confirm
```

## 🛠️ Management Commands

### Service Management
```bash
# Check status
sudo systemctl status inventory

# Restart service
sudo systemctl restart inventory

# View logs
sudo journalctl -u inventory -f

# Update application
./deploy.sh update
```

### Backup Management
```bash
# Create backup
python3 backup_restore.py backup

# Restore from backup
python3 backup_restore.py restore backup_file.zip

# S3 backup
python3 backup_restore.py backup-s3
```

### Database Management
```bash
# Initialize database
python3 init_database.py

# Create admin user
python3 -c "from app import app, db, User; from security import generate_secure_password; print('Admin password:', generate_secure_password())"
```

## 🔒 Security Features

- **Secure Password Generation**: Automatic generation of strong passwords
- **Environment-based Configuration**: No hardcoded secrets
- **Rate Limiting**: Protection against brute force attacks
- **Security Headers**: XSS, CSRF, and clickjacking protection
- **Input Sanitization**: Protection against injection attacks
- **Session Security**: Secure session management

## 📊 Monitoring

### Health Check
```bash
curl http://localhost:8000/health
```

### Logs
```bash
# Application logs
sudo journalctl -u inventory -f

# Nginx logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Performance
```bash
# Check service status
sudo systemctl status inventory nginx

# Check resource usage
htop
df -h
```

## 🔄 Updates

### Application Updates
```bash
cd /var/www/inventory-management
git pull origin main
sudo systemctl restart inventory
```

### Database Updates
```bash
# Backup first
python3 backup_restore.py backup

# Run migrations
python3 migrate_to_postgres.py --confirm

# Restart service
sudo systemctl restart inventory
```

## 🆘 Troubleshooting

### Common Issues

#### Service Won't Start
```bash
# Check logs
sudo journalctl -u inventory -f

# Check configuration
python3 -c "from app import app; print('Config OK')"

# Check database connection
python3 -c "from app import app, db; db.engine.execute('SELECT 1')"
```

#### Database Connection Issues
```bash
# Test database connection
psql $DATABASE_URL

# Check environment variables
env | grep DATABASE
```

#### Permission Issues
```bash
# Fix ownership
sudo chown -R $USER:$USER /var/www/inventory-management

# Fix permissions
chmod -R 755 /var/www/inventory-management
```

### Support

1. Check the logs: `sudo journalctl -u inventory -f`
2. Verify configuration: `python3 -c "from app import app; print('OK')"`
3. Test database: `python3 -c "from app import db; db.engine.execute('SELECT 1')"`
4. Check service status: `sudo systemctl status inventory`

## 📈 Scaling

### Horizontal Scaling
- Use load balancer (nginx, HAProxy)
- Multiple application instances
- Database read replicas

### Vertical Scaling
- Increase server resources
- Optimize database queries
- Enable caching (Redis)

## 🔐 Security Checklist

- [ ] Change default passwords
- [ ] Set strong SECRET_KEY
- [ ] Configure SSL/TLS
- [ ] Enable firewall
- [ ] Regular backups
- [ ] Monitor logs
- [ ] Update dependencies
- [ ] Test disaster recovery

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.