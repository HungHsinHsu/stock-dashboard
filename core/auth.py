"""極簡帳號驗證：密碼以 PBKDF2-SHA256 加鹽雜湊存放（不存明碼），純標準庫。

角色：admin（由 Streamlit secrets 的 ADMIN_USER/ADMIN_PASSWORD 認定，可管理使用者）
      user （由 admin 新增、存在 DB，只能用 chatbox）。
"""
import binascii
import hashlib
import os

_ITER = 100_000


def hash_password(password, salt=None):
    """回 'salt$hexhash'。salt 未給時隨機產生。"""
    if salt is None:
        salt = binascii.hexlify(os.urandom(16)).decode()
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                             salt.encode(), _ITER)
    return f"{salt}${binascii.hexlify(dk).decode()}"


def verify_password(password, stored):
    """比對明碼與 'salt$hexhash'；不符或格式錯回 False。"""
    if not stored or "$" not in str(stored):
        return False
    salt = stored.split("$", 1)[0]
    return hash_password(password, salt) == stored
