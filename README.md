# 🪩 Johka Live — Next-Gen Chat & Creator Platform

**Johka Live** is een moderne, zelfgehoste webapp die live videochat, AI-interactie, tokenbetalingen en community-functies combineert.  
Gebouwd met **FastAPI**, **Docker Compose**, **PostgreSQL**, **Redis**, **LiveKit** en een custom frontend in HTML/JS.

---

## ⚡️ Features

- 🔐 **Registratie met e-mailverificatie**  
  Gebruikers ontvangen automatisch een bevestigingsmail via SMTP (MailProtect of Mailtrap).  
- 💬 **Rooms & Live Sessions**  
  Gebouwd bovenop [LiveKit](https://livekit.io) voor video/audio.  
- 💰 **Wallet met tokens**  
  - Mollie-integratie voor veilige betalingen  
  - Realtime saldo-update via webhook  
  - Volledige transactiegeschiedenis  
- 🧑‍💻 **Admin Dashboard**  
  - Bekijk gebruikers + saldi  
  - Voeg tokens toe  
  - Bekijk recente transacties  
- 🧠 **AI-chat integratie (optioneel)**  
  Johka kan uitbreiden met AI-gestuurde chatrooms en moderatie.  
- 📦 **Volledig Docker-gebaseerd**  
  Eén commando (`docker compose up`) start alles: backend, database, redis, caddy, livekit.

---

## 🛠 Tech Stack

| Component | Technologie |
|------------|--------------|
| Backend | FastAPI (Python 3.11) |
| Frontend | HTML / JS / CSS |
| Database | PostgreSQL 16 |
| Cache / Queue | Redis 7 |
| Videochat | LiveKit |
| Webserver | Caddy 2 |
| Payments | Mollie API |
| Mails | MailProtect (Easyhost) / Mailtrap |
| Auth | JWT-tokens (7 dagen geldig) |

---

## 🚀 Installatie (productie op VPS)

Johka Live draait volledig in **Docker Compose** op een eigen VPS  
(bijv. Easyhost of TransIP). Alle services (backend, frontend, Redis, Postgres, LiveKit, Caddy) draaien in aparte containers.


