"""Parollar (bcrypt) va login urinishlarini cheklash."""
from __future__ import annotations

import secrets
import time

import bcrypt


def hash_password(password: str) -> str:
    # bcrypt 72 baytdan uzun parolni qabul qilmaydi — kesib olamiz
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def password_problem(pw: str) -> str | None:
    """Parol talablari. Muammo bo'lsa matn, aks holda None."""
    if len(pw) < 8:
        return "Parol kamida 8 belgidan iborat bo'lishi kerak"
    if pw.isdigit() or pw.isalpha():
        return "Parolda harf va raqam aralash bo'lsin"
    return None


class LoginLimiter:
    """IP bo'yicha: 10 daqiqada 8 ta noto'g'ri urinishdan keyin 10 daqiqa bloklaydi."""

    def __init__(self, max_attempts: int = 8, window: int = 600) -> None:
        self.max_attempts, self.window = max_attempts, window
        self._fails: dict[str, list[float]] = {}

    def _clean(self, ip: str) -> list[float]:
        now = time.monotonic()
        lst = [t for t in self._fails.get(ip, []) if now - t < self.window]
        self._fails[ip] = lst
        return lst

    def blocked(self, ip: str) -> bool:
        return len(self._clean(ip)) >= self.max_attempts

    def fail(self, ip: str) -> None:
        self._clean(ip).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)


login_limiter = LoginLimiter()
