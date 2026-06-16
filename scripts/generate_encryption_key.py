#!/usr/bin/env python3
"""
scripts/generate_encryption_key.py
Generate cryptographic keys for production .env.

Usage: python scripts/generate_encryption_key.py
"""
import secrets
from cryptography.fernet import Fernet

print("\n" + "="*60)
print("OCR Enterprise — Key Generator")
print("="*60)
print()
print("# Add these to your .env file:\n")
print(f"SECRET_KEY={secrets.token_hex(32)}")
print()
print(f"SUPER_ADMIN_KEY={secrets.token_urlsafe(32)}")
print()
print(f"ENCRYPTION_KEY={Fernet.generate_key().decode()}")
print()
print("⚠️  Store these securely. They cannot be recovered if lost.")
print("   ENCRYPTION_KEY loss = permanent data loss for encrypted files.")
print("="*60 + "\n")
