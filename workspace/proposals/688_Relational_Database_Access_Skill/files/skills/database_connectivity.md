# Database Connectivity Skill

## Overview
This skill teaches how to connect to relational databases (PostgreSQL, MySQL, SQLite) from Python using SQLAlchemy.

## Prerequisites
- Python environment with `sqlalchemy` and appropriate DB driver (e.g., `psycopg2`, `mysql-connector-python`).
- Credentials stored in environment variables (e.g., `DB_URL`).

## Core Concepts
- **Engine**: The starting point for SQLAlchemy.
- **Session**: Handles transactions.
- **Models**: ORM classes.

## Example
```python
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv('DB_URL')
engine = create_engine(DATABASE_URL, pool_size=10, max_overflow=20)
Session = sessionmaker(bind=engine)
session = Session()

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    name = Column(String)

# Query
users = session.query(User).filter(User.name.like('%Alice%')).all()
```

## Security Best Practices
- Never hardcode credentials.
- Use connection pooling.
- Employ parameterized queries to avoid SQL injection.

## Further Reading
- SQLAlchemy docs: https://docs.sqlalchemy.org/
