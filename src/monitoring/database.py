"""PostgreSQL connections; production credentials stay in Secrets Manager."""

import json
import os
import time
from functools import lru_cache

import boto3
from sqlalchemy import create_engine, event


@lru_cache(maxsize=1)
def engine():
    if os.environ.get("DATABASE_URL"):
        return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, hide_parameters=True)
    db = create_engine(
        "postgresql+psycopg://", pool_pre_ping=True, pool_recycle=300, hide_parameters=True
    )
    cached = {}

    @event.listens_for(db, "do_connect")
    def credentials(dialect, connection_record, args, params):
        if cached.get("until", 0) < time.time():
            response = boto3.client("secretsmanager").get_secret_value(
                SecretId=os.environ["MONITORING_DB_SECRET"]
            )
            cached.update(value=json.loads(response["SecretString"]), until=time.time() + 60)
        secret = cached["value"]
        params.update(
            host=os.environ["MONITORING_DB_HOST"],
            dbname="monitoring",
            user=secret["username"],
            password=secret["password"],
            sslmode="verify-full",
            sslrootcert="/app/rds-ca.pem",
            connect_timeout=15,
            options="-c statement_timeout=30000",
        )

    return db
