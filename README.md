# Class 4 Driver’s Licence Bot 🇨🇦🚗

This Telegram bot helps users practice for the Alberta Class 4 Commercial Driver’s Licence knowledge test.  
It supports English and Ukrainian questions with feedback and explanations.

## 🧠 Features
- Bilingual (English + Ukrainian)
- 10-question quiz
- Inline buttons (A/B/C/D) for answering
- Feedback after each answer
- Final score out of 10
- Built using `python-telegram-bot v20+`

## 🚀 How to run locally

```bash
pip install -r requirements.txt
export BOT_TOKEN=your_telegram_token
python main.py
```

## 🌐 Deploy on Render.com

1. Push this repo to GitHub
2. Connect it to [Render.com](https://render.com)
3. Create a **Web Service**
4. Set the environment variable:
   - `BOT_TOKEN = <your bot token>`
5. Set start command: `python main.py`

That’s it — your Telegram bot will be live!
