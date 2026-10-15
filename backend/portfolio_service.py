import os
import datetime
import logging
import threading
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey, UniqueConstraint, event, text, update as sa_update, delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

logger = logging.getLogger(__name__)

Base = declarative_base()

# Repo root = parent of the backend/ dir. Same anchoring strategy as
# backend/storage.py: a relative db_path resolves against the repo root so
# every entry point opens the same file regardless of the launch CWD.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _utc_now():
    """Naive datetime in UTC, matching storage.py's convention: the
    DateTime columns here are timezone-naive, and datetime.utcnow() is
    deprecated since py3.12."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _sqlite_connect(dbapi_conn, _connection_record):
    """Per-connection pragmas — the same set storage.py applies, plus
    foreign_keys=ON so Position.portfolio_id actually rejects orphan rows
    (SQLite ships with FK enforcement off).

    Each pragma is guarded individually: journal_mode=WAL writes the db
    file header and fails on a read-only file, while foreign_keys /
    busy_timeout / synchronous are connection-local flags that must still
    be applied there.
    """
    cur = dbapi_conn.cursor()
    try:
        for pragma in (
            "PRAGMA foreign_keys=ON",
            "PRAGMA busy_timeout=15000",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA journal_mode=WAL",
        ):
            try:
                cur.execute(pragma)
            except Exception as e:
                logger.debug("sqlite pragma %r failed: %s", pragma, e)
    finally:
        cur.close()


class _StaleRow(Exception):
    """A guarded UPDATE/DELETE matched 0 rows: a concurrent writer changed
    the row between our SELECT and our write. _upsert_retry re-runs the
    whole read-modify-write on a fresh transaction.

    Needed because SQLite SELECTs run in autocommit (pysqlite only BEGINs
    before the first DML), so the SELECT in add_position is NOT snapshot-
    protected against a commit that lands before our UPDATE — the write
    then silently overwrites fresh data (lost update). The CAS guard
    (WHERE volume = <value we read>) is evaluated under the write lock and
    turns that race into a detectable rowcount=0."""


class Portfolio(Base):
    __tablename__ = 'portfolios'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=_utc_now)
    description = Column(Text, nullable=True)

    positions = relationship("Position", back_populates="portfolio", cascade="all, delete-orphan")


class Position(Base):
    __tablename__ = 'positions'
    # One row per (portfolio_id, code): the SELECT-then-INSERT upsert in
    # add_position relies on this constraint to serialize concurrent
    # writers — the loser gets IntegrityError and retries onto the
    # winner's row instead of creating a duplicate.
    __table_args__ = (
        UniqueConstraint('portfolio_id', 'code', name='uq_positions_portfolio_code'),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(50), nullable=True)
    volume = Column(Integer, default=0)
    avg_price = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0) # Last updated price
    updated_at = Column(DateTime, default=_utc_now)

    portfolio = relationship("Portfolio", back_populates="positions")


class PortfolioService:
    def __init__(self, db_path=None):
        # Anchoring matches StorageManager: relative paths resolve against
        # the repo root (not os.getcwd()); the env override and absolute
        # paths are honored as-is.
        if db_path is None:
            db_path = os.environ.get("MLCHAN_PORTFOLIO_DB") or "portfolio.db"
        if not os.path.isabs(db_path):
            db_path = os.path.join(_REPO_ROOT, db_path)
        self.db_path = db_path

        # timeout=15 is the sqlite3-level busy timeout; the connect hook
        # additionally switches the file to WAL + busy_timeout (see
        # _sqlite_connect) so concurrent writers wait instead of raising
        # 'database is locked'.
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )
        event.listen(self.engine, "connect", _sqlite_connect)
        Base.metadata.create_all(self.engine)
        self._ensure_schema()
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def _ensure_schema(self):
        """Backfill for DBs whose positions table predates the
        UniqueConstraint: create_all() only affects fresh tables, so an
        existing file still lacks (portfolio_id, code) uniqueness. Creates
        a covering UNIQUE INDEX when none exists and the table has no
        duplicates; pre-existing duplicates are logged and left for manual
        merge rather than silently rewritten. Best-effort — failures
        (e.g. read-only file) are logged, never raised."""
        try:
            with self.engine.connect() as conn:
                # PRAGMA index_list -> (seq, name, unique, origin, partial)
                for row in conn.execute(text("PRAGMA index_list('positions')")).fetchall():
                    if not row[2]:
                        continue
                    # PRAGMA index_info -> (seqno, cid, colname)
                    cols = [r[2] for r in conn.execute(
                        text("PRAGMA index_info('%s')" % row[1])).fetchall()]
                    if cols == ["portfolio_id", "code"]:
                        return  # uniqueness already enforced
                dup = conn.execute(text(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM positions "
                    "GROUP BY portfolio_id, code HAVING COUNT(*) > 1)"
                )).scalar()
                if dup:
                    logger.warning(
                        "positions has %s duplicate (portfolio_id, code) groups; "
                        "unique index skipped — merge them manually", dup)
                    return
                conn.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_positions_portfolio_code "
                    "ON positions(portfolio_id, code)"))
                conn.commit()
        except Exception as e:
            logger.debug("positions unique-index backfill skipped: %s", e)

    def _upsert_retry(self, work, attempts: int = 5):
        """Run work(session), retrying on retryable write conflicts —
        same idea as StorageManager._upsert_retry, covering two races:

        * IntegrityError: a second writer inserted the same
          (portfolio_id, code) between our SELECT and our commit; the
          retry re-reads and updates the winner's row instead.
        * _StaleRow: our CAS guard saw the row change under us; the retry
          re-reads and re-applies on fresh values.

        work() must commit() the passed session itself and be idempotent;
        ValueError raised inside propagates immediately."""
        last_exc = None
        for _ in range(max(1, int(attempts))):
            with self.get_session() as session:
                try:
                    return work(session)
                except (IntegrityError, _StaleRow) as e:
                    session.rollback()
                    last_exc = e
        raise last_exc

    def get_session(self):
        return self.SessionLocal()

    def list_portfolios(self) -> List[Dict[str, Any]]:
        with self.get_session() as db:
            portfolios = db.query(Portfolio).all()
            return [{
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "created_at": p.created_at.isoformat() if p.created_at else "",
            } for p in portfolios]

    def create_portfolio(self, name: str, description: str = "") -> Dict[str, Any]:
        with self.get_session() as db:
            p = Portfolio(name=name, description=description)
            db.add(p)
            db.commit()
            db.refresh(p)
            return {"id": p.id, "name": p.name}

    def delete_portfolio(self, portfolio_id: int) -> bool:
        """Delete a portfolio and its positions (ORM cascade).
        Returns True when a row was deleted, False when the id does not
        exist — callers can map False to a 404."""
        with self.get_session() as db:
            p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            if not p:
                return False
            db.delete(p)
            db.commit()
            return True

    def get_portfolio_detail(self, portfolio_id: int) -> Optional[Dict[str, Any]]:
        with self.get_session() as db:
            p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            if not p:
                return None

            positions = []
            total_market_value = 0.0
            total_cost = 0.0

            for pos in p.positions:
                mv = pos.volume * pos.current_price
                cost = pos.volume * pos.avg_price
                pnl = mv - cost
                pnl_pct = (pnl / cost) if cost > 0 else 0.0

                positions.append({
                    "id": pos.id,
                    "code": pos.code,
                    "name": pos.name,
                    "volume": pos.volume,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "market_value": mv,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "updated_at": pos.updated_at.isoformat() if pos.updated_at else None
                })
                total_market_value += mv
                total_cost += cost

            total_pnl = total_market_value - total_cost
            total_pnl_pct = (total_pnl / total_cost) if total_cost > 0 else 0.0

            return {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "positions": positions,
                "summary": {
                    "total_market_value": total_market_value,
                    "total_cost": total_cost,
                    "total_pnl": total_pnl,
                    "total_pnl_pct": total_pnl_pct
                }
            }

    def add_position(self, portfolio_id: int, code: str, volume: int, price: float, name: str = "") -> Dict[str, Any]:
        """Apply a buy (volume>0) or sell (volume<0) to (portfolio_id, code).

        Returns a dict describing the resulting position; {"closed": True}
        when the trade closed the position out. Raises ValueError for a
        zero volume, an oversell, a reduce on a missing position, or a
        portfolio that does not exist."""
        if volume == 0:
            # A zero-volume trade is meaningless input (likely a caller bug);
            # reject it explicitly instead of silently no-op'ing.
            raise ValueError("volume must be non-zero (positive to add, negative to reduce)")

        def work(db):
            # Explicit existence check: foreign_keys=ON would also reject
            # an orphan insert at commit, but a clear error beats a bare
            # IntegrityError — and the retry loop must see ValueError here
            # rather than retry a doomed insert.
            if db.query(Portfolio).filter(Portfolio.id == portfolio_id).first() is None:
                raise ValueError(f"portfolio {portfolio_id} does not exist")

            pos = db.query(Position).filter(
                Position.portfolio_id == portfolio_id,
                Position.code == code,
            ).first()
            if pos:
                new_vol = pos.volume + volume
                if new_vol < 0:
                    # Oversell would corrupt the position; reject and keep the row.
                    raise ValueError(
                        f"oversell rejected: holding {pos.volume} of {code}, cannot reduce by {abs(volume)}"
                    )
                if new_vol == 0:
                    # Fully closed. CAS-guarded delete: if a concurrent
                    # writer changed volume since our SELECT, rowcount=0
                    # and we retry on the fresh row instead of deleting a
                    # position that just gained/lost shares.
                    res = db.execute(
                        sa_delete(Position).where(
                            Position.id == pos.id,
                            Position.volume == pos.volume,
                        )
                    )
                    if res.rowcount != 1:
                        raise _StaleRow(f"position {code} changed under us")
                    db.commit()
                    return {"code": code, "volume": 0, "closed": True}
                vals = {
                    "volume": new_vol,
                    "updated_at": _utc_now(),
                }
                if volume > 0:
                    # Add to position: weighted average cost
                    vals["avg_price"] = (pos.volume * pos.avg_price + volume * price) / new_vol
                # else: reduce — decrease volume only, keep avg_price unchanged
                if name:
                    vals["name"] = name
                # CAS-guarded update (see _StaleRow docstring).
                res = db.execute(
                    sa_update(Position)
                    .where(Position.id == pos.id, Position.volume == pos.volume)
                    .values(**vals)
                )
                if res.rowcount != 1:
                    raise _StaleRow(f"position {code} changed under us")
                out = {
                    "id": pos.id, "code": pos.code, "volume": new_vol,
                    "avg_price": vals.get("avg_price", pos.avg_price),
                    "closed": False,
                }
                db.commit()
                return out

            if volume < 0:
                raise ValueError(f"cannot reduce non-existent position {code}")
            pos = Position(
                portfolio_id=portfolio_id,
                code=code,
                name=name,
                volume=volume,
                avg_price=price,
                current_price=price
            )
            db.add(pos)
            db.flush() # assign PK; surfaces a unique-conflict before commit
            out = {
                "id": pos.id, "code": pos.code, "volume": pos.volume,
                "avg_price": pos.avg_price, "closed": False,
            }
            db.commit()
            return out

        return self._upsert_retry(work)

    def update_prices(self, price_map: Dict[str, float]):
        """
        Update current prices for all positions
        price_map: {code: current_price}
        """
        with self.get_session() as db:
            positions = db.query(Position).all()
            for pos in positions:
                if pos.code in price_map:
                    pos.current_price = price_map[pos.code]
                    pos.updated_at = _utc_now()
            db.commit()

# Singleton instance
_portfolio_service = None
_portfolio_service_lock = threading.Lock()

def get_portfolio_service():
    global _portfolio_service
    if _portfolio_service is None:
        # Double-checked locking: PortfolioService() opens an engine and
        # runs create_all, so a race would otherwise build two pools.
        with _portfolio_service_lock:
            if _portfolio_service is None:
                _portfolio_service = PortfolioService()
    return _portfolio_service
