"""Postgres connection pool (asyncpg), with TLS as configured by DB_SSL."""
from __future__ import annotations

import ssl
from pathlib import Path

import asyncpg

from .config import settings

pool: asyncpg.Pool | None = None


def _ssl_context():
    mode = settings.db_ssl
    if mode == "disable":
        return False
    if mode == "verify-full":
        ctx = ssl.create_default_context(cafile=settings.db_ssl_root_cert or None)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx  # verifies the certificate chain and the hostname
    return mode  # "prefer" / "require": encrypted, certificate not verified


async def connect() -> None:
    global pool
    pool = await asyncpg.create_pool(settings.database_url, ssl=_ssl_context(), min_size=1, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute((Path(__file__).parent / "schema.sql").read_text())


async def close() -> None:
    if pool:
        await pool.close()
