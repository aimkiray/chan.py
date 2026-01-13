
import os
import datetime
import json
from typing import List, Optional, Dict, Any
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, Session

Base = declarative_base()

class Portfolio(Base):
    __tablename__ = 'portfolios'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    description = Column(Text, nullable=True)
    
    positions = relationship("Position", back_populates="portfolio", cascade="all, delete-orphan")

class Position(Base):
    __tablename__ = 'positions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    portfolio_id = Column(Integer, ForeignKey('portfolios.id'), nullable=False)
    code = Column(String(20), nullable=False)
    name = Column(String(50), nullable=True)
    volume = Column(Integer, default=0)
    avg_price = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0) # Last updated price
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    portfolio = relationship("Portfolio", back_populates="positions")

class PortfolioService:
    def __init__(self, db_path="portfolio.db"):
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_session(self):
        return self.SessionLocal()

    def list_portfolios(self) -> List[Dict[str, Any]]:
        with self.get_session() as db:
            portfolios = db.query(Portfolio).all()
            return [{"id": p.id, "name": p.name, "description": p.description, "created_at": p.created_at.isoformat()} for p in portfolios]

    def create_portfolio(self, name: str, description: str = "") -> Dict[str, Any]:
        with self.get_session() as db:
            p = Portfolio(name=name, description=description)
            db.add(p)
            db.commit()
            db.refresh(p)
            return {"id": p.id, "name": p.name}

    def delete_portfolio(self, portfolio_id: int):
        with self.get_session() as db:
            p = db.query(Portfolio).filter(Portfolio.id == portfolio_id).first()
            if p:
                db.delete(p)
                db.commit()

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

    def add_position(self, portfolio_id: int, code: str, volume: int, price: float, name: str = ""):
        with self.get_session() as db:
            # Check if exists
            pos = db.query(Position).filter(Position.portfolio_id == portfolio_id, Position.code == code).first()
            if pos:
                # Average down/up
                new_vol = pos.volume + volume
                if new_vol > 0:
                    new_avg = (pos.volume * pos.avg_price + volume * price) / new_vol
                    pos.volume = new_vol
                    pos.avg_price = new_avg
                else:
                    db.delete(pos) # Closed
            else:
                if volume > 0:
                    pos = Position(
                        portfolio_id=portfolio_id,
                        code=code,
                        name=name,
                        volume=volume,
                        avg_price=price,
                        current_price=price
                    )
                    db.add(pos)
            db.commit()

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
                    pos.updated_at = datetime.datetime.utcnow()
            db.commit()

# Singleton instance
_portfolio_service = None

def get_portfolio_service():
    global _portfolio_service
    if _portfolio_service is None:
        _portfolio_service = PortfolioService()
    return _portfolio_service
