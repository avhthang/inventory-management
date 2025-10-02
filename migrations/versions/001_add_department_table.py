"""add_department_table

Revision ID: 001
Create Date: 2025-10-02
"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create department table
    op.create_table(
        'department',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('order_index', sa.Integer(), nullable=False, default=0),
        sa.Column('manager_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['parent_id'], ['department.id'], ),
        sa.ForeignKeyConstraint(['manager_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

    # Add department_id column to user table
    op.add_column('user', sa.Column('department_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'user', 'department', ['department_id'], ['id'])

    # Migrate existing department data
    op.execute('''
        INSERT INTO department (name, created_at, updated_at)
        SELECT DISTINCT department, datetime('now'), datetime('now')
        FROM user
        WHERE department IS NOT NULL
    ''')

    # Update user.department_id based on department names
    op.execute('''
        UPDATE user
        SET department_id = (
            SELECT id 
            FROM department 
            WHERE department.name = user.department
        )
        WHERE department IS NOT NULL
    ''')

def downgrade():
    # Backup department info to user.department
    op.execute('''
        UPDATE user
        SET department = (
            SELECT name
            FROM department
            WHERE department.id = user.department_id
        )
        WHERE department_id IS NOT NULL
    ''')

    # Drop foreign key on user.department_id
    op.drop_constraint(None, 'user', type_='foreignkey')
    
    # Drop department_id column from user
    op.drop_column('user', 'department_id')
    
    # Drop department table
    op.drop_table('department')