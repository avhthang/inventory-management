#!/usr/bin/env python3
"""
Backup and restore utilities for the inventory management system
Supports both SQLite and PostgreSQL databases
"""
import os
import sys
import json
import zipfile
import tempfile
import shutil
from datetime import datetime
import subprocess
import boto3
from botocore.exceptions import ClientError
from config import get_database_info, is_external_database

class DatabaseBackup:
    def __init__(self):
        self.db_info = get_database_info()
        self.is_external = is_external_database()
        
    def create_backup(self, backup_path=None):
        """Create a backup of the database"""
        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"backup_{timestamp}.zip"
        
        print(f"Creating backup: {backup_path}")
        
        with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if self.is_external:
                # For external databases, export data as SQL dump
                self._backup_external_db(zipf)
            else:
                # For SQLite, copy the database file
                self._backup_sqlite(zipf)
            
            # Add configuration files
            self._backup_config_files(zipf)
            
            # Add metadata
            metadata = {
                'backup_date': datetime.now().isoformat(),
                'database_type': self.db_info['type'],
                'database_info': self.db_info,
                'version': '1.0'
            }
            zipf.writestr('backup_metadata.json', json.dumps(metadata, indent=2))
        
        print(f"✅ Backup created successfully: {backup_path}")
        return backup_path
    
    def _backup_sqlite(self, zipf):
        """Backup SQLite database"""
        db_file = self.db_info['file']
        if os.path.exists(db_file):
            zipf.write(db_file, 'database/inventory.db')
            print(f"  Added SQLite database: {db_file}")
        else:
            print(f"  Warning: SQLite database not found at {db_file}")
    
    def _backup_external_db(self, zipf):
        """Backup external database (PostgreSQL/MySQL)"""
        try:
            if self.db_info['type'] == 'postgresql':
                dump_file = 'database/postgres_dump.sql'
                cmd = [
                    'pg_dump',
                    f"--host={self.db_info['host']}",
                    f"--port={self.db_info['port']}",
                    f"--username={self.db_info['username']}",
                    f"--dbname={self.db_info['database']}",
                    '--no-password',
                    '--clean',
                    '--if-exists'
                ]
            elif self.db_info['type'] == 'mysql':
                dump_file = 'database/mysql_dump.sql'
                cmd = [
                    'mysqldump',
                    f"--host={self.db_info['host']}",
                    f"--port={self.db_info['port']}",
                    f"--user={self.db_info['username']}",
                    f"--password={self.db_info['password']}",
                    '--single-transaction',
                    '--routines',
                    '--triggers',
                    self.db_info['database']
                ]
            else:
                print(f"  Unsupported database type: {self.db_info['type']}")
                return
            
            # Set password environment variable
            env = os.environ.copy()
            if self.db_info['type'] == 'postgresql':
                env['PGPASSWORD'] = self.db_info['password']
            
            # Run dump command
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as tmp_file:
                result = subprocess.run(cmd, stdout=tmp_file, stderr=subprocess.PIPE, env=env)
                
                if result.returncode == 0:
                    zipf.write(tmp_file.name, dump_file)
                    print(f"  Added {self.db_info['type']} dump: {dump_file}")
                else:
                    print(f"  Error creating dump: {result.stderr.decode()}")
                
                os.unlink(tmp_file.name)
                
        except Exception as e:
            print(f"  Error backing up external database: {e}")
    
    def _backup_config_files(self, zipf):
        """Backup configuration files"""
        config_files = [
            '.env',
            'config.py',
            'requirements.txt',
            'app.py'
        ]
        
        for file_path in config_files:
            if os.path.exists(file_path):
                zipf.write(file_path, f'config/{file_path}')
                print(f"  Added config file: {file_path}")
    
    def restore_backup(self, backup_path):
        """Restore from backup"""
        if not os.path.exists(backup_path):
            print(f"❌ Backup file not found: {backup_path}")
            return False
        
        print(f"Restoring from backup: {backup_path}")
        
        with zipfile.ZipFile(backup_path, 'r') as zipf:
            # Read metadata
            try:
                metadata_str = zipf.read('backup_metadata.json').decode()
                metadata = json.loads(metadata_str)
                print(f"  Backup date: {metadata['backup_date']}")
                print(f"  Database type: {metadata['database_type']}")
            except:
                print("  Warning: Could not read backup metadata")
            
            # Restore database
            if self.is_external:
                self._restore_external_db(zipf)
            else:
                self._restore_sqlite(zipf)
            
            # Restore config files
            self._restore_config_files(zipf)
        
        print("✅ Restore completed successfully")
        return True
    
    def _restore_sqlite(self, zipf):
        """Restore SQLite database"""
        try:
            # Extract database file
            zipf.extract('database/inventory.db', '/tmp')
            
            # Copy to instance directory
            instance_dir = os.path.join(os.getcwd(), 'instance')
            os.makedirs(instance_dir, exist_ok=True)
            
            shutil.copy('/tmp/database/inventory.db', os.path.join(instance_dir, 'inventory.db'))
            print("  Restored SQLite database")
            
        except Exception as e:
            print(f"  Error restoring SQLite database: {e}")
    
    def _restore_external_db(self, zipf):
        """Restore external database"""
        try:
            if self.db_info['type'] == 'postgresql':
                dump_file = 'database/postgres_dump.sql'
                cmd = [
                    'psql',
                    f"--host={self.db_info['host']}",
                    f"--port={self.db_info['port']}",
                    f"--username={self.db_info['username']}",
                    f"--dbname={self.db_info['database']}",
                    '--no-password'
                ]
            elif self.db_info['type'] == 'mysql':
                dump_file = 'database/mysql_dump.sql'
                cmd = [
                    'mysql',
                    f"--host={self.db_info['host']}",
                    f"--port={self.db_info['port']}",
                    f"--user={self.db_info['username']}",
                    f"--password={self.db_info['password']}",
                    self.db_info['database']
                ]
            else:
                print(f"  Unsupported database type: {self.db_info['type']}")
                return
            
            # Extract dump file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sql', delete=False) as tmp_file:
                zipf.extract(dump_file, '/tmp')
                shutil.copy(f'/tmp/{dump_file}', tmp_file.name)
                
                # Set password environment variable
                env = os.environ.copy()
                if self.db_info['type'] == 'postgresql':
                    env['PGPASSWORD'] = self.db_info['password']
                
                # Run restore command
                with open(tmp_file.name, 'r') as f:
                    result = subprocess.run(cmd, stdin=f, stderr=subprocess.PIPE, env=env)
                
                if result.returncode == 0:
                    print(f"  Restored {self.db_info['type']} database")
                else:
                    print(f"  Error restoring database: {result.stderr.decode()}")
                
                os.unlink(tmp_file.name)
                
        except Exception as e:
            print(f"  Error restoring external database: {e}")
    
    def _restore_config_files(self, zipf):
        """Restore configuration files"""
        config_files = ['config/.env', 'config/config.py', 'config/requirements.txt', 'config/app.py']
        
        for config_file in config_files:
            try:
                zipf.extract(config_file, '/tmp')
                target_file = config_file.replace('config/', '')
                shutil.copy(f'/tmp/{config_file}', target_file)
                print(f"  Restored config file: {target_file}")
            except:
                pass  # Config file might not exist in backup

class S3Backup:
    def __init__(self, bucket_name, region='us-east-1'):
        self.bucket_name = bucket_name
        self.region = region
        self.s3_client = boto3.client('s3', region_name=region)
    
    def upload_backup(self, backup_path, s3_key=None):
        """Upload backup to S3"""
        if s3_key is None:
            s3_key = f"backups/{os.path.basename(backup_path)}"
        
        try:
            self.s3_client.upload_file(backup_path, self.bucket_name, s3_key)
            print(f"✅ Backup uploaded to S3: s3://{self.bucket_name}/{s3_key}")
            return True
        except ClientError as e:
            print(f"❌ Error uploading to S3: {e}")
            return False
    
    def download_backup(self, s3_key, local_path=None):
        """Download backup from S3"""
        if local_path is None:
            local_path = os.path.basename(s3_key)
        
        try:
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            print(f"✅ Backup downloaded from S3: {local_path}")
            return local_path
        except ClientError as e:
            print(f"❌ Error downloading from S3: {e}")
            return None

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 backup_restore.py backup [backup_file.zip]")
        print("  python3 backup_restore.py restore <backup_file.zip>")
        print("  python3 backup_restore.py backup-s3 <backup_file.zip> [s3_key]")
        print("  python3 backup_restore.py restore-s3 <s3_key> [local_file]")
        sys.exit(1)
    
    command = sys.argv[1]
    backup = DatabaseBackup()
    
    if command == 'backup':
        backup_file = sys.argv[2] if len(sys.argv) > 2 else None
        backup.create_backup(backup_file)
    
    elif command == 'restore':
        if len(sys.argv) < 3:
            print("❌ Please specify backup file to restore")
            sys.exit(1)
        backup_file = sys.argv[2]
        backup.restore_backup(backup_file)
    
    elif command == 'backup-s3':
        backup_file = sys.argv[2] if len(sys.argv) > 2 else None
        s3_key = sys.argv[3] if len(sys.argv) > 3 else None
        
        # Create backup first
        backup_path = backup.create_backup(backup_file)
        
        # Upload to S3
        bucket_name = os.environ.get('BACKUP_S3_BUCKET')
        if not bucket_name:
            print("❌ BACKUP_S3_BUCKET environment variable not set")
            sys.exit(1)
        
        s3_backup = S3Backup(bucket_name)
        s3_backup.upload_backup(backup_path, s3_key)
    
    elif command == 'restore-s3':
        if len(sys.argv) < 3:
            print("❌ Please specify S3 key to restore")
            sys.exit(1)
        
        s3_key = sys.argv[2]
        local_file = sys.argv[3] if len(sys.argv) > 3 else None
        
        bucket_name = os.environ.get('BACKUP_S3_BUCKET')
        if not bucket_name:
            print("❌ BACKUP_S3_BUCKET environment variable not set")
            sys.exit(1)
        
        s3_backup = S3Backup(bucket_name)
        backup_path = s3_backup.download_backup(s3_key, local_file)
        
        if backup_path:
            backup.restore_backup(backup_path)
    
    else:
        print(f"❌ Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()