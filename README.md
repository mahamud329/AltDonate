# <img width="26" height="26" alt="donation" src="https://github.com/user-attachments/assets/db396e2e-859e-4367-a9e3-a7077f452869" /> AltDonate  
### Alternative Donation Platform for Bangladeshi Streamers

AltDonate is a full-stack donation platform that enables Bangladeshi streamers to receive **real-time on-stream donation alerts** using local Mobile Financial Services (MFS) like **bKash** and **Nagad**, without relying on international payment gateways.



## 🚀 Overview

Global platforms such as Streamlabs do not support local payment methods commonly used in Bangladesh. AltDonate solves this by converting **MFS SMS notifications into instant stream alerts**, allowing viewers to donate easily and streamers to receive support seamlessly.

## ⚡ Key Features

- 🔴 Real-time donation alerts (1–3 seconds)
- 📱 Android SMS capture and processing
- 🔑 Phrase-based filtering to detect valid donations
- 👤 Donor name mapping via Google Forms
- 📊 Streamer dashboard with analytics & history
- 🎯 Campaign and fundraising support
- 🧪 Test mode for setup without real transactions
- 🛠 Admin panel for system management


## 🏗 How It Works

1. Viewer sends money via bKash/Nagad with a unique phrase  
2. Streamer’s phone receives transaction SMS  
3. Android app captures and filters the SMS  
4. Server processes and stores donation data  
5. WebSocket sends real-time event  
6. 🎉 Alert appears live on stream via StreamerBot  


## 🧩 Tech Stack

- **Mobile:** Kotlin / Java (Android)  
- **Backend:** Python (Flask + WebSocket)  
- **Database:** PostgreSQL (SQLAlchemy ORM)  
- **Integration:** Google Sheets API  
- **Infrastructure:** Cloudflared (HTTPS tunnel)  


## 🔐 Security & Privacy

- Secure HTTPS communication  
- Token-based authentication  
- No phone numbers shown on stream  
- Optional donor registration  
- Phrase-based fraud prevention  


## 📌 Status

✅ Production-ready  
✅ Actively used by streamers  
✅ Proven with real donation transactions  


## 👨‍💻 Author

**Md. Almahamud**  
