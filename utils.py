import hashlib
import re
import bcrypt


# SHA-256 password hashing (kept for backwards compatibility only)
def _hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


# NEW SECURE PASSWORD UTILITIES

def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt (secure, salted hashing).
    
    Args:
        password: Plain text password to hash
        
    Returns:
        Hashed password as UTF-8 string
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed.decode('utf-8')


def verify_password(password: str, hashed_password: str) -> bool:
    """
    Verify a plain text password against a bcrypt hash.
    
    Args:
        password: Plain text password to verify
        hashed_password: Hashed password from database
        
    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets security requirements.
    
    Requirements:
    - Minimum 8 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character (!@#$%^&*)
    
    Args:
        password: Password to validate
        
    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    
    if not re.search(r'[0-9]', password):
        return False, "Password must contain at least one digit"
    
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>?/\\|`~]', password):
        return False, "Password must contain at least one special character (!@#$%^&*)"
    
    return True, "Password is strong"

