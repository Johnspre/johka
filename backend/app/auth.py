from fastapi import Depends, HTTPException, status, Request
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from database import get_db
from models import UserDB
import os
from dotenv import load_dotenv

# 🔐 Laad alle gevoelige data via .env
load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET")
ALGORITHM = os.getenv("ALGORITHM")



# helper: token decoderen
def verify_token(token: str):
    try:
        # Probeer eerst normale Johka-token te decoderen (met handtekening)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        # Als dat mislukt, probeer LiveKit-stijl (zonder verificatie)
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except Exception:
            raise HTTPException(status_code=401, detail="Ongeldige of verlopen token")

    # Johka-login tokens: hebben meestal 'username' of 'sub' (int)
    # LiveKit-tokens: hebben 'sub' of 'identity' met string-naam
    user_id = payload.get("sub")
    username = payload.get("username") or payload.get("name") or payload.get("identity")

    if not user_id and not username:
        raise HTTPException(status_code=401, detail="Token bevat geen geldige claims")

    return {
        "id": int(user_id) if str(user_id).isdigit() else None,
        "username": username,
    }


# dependency: gebruiker ophalen uit token
def get_current_user(request: Request, s: Session = Depends(get_db)):
    """
    Haalt de ingelogde gebruiker op aan de hand van het Bearer-token in de Authorization-header.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Geen geldige Authorization-header")

    token = auth_header.split(" ")[1]
    claims = verify_token(token)

    user = None
    if claims["id"]:
        user = s.query(UserDB).filter_by(id=claims["id"]).first()
    elif claims["username"]:
        user = s.query(UserDB).filter_by(username=claims["username"]).first()

    if not user:
        raise HTTPException(status_code=401, detail="Gebruiker niet gevonden")

    return user
