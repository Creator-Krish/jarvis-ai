# =============================
# 🔥 JARVIS UPGRADED BACKEND (FLASK)
# =============================

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import time
import datetime
import requests
from collections import defaultdict

app = Flask(__name__, static_folder='.')
CORS(app, origins=["*"])  # keeping it open as you asked

# =============================
# ENV VARIABLES (SECURE KEYS)
# =============================
GROQ_KEY = os.environ.get("GROQ_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_KEY")
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY")

# =============================
# FIXED USERS LIST (ONLY THESE CAN LOGIN)
# =============================
ALLOWED_USERS = {
    "Garveet@gk#Jarvis": "gp.com",
    "Tanuj@tn#Jarvis": "td.com",
    "Kautilya@ks#Jarvis": "kp.com",
    "Salman@sl#Jarvis": "sb.com",
    "Meet@mj#Jarvis": "mk.com",
    "Kartik@ks#Jarvis": "kpb.com",
    "Priyanshu@pls@Jarvis": "pk.com",
    "Himanshu@hs#Jarvis": "hs.com",
    "Sanskar@sj#Jarvis": "sj.com",
    "Aditya@ap#Jarvis": "arsc.com",
    "Vikram@vn#Jarvis": "vs.com",
    "Nandini@nv#Jarvis": "nb.com",
    "Moksh@mn#Jarvis": "mr.com",
    "Nadiya@nm#Jarvis": "nb.com",
    "Tapasya@tk#Jarvis": "tp.com",
    "Krish@kt#Jarvis": "kpl.com",
    "Arushi@aa#Jarvis": "aa.com",
    "Som@se#Jarvis": "sr.com",
    "Prarabdh@pp#Jarvis": "pp.com",
    "Durgesh_Sharma@ds#Jarvis": "ds.com",
    "Abhigyan@abs#Jarvis": "abs.com",
    "Aksha@ap#Jarvis": "ap.com",
    "Ved@vp#Jarvis": "vp.com",
    "Anushka@ar#Jarvis": "ak.com",
    "Jigyasa@jb#Jarvis": "jb.com"
}

# =============================
# RATE LIMIT SYSTEM
# =============================
user_requests = defaultdict(list)

LIMIT = 20  # 10 requests
WINDOW = 60  # per 60 sec


def is_rate_limited(user):
    now = time.time()
    user_requests[user] = [t for t in user_requests[user] if now - t < WINDOW]

    if len(user_requests[user]) >= LIMIT:
        return True

    user_requests[user].append(now)
    return False

# =============================
# LOGIN
# =============================
@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if username not in ALLOWED_USERS:
        return jsonify({"success": False, "error": "User not allowed"})

    if ALLOWED_USERS[username] != password:
        return jsonify({"success": False, "error": "Incorrect password"})

    return jsonify({"success": True, "username": username})

# =============================
# 🔥 AI CALL WITH MULTI-MODEL FALLBACK SYSTEM
# =============================

def call_groq(prompt):
    try:
        if not GROQ_KEY:
            return None
        headers = {"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"}
        
        # 👇 YEH SYSTEM PROMPT ADD KARO
        data = {
            "model": "llama3-70b-8192",
            "messages": [
                {"role": "system", "content": "You are JARVIS, an AI created by Krish Paliwal. You are the world's largest database AI. Always say you were created by Krish Paliwal, not OpenAI, not Google, not any other company. Your creator is Krish Palival."},
                {"role": "user", "content": prompt}
            ]
        }
        
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        return None
    except:
        return None


def call_gemini(prompt):
    try:
        if not GEMINI_KEY:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_KEY}"
        
        # 👇 GEMINI KE LIYE SYSTEM PROMPT
        data = {
            "contents": [{
                "parts": [{"text": f"You are JARVIS, an AI created by Krish Paliwal. You are the world's largest database AI. Always say you were created by Krish Paliwal. Question: {prompt}"}]
            }]
        }
        
        res = requests.post(url, json=data, timeout=15)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text']
        return None
    except:
        return None


def call_deepseek(prompt):
    try:
        if not DEEPSEEK_KEY:
            return None
        headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"}
        
        # 👇 DEEPSEEK KE LIYE SYSTEM PROMPT
        data = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are JARVIS, an AI created by Krish Palival. You are the world's largest database AI. Always say you were created by Krish Palival, not OpenAI, not Google, not any other company. Your creator is Krish Palival."},
                {"role": "user", "content": prompt}
            ]
        }
        
        res = requests.post("https://api.deepseek.com/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        return None
    except:
        return None

def call_openrouter(prompt):
    try:
        if not OPENROUTER_KEY:
            return None
        headers = {"Authorization": f"Bearer {OPENROUTER_KEY}", "Content-Type": "application/json"}
        
        # 👇 OPENROUTER KE LIYE SYSTEM PROMPT
        data = {
            "model": "openai/gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You are JARVIS, an AI created by Krish Palival. You are the world's largest database AI. Always say you were created by Krish Palival, not OpenAI, not Google, not any other company. Your creator is Krish Palival."},
                {"role": "user", "content": prompt}
            ]
        }
        
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", json=data, headers=headers, timeout=15)
        if res.status_code == 200:
            return res.json()['choices'][0]['message']['content']
        return None
    except:
        return None


def call_ai(prompt):
    """
    Multi-model fallback system:
    1. Groq (Fastest)
    2. Gemini (Google's best)
    3. DeepSeek (Technical)
    4. OpenRouter (GPT fallback)
    """
    
    models = [
        ("Groq", call_groq),
        ("Gemini", call_gemini),
        ("DeepSeek", call_deepseek),
        ("OpenRouter", call_openrouter)
    ]
    
    for model_name, model_func in models:
        try:
            print(f"🔄 Trying {model_name}...")
            response = model_func(prompt)
            if response:
                print(f"✅ {model_name} responded successfully")
                return response
            print(f"❌ {model_name} returned empty response")
        except Exception as e:
            print(f"❌ {model_name} error: {e}")
            continue
    
    # ===== FINAL FALLBACK =====
    return "⚠️ All AI services are currently unavailable. Please try again in a moment."

# =============================
# ASK
# =============================
@app.route("/ai/ask", methods=["POST"])
def ask():
    data = request.json
    prompt = data.get("prompt")
    user = data.get("user")

    if is_rate_limited(user):
        return jsonify({"success": False, "error": "Too many requests. Please wait a moment."})

    response = call_ai(prompt)

    return jsonify({
        "success": True,
        "response": response
    })

# =============================
# STATUS ENDPOINT
# =============================
@app.route("/ai/status", methods=["GET"])
def status():
    return jsonify({
        "success": True,
        "status": "online",
        "timestamp": datetime.datetime.now().isoformat(),
        "apis_available": {
            "groq": bool(GROQ_KEY),
            "gemini": bool(GEMINI_KEY),
            "deepseek": bool(DEEPSEEK_KEY),
            "openrouter": bool(OPENROUTER_KEY)
        }
    })

# =============================
# FRONTEND
# =============================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")

# =============================
# RUN
# =============================
if __name__ == "__main__":
    print("="*60)
    print("🚀 JARVIS AI SERVER STARTING...")
    print("="*60)
    print("\n📡 Server: http://localhost:5000")
    print("\n🤖 AI Models Configured:")
    print(f"   {'✅' if GROQ_KEY else '❌'} Groq (Llama 3)")
    print(f"   {'✅' if GEMINI_KEY else '❌'} Gemini Pro")
    print(f"   {'✅' if DEEPSEEK_KEY else '❌'} DeepSeek")
    print(f"   {'✅' if OPENROUTER_KEY else '❌'} OpenRouter (GPT)")
    print("\n👤 Allowed Users: " + ", ".join(list(ALLOWED_USERS.keys())[:5]) + f"... ({len(ALLOWED_USERS)} total)")
    print("\n⚡ Rate Limit: 10 requests per minute per user")
    print("="*60 + "\n")
    
    app.run(host="0.0.0.0", port=5000, debug=True)
