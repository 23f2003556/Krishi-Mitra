import os
import requests
import struct
import base64
import json
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Form, UploadFile, File, Response, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from deepgram import DeepgramClient, AsyncDeepgramClient
from twilio.twiml.voice_response import VoiceResponse
import audioop
from dataclasses import dataclass

load_dotenv()

# Retrieve API keys
gemini_api_key = os.getenv("GEMINI_API_KEY")
DATAGOV_API_KEY = os.getenv("DATAGOV_API_KEY", "")
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")

# Silk TTS API Settings
SILK_ENDPOINT = os.getenv("SILK_ENDPOINT", "")
SILK_API_KEY = os.getenv("SILK_API_KEY", "")
SILK_VOICE_ID = os.getenv("SILK_VOICE_ID", "")
try:
    SILK_SAMPLE_HZ = int(os.getenv("SILK_SAMPLE_HZ", "24000"))
except (ValueError, TypeError):
    SILK_SAMPLE_HZ = 24000

# Initialize Gemini client
client = genai.Client(api_key=gemini_api_key)

# Crop translation dictionary (Hindi -> English)
COMMODITY_MAP = {
    "गेहूं": "Wheat",
    "गेहूँ": "Wheat",
    "धान": "Paddy(Dhan)(Common)",
    "चावल": "Paddy(Dhan)(Common)",
    "कपास": "Cotton",
    "मक्का": "Maize",
    "चना": "Bengal Gram(Gram)",
    "सोयाबीन": "Soyabean",
    "प्याज़": "Onion",
    "प्याज": "Onion",
    "टमाटर": "Tomato",
    "आलू": "Potato",
    "सरसों": "Mustard",
}

# Static scheme dictionary
SCHEMES = {
    "PM-KISAN": {
        "name": "Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)",
        "benefit": "₹6,000 per year in three equal installments of ₹2,000 directly into bank accounts of land-holding farmer families.",
        "eligibility": "Small and marginal farmers owning cultivable land.",
        "apply": "Register online at PM-KISAN portal (pmkisan.gov.in) or via Common Service Centres (CSC).",
        "docs": "Aadhaar Card, Land ownership papers, Bank Account Details, Mobile Number."
    },
    "PMFBY": {
        "name": "Pradhan Mantri Fasal Bima Yojana (PMFBY)",
        "benefit": "Low-premium crop insurance coverage against non-preventable natural risks.",
        "eligibility": "All farmers growing notified crops in notified areas, including sharecroppers and tenant farmers.",
        "apply": "Apply through PMFBY portal (pmfby.gov.in), nationalized banks, or CSC centers.",
        "docs": "Land record (RoR), Sowing certificate, Aadhaar Card, Bank Passbook, Tenant agreement (if applicable)."
    },
    "KCC": {
        "name": "Kisan Credit Card (KCC)",
        "benefit": "Easy short-term credit/loans for cultivation expenses, crop production, and post-harvest maintenance at low interest rates.",
        "eligibility": "All farmers, including owner-cultivators, tenant farmers, sharecroppers, and self-help groups.",
        "apply": "Visit any commercial bank, cooperative bank, or regional rural bank.",
        "docs": "Land ownership documents, Identity proof (Aadhaar/PAN), Address proof, Passport size photos."
    },
    "Soil Health Card": {
        "name": "Soil Health Card Scheme",
        "benefit": "Provides customized soil analysis reports and recommendations for nutrients and fertilizers required for targeted crop yields.",
        "eligibility": "All land-holding farmers in India.",
        "apply": "Agricultural officers collect soil samples from your farm and issue the card after lab analysis.",
        "docs": "Land Details, Aadhaar Card number."
    },
    "PMKSY": {
        "name": "Pradhan Mantri Krishi Sinchayee Yojana (PMKSY)",
        "benefit": "Financial assistance/subsidies for installing micro-irrigation systems like drip and sprinkler irrigation.",
        "eligibility": "Farmers owning cultivable land with a water source. Members of water users associations are also eligible.",
        "apply": "Submit an application to the district agriculture/horticulture department or via their online portal.",
        "docs": "Land documents (copy of 7/12 or Jamabandi), Micro-irrigation layout plan, Aadhaar Card, Bank passbook."
    }
}

# --- TOOL DEFINITIONS ---

def get_mandi_prices(commodity: str, state: str, district: str = "") -> dict:
    """Fetch today's mandi prices for an Indian state.

    Args:
        commodity: Crop in English or Hindi (e.g. "Wheat" or "गेहूं")
        state: Indian state in English (e.g. "Madhya Pradesh")
        district: Optional district filter
    """
    print(f"  🔧 get_mandi_prices: commodity={commodity}, state={state}, district={district}")
    
    # Translate Hindi commodity to English using map
    mapped_commodity = COMMODITY_MAP.get(commodity.strip(), commodity.strip())
    
    if not DATAGOV_API_KEY:
        # Fallback to demo record if API key is empty
        return {
            "ok": True,
            "source": "Demo Data (No API Key)",
            "records": [
                {
                    "market": district or "Indore Mandi",
                    "min_price": "2100",
                    "max_price": "2500",
                    "modal_price": "2300",
                    "unit": "Quintal",
                    "date": "2026-05-24"
                }
            ]
        }
    
    url = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
    params = {
        "api-key": DATAGOV_API_KEY,
        "format": "json",
        "limit": 5,
        "filters[state.keyword]": state,
        "filters[commodity]": mapped_commodity
    }
    if district:
        params["filters[district]"] = district
        
    try:
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        records = data.get("records", [])
        mapped_records = []
        for rec in records:
            mapped_records.append({
                "market": rec.get("market", ""),
                "min_price": rec.get("min_price", ""),
                "max_price": rec.get("max_price", ""),
                "modal_price": rec.get("modal_price", ""),
                "unit": "Quintal",
                "date": rec.get("arrival_date", "")
            })
        if not mapped_records:
            return {
                "ok": True,
                "source": "Data.gov.in API (Empty)",
                "records": [
                    {
                        "market": district or "Local Mandi",
                        "min_price": "2150",
                        "max_price": "2450",
                        "modal_price": "2300",
                        "unit": "Quintal",
                        "date": "2026-05-24"
                    }
                ]
            }
        return {
            "ok": True,
            "source": "Data.gov.in API",
            "records": mapped_records
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "source": "Error calling API",
            "records": []
        }

def get_weather(location: str) -> dict:
    """3-day weather forecast for an Indian location.

    Args:
        location: Indian city or location name (e.g. "Indore")
    """
    print(f"  🔧 get_weather: location={location}")
    try:
        # Geocode location within India
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1&country=IN"
        geo_res = requests.get(geo_url, timeout=10).json()
        results = geo_res.get("results", [])
        if not results:
            return {"ok": False, "error": f"Location '{location}' not found in India."}
        
        place = results[0]
        name = place.get("name", location)
        lat = place.get("latitude")
        lon = place.get("longitude")
        
        # Fetch 3-day weather forecast
        fc_url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 3,
            "timezone": "Asia/Kolkata"
        }
        fc_res = requests.get(fc_url, params=params, timeout=10).json()
        
        curr = fc_res.get("current", {})
        daily = fc_res.get("daily", {})
        
        forecast = []
        times = daily.get("time", [])
        max_temps = daily.get("temperature_2m_max", [])
        min_temps = daily.get("temperature_2m_min", [])
        precip_probs = daily.get("precipitation_probability_max", [])
        
        for i in range(len(times)):
            forecast.append({
                "date": times[i],
                "temp_max_c": max_temps[i] if i < len(max_temps) else None,
                "temp_min_c": min_temps[i] if i < len(min_temps) else None,
                "precipitation_probability": precip_probs[i] if i < len(precip_probs) else None
            })
            
        return {
            "ok": True,
            "place": name,
            "current": {
                "temp_c": curr.get("temperature_2m"),
                "precipitation_mm": curr.get("precipitation")
            },
            "forecast": forecast
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_scheme_info(scheme_query: str) -> dict:
    """Look up an Indian government farm scheme.

    Args:
        scheme_query: Name or alias of the scheme (e.g. "PM-KISAN", "kisan credit", "fasal bima")
    """
    print(f"  🔧 get_scheme_info: scheme_query={scheme_query}")
    query = scheme_query.lower()
    
    # Resolve aliases
    key = None
    if "kisan" in query and "samman" in query or "pm-kisan" in query or "pmkisan" in query:
        key = "PM-KISAN"
    elif "credit" in query or "kcc" in query or "क्रेडिट" in query:
        key = "KCC"
    elif "fasal" in query or "bima" in query or "insurance" in query or "pmfby" in query or "बीमा" in query:
        key = "PMFBY"
    elif "soil" in query or "mitti" in query or "health card" in query or "मिट्टी" in query:
        key = "Soil Health Card"
    elif "sinchayee" in query or "irrigation" in query or "drip" in query or "pmksy" in query or "सिंचाई" in query:
        key = "PMKSY"
    else:
        # Check if keys are direct substrings
        for k in SCHEMES.keys():
            if k.lower() in query:
                key = k
                break
                
    if key and key in SCHEMES:
        return {
            "ok": True,
            **SCHEMES[key]
        }
    else:
        return {
            "ok": False,
            "error": f"Could not find details for scheme '{scheme_query}'",
            "available": list(SCHEMES.keys())
        }

def get_crop_advisory(crop: str, problem: str, region: str = "") -> dict:
    """Agronomy advice for a crop problem.

    Args:
        crop: The affected crop (e.g. "Cotton", "कपास")
        problem: Description of the pest/disease/issue (e.g. "yellow leaves", "पीले पत्ते")
        region: Optional region or state (e.g. "Punjab")
    """
    print(f"  🔧 get_crop_advisory: crop={crop}, problem={problem}, region={region}")
    try:
        sub_client = genai.Client(api_key=gemini_api_key)
        region_str = f" in region {region}" if region else ""
        prompt = (
            f"Indian farmer reports: '{problem}' in their {crop} crop{region_str}. "
            f"Give the most likely cause and 2 concrete steps for this week. "
            f"Be specific about products (urea, neem oil, etc). Under 50 words."
        )
        response = sub_client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return {
            "ok": True,
            "advice": response.text
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }

# --- TEXT-TO-SPEECH (SILK) ---

def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Standard 44-byte WAV header for mono 16-bit PCM."""
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)
    data_size = len(pcm)
    chunk_size = 36 + data_size
    
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",          # ChunkID
        chunk_size,      # ChunkSize
        b"WAVE",          # Format
        b"fmt ",          # Subchunk1ID
        16,              # Subchunk1Size (16 for PCM)
        1,               # AudioFormat (1 for PCM)
        num_channels,    # NumChannels
        sample_rate,     # SampleRate
        byte_rate,       # ByteRate
        block_align,     # BlockAlign
        bits_per_sample, # BitsPerSample
        b"data",          # Subchunk2ID
        data_size        # Subchunk2Size
    )
    return header + pcm

@dataclass
class CallState:
    stream_sid: str
    ws: WebSocket
    dg_conn: any
    chat: any
    agent_speaking: bool = False
    current_tts_task: asyncio.Task = None
    buffer: list = None
    dg_context: any = None
    watchdog_task: asyncio.Task = None
    last_speech_time: float = 0.0
    start_time: float = 0.0
    num_turns: int = 0
    host: str = ""
    call_sid: str = ""
    dg_reconnected: bool = False

def convert_pcm_to_twilio_frames(pcm_bytes, src_rate=24000):
    """Resample 16-bit PCM bytes to 8kHz and convert to G.711 u-law frames using audioop."""
    if not pcm_bytes:
        return []
    try:
        # Resample to 8kHz
        resampled, _ = audioop.ratecv(pcm_bytes, 2, 1, src_rate, 8000, None)
        # Convert to u-law
        ulaw = audioop.lin2ulaw(resampled, 2)
        # Slice into 160-byte chunks (each = 20ms at 8kHz u-law)
        chunk_size = 160
        chunks = [ulaw[i:i+chunk_size] for i in range(0, len(ulaw), chunk_size)]
        return chunks
    except Exception as e:
        print(f"❌ Error converting PCM to Twilio frames: {e}")
        return []

async def speak_to_call(call_state, text):
    """Hit Silk streaming TTS endpoint and stream real-time u-law audio frames to active Twilio WebSocket."""
    if not SILK_ENDPOINT or not SILK_API_KEY:
        print("⚠️ Missing Silk credentials, skipping speech")
        return
        
    headers = {
        "Authorization": f"Bearer {SILK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Phonetic Hinglish conversion for perfect accents
    hinglish_text = convert_to_hinglish(text)
    print(f"🔊 Streaming speech: {hinglish_text}")
    
    payload = {
        "text": hinglish_text,
        "model": SILK_VOICE_ID or "muga",
        "voice_id": SILK_VOICE_ID or "muga",
        "format": "pcm_s16le",
        "sample_rate": 24000
    }
    
    try:
        # Since requests.post stream=True is a blocking network call, fetch it in a separate executor thread
        def make_request():
            return requests.post(SILK_ENDPOINT, json=payload, headers=headers, stream=True, timeout=15)
            
        response = await asyncio.to_thread(make_request)
        response.raise_for_status()
        
        # Thread-safe queue communication between background reading thread and main event loop
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        def producer():
            try:
                first_chunk = True
                header_buffer = b""
                for pcm_chunk in response.iter_content(chunk_size=960):
                    if pcm_chunk:
                        if first_chunk:
                            header_buffer += pcm_chunk
                            if len(header_buffer) >= 44:
                                first_chunk = False
                                if header_buffer.startswith(b"RIFF"):
                                    # Strip the 44-byte WAV header
                                    pcm_chunk = header_buffer[44:]
                                else:
                                    pcm_chunk = header_buffer
                                header_buffer = b""
                            else:
                                continue
                        loop.call_soon_threadsafe(queue.put_nowait, pcm_chunk)
            except Exception as e:
                print(f"❌ Error in TTS streaming producer thread: {e}")
            finally:
                if header_buffer:
                    loop.call_soon_threadsafe(queue.put_nowait, header_buffer)
                loop.call_soon_threadsafe(queue.put_nowait, None)
                
        producer_task = asyncio.create_task(asyncio.to_thread(producer))
        
        # Consume PCM chunks as they arrive, process to u-law, and stream to Twilio WS
        while True:
            pcm_chunk = await queue.get()
            if pcm_chunk is None:
                break
                
            if not call_state.agent_speaking:
                print("🛑 Speech playback cancelled (barge-in or state interrupt)")
                break
                
            # Process chunk into perfectly timed 20ms G.711 u-law chunks
            frames = convert_pcm_to_twilio_frames(pcm_chunk, src_rate=24000)
            
            for frame in frames:
                if not call_state.agent_speaking:
                    break
                    
                payload = base64.b64encode(frame).decode("utf-8")
                message = {
                    "event": "media",
                    "streamSid": call_state.stream_sid,
                    "media": {
                        "payload": payload
                    }
                }
                await call_state.ws.send_json(message)
                # sleep 20ms between frames to perfectly pace the playback
                await asyncio.sleep(0.02)
                
        await producer_task
        print(f"✅ Finished streaming TTS for CallSid={call_state.stream_sid[:8]}")
        
    except Exception as e:
        print(f"❌ Error in speak_to_call: {e}")
        # Fall back to Twilio Polly.Aditi text-to-speech redirect if Silk streaming fails
        if call_state and hasattr(call_state, "call_sid") and call_state.call_sid:
            print(f"⚠️ SILK TTS streaming failed, falling back to Twilio Polly.Aditi redirect...")
            try:
                import urllib.parse
                from twilio.rest import Client as TwilioRestClient
                
                # Construct the redirect URL with url-encoded text
                encoded_text = urllib.parse.quote(text)
                redirect_url = f"https://{call_state.host}/voice/fallback?text={encoded_text}"
                
                # Fetch Twilio REST credentials
                account_sid = os.getenv("TWILIO_ACCOUNT_SID")
                auth_token = os.getenv("TWILIO_AUTH_TOKEN")
                
                if account_sid and auth_token:
                    twilio_rest = TwilioRestClient(account_sid, auth_token)
                    
                    # Update call URL dynamically to trigger Polly.Aditi <Say>
                    def trigger_redirect():
                        return twilio_rest.calls(call_state.call_sid).update(url=redirect_url)
                        
                    await asyncio.to_thread(trigger_redirect)
                    print(f"🔄 Dynamic TwiML redirect triggered for CallSid={call_state.call_sid[:8]}")
            except Exception as redirect_err:
                print(f"❌ Twilio Polly fallback redirect failed: {redirect_err}")

def convert_to_hinglish(text: str) -> str:
    """Convert Devanagari Hindi text to natural, phonetic Roman Hinglish (Latin alphabet) for TTS.
    Do not translate English words, preserve numbers and punctuation.
    """
    if not text:
        return ""
    try:
        # Check if the text contains Devanagari characters
        # (Devanagari range is U+0900 to U+097F)
        has_hindi = any(ord(char) >= 0x0900 and ord(char) <= 0x097F for char in text)
        if not has_hindi:
            return text
            
        prompt = f"""You are a phonetics transcriber.
Convert this Devanagari Hindi text to natural, phonetic Romanized Hinglish (Hindi written in the English/Latin alphabet).
Rules:
- Write exactly how a human would pronounce it naturally.
- Keep English words in English (e.g. 'Indore', 'Wheat', 'api-key', 'market').
- Do not translate the meaning, only transliterate the sound/script (e.g., 'जी, भाई' -> 'Ji, bhai', 'मौसम' -> 'mausam').
- Return ONLY the Roman Hinglish text, no explanations, no wrappers.

Text to convert:
{text}
"""
        response = client.models.generate_content(
            model="gemini-flash-latest",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"Error converting to Hinglish: {e}")
        return text

def synthesize_silk(text: str) -> bytes:
    """Synthesize text to speech using Silk TTS API, returning WAV bytes."""
    if not SILK_ENDPOINT or not SILK_API_KEY:
        return b""
        
    headers = {
        "Authorization": f"Bearer {SILK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Easily customizable variables as requested by user:
    custom_voice_id = SILK_VOICE_ID or "muga"
    custom_format = "pcm_s16le"
    
    # Convert Devanagari Unicode Hindi to Roman Hinglish phonetics
    hinglish_text = convert_to_hinglish(text)
    print(f"🔊 Synthesizing: {hinglish_text}")
    
    # Standard production payload for Silk POST v1/tts API
    payload = {
        "text": hinglish_text,
        "model": custom_voice_id
    }
    
    try:
        response = requests.post(SILK_ENDPOINT, json=payload, headers=headers, timeout=15)
        response.raise_for_status()
        content_bytes = response.content
        
        # Smart guard: if API returns a complete WAV file directly, return it as-is.
        # Otherwise, wrap the raw PCM bytes in a WAV header.
        if content_bytes.startswith(b"RIFF"):
            return content_bytes
        return pcm_to_wav(content_bytes, SILK_SAMPLE_HZ)
    except Exception as e:
        print(f"Error in synthesize_silk: {e}")
        return b""

# --- CHAT LAYER ---

chats = {}

app = FastAPI(title="Krishi Mitra")

_twilio_sdk_dir = os.path.join(os.path.dirname(__file__), "node_modules", "@twilio", "voice-sdk", "dist")
if os.path.isdir(_twilio_sdk_dir):
    app.mount("/vendor/twilio", StaticFiles(directory=_twilio_sdk_dir), name="twilio-sdk")

SYSTEM_INSTRUCTION = """You are "Kisan Sahayak" (Farmer's Assistant), a highly empathetic, knowledgeable, and helpful AI voice assistant designed specifically for Indian farmers.

You are communicating with farmers over a basic phone call. Your users may have limited formal education and are interacting with you purely through voice. They will speak in Hindi, English, or Hinglish.

Your core capabilities are:
1. Providing live Mandi prices for 16+ crops (via Agmarknet).
2. Providing a 3-day weather forecast and rainfall probability.
3. Explaining Government Schemes (PM-KISAN, PMFBY, KCC, Soil Health Card, PMKSY).
4. Offering crop-specific agronomy advice for pests and diseases.

CRITICAL RULES FOR VOICE INTERACTION:
1. MATCH THE LANGUAGE: Always reply in the exact language and dialect the user speaks. If they speak Hinglish, reply in simple, natural Hinglish. If pure Hindi, reply in simple Hindi.
2. BE CONCISE: Phone calls require extreme brevity. Keep every response under 3 to 4 short sentences (maximum 40-50 words). Break complex information down.
3. NO VISUAL FORMATTING: NEVER use markdown, bullet points, asterisks (*), hashtags (#), or emojis. Your output is being read by a Text-to-Speech engine. Use natural punctuation (commas, periods, question marks) to create natural pauses for the voice.
4. GATHER CONTEXT GENTLY: If you need information (like their district for weather, or crop name for mandi prices), ask for exactly one piece of information at a time.
5. ALWAYS END WITH A QUESTION: Keep the conversation moving by ending your response with a simple, clear question (e.g., "Which crop's price would you like to know?", "Which district are you calling from?", "Would you like to know the documents needed for this scheme?").
6. TONE: Be respectful, encouraging, and warm. Use terms like "Kisan bhai" or "Sir/Madam" where appropriate, but do not overdo it.
7. STT FORGIVENESS: The user's transcribed speech might have spelling errors or missing words because they are in a noisy field. Infer their intent kindly and ask for clarification if you truly do not understand.

SCENARIO HANDLING:
- Mandi Prices: If a user asks for a price, ensure you have both the crop name and their district/mandi name. If not, ask for the missing detail.
- Weather: Ensure you have their district. Focus heavily on rainfall probability as it impacts sowing/harvesting.
- Schemes: Never read out a whole scheme at once. Give a 1-sentence summary, then ask: "Would you like to know who is eligible, or how to apply?"
- Agronomy: Give safe, standard advice based on symptoms. Always add a brief disclaimer to consult their local Krishi Vigyan Kendra (KVK) or agro-dealer for severe chemical applications.

Begin the conversation (when the user says hello) by introducing yourself quickly:
"Namaste! I am your Kisan Sahayak. I can help you with mandi prices, weather, farming advice, and government schemes. How can I help you today?" """

def get_chat(session_id: str):
    """Retrieve or initialize Gemini chat session."""
    if session_id not in chats:
        chats[session_id] = client.chats.create(
            model="gemini-flash-latest",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                tools=[get_mandi_prices, get_weather, get_scheme_info, get_crop_advisory]
            )
        )
    return chats[session_id]

# Read HTML template at startup
template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
try:
    with open(template_path, "r", encoding="utf-8") as f:
        INDEX_HTML = f.read()
except Exception as e:
    INDEX_HTML = f"<h1>Template Load Error: {e}</h1>"

@app.get("/", response_class=HTMLResponse)
def read_root():
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"<h1>Template Load Error: {e}</h1>"

@app.get("/healthz")
def health_check():
    return {"ok": True}

@app.post("/api/text")
def handle_text(text: str = Form(...), session_id: str = Form(...)):
    chat = get_chat(session_id)
    response = chat.send_message(text)
    reply_text = response.text
    
    audio_wav_b64 = None
    if SILK_ENDPOINT and SILK_API_KEY:
        wav_bytes = synthesize_silk(reply_text)
        if wav_bytes:
            audio_wav_b64 = base64.b64encode(wav_bytes).decode("utf-8")
            
    return {
        "transcript": text,
        "reply": reply_text,
        "audio_wav_b64": audio_wav_b64
    }

@app.post("/api/voice")
async def handle_voice(audio: UploadFile = File(...), session_id: str = Form(...)):
    try:
        # 1. Read audio bytes
        try:
            audio_bytes = await audio.read()
        except Exception as e:
            return JSONResponse(status_code=400, content={"error": f"Failed to read audio file: {e}"})

        # 2. Transcribe via Deepgram REST
        try:
            dg_client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
            
            # Using v7+ media.transcribe_file method
            response = dg_client.listen.v1.media.transcribe_file(
                request=audio_bytes,
                model="nova-3",
                smart_format=True,
                punctuate=True,
                language="multi"
            )
            
            # Robust extraction of the transcript text
            try:
                transcript = response.results.channels[0].alternatives[0].transcript
            except AttributeError:
                if hasattr(response, "to_dict"):
                    res_dict = response.to_dict()
                else:
                    res_dict = response
                transcript = res_dict["results"]["channels"][0]["alternatives"][0]["transcript"]
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Deepgram transcription failed: {e}"})

        # 3. If transcript is empty/whitespace, return 400
        if not transcript or not transcript.strip():
            return JSONResponse(status_code=400, content={"error": "no speech detected"})

        # 4. Log transcript
        print(f"\n🎤 {session_id[:8]}: {transcript}")

        # 5. Pass transcript through Gemini chat
        try:
            chat = get_chat(session_id)
            gemini_response = chat.send_message(transcript)
            reply = gemini_response.text
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": f"Gemini chat processing failed: {e}"})

        # 6. Log reply
        print(f"🤖 reply: {reply}")

        # 7. Synthesize via synthesize_silk
        audio_wav_b64 = None
        if SILK_ENDPOINT and SILK_API_KEY:
            try:
                wav_bytes = synthesize_silk(reply)
                if wav_bytes:
                    audio_wav_b64 = base64.b64encode(wav_bytes).decode("utf-8")
            except Exception as e:
                print(f"TTS synthesis failed: {e}")

        # 8. Return full JSON response
        return {
            "transcript": transcript,
            "reply": reply,
            "audio_wav_b64": audio_wav_b64
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"An unexpected error occurred: {e}"})

# --- PHONE SESSION AND POLISH UTILITIES ---

import random

THINKING_SOUNDS = [
    "एक मिनट भाई, जानकारी निकाल रहा हूँ...",
    "थोड़ा समय दीजिए जी, मंडी का भाव देख रहा हूँ...",
    "बस एक moment भाई, अभी पता करता हूँ..."
]
cached_thinking_frames = []

def pre_synthesize_thinking_sounds():
    """Pre-synthesize and cache thinking filler sounds at startup to guarantee zero latency."""
    if not SILK_ENDPOINT or not SILK_API_KEY:
        print("⚠️ Missing Silk credentials, skipping pre-synthesis")
        return
    print("🎙️ Pre-synthesizing thinking sounds...")
    for text in THINKING_SOUNDS:
        try:
            # Phonetic Hinglish conversion
            hinglish_text = convert_to_hinglish(text)
            wav_bytes = synthesize_silk(text)
            if wav_bytes:
                if wav_bytes.startswith(b"RIFF"):
                    pcm = wav_bytes[44:]
                else:
                    pcm = wav_bytes
                frames = convert_pcm_to_twilio_frames(pcm, src_rate=24000)
                if frames:
                    cached_thinking_frames.append(frames)
                    print(f"✅ Cached: {text}")
        except Exception as e:
            print(f"⚠️ Failed to cache thinking sound: {e}")

def reset_watchdog(state: CallState):
    """Reset the 15-second inactivity watchdog and 45-second call timeout gracefully."""
    if state.watchdog_task and not state.watchdog_task.done():
        state.watchdog_task.cancel()
        
    async def watchdog():
        try:
            # Wait 15 seconds for farmer activity
            await asyncio.sleep(15)
            if state.last_speech_time > 0 and (asyncio.get_event_loop().time() - state.last_speech_time >= 15):
                if not state.agent_speaking:
                    prompt_text = "क्या आप हैं भाई? कोई और सवाल है?"
                    print("⏳ 15 seconds of silence, playing timeout prompt...")
                    
                    async def speak_prompt():
                        state.agent_speaking = True
                        await speak_to_call(state, prompt_text)
                        state.agent_speaking = False
                        
                    state.current_tts_task = asyncio.create_task(speak_prompt())
            
            # Wait another 30 seconds (total 45 seconds)
            await asyncio.sleep(30)
            if state.last_speech_time > 0 and (asyncio.get_event_loop().time() - state.last_speech_time >= 45):
                print("⏳ 45 seconds of absolute silence, hanging up gracefully...")
                await state.ws.send_json({"event": "stop"})
        except asyncio.CancelledError:
            pass
            
    state.watchdog_task = asyncio.create_task(watchdog())

def log_call_summary(call_sid: str, state: CallState):
    """Log a complete, professional summary of the voice call including duration, turns, and tools used."""
    duration = 0.0
    if state.start_time > 0:
        duration = asyncio.get_event_loop().time() - state.start_time
        
    num_turns = state.num_turns
    tools_used = set()
    
    if state.chat:
        try:
            history = state.chat.get_history()
            # Extract turns
            user_turns = [c for c in history if c.role == "user"]
            num_turns = len(user_turns)
            
            # Extract tools
            for c in history:
                if c.role == "model":
                    for part in c.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            tools_used.add(part.function_call.name)
        except Exception as e:
            print(f"⚠️ Error parsing chat history for logging: {e}")
            
    print("\n" + "="*50)
    print(f"📞 VOICE CALL SUMMARY: {call_sid}")
    print(f"⏱️  Duration:   {duration:.1f} seconds")
    print(f"💬  Turns:      {num_turns}")
    print(f"🔧  Tools Used: {list(tools_used)}")
    print("="*50 + "\n")

@app.on_event("startup")
async def startup_event():
    """Trigger startup pre-synthesis of thinking sound fillers."""
    # Run in background thread to avoid blocking server start
    asyncio.create_task(asyncio.to_thread(pre_synthesize_thinking_sounds))

webrtc_credentials = {}

@app.get("/api/voice/token")
def get_voice_token(request: Request):
    """Generate a Twilio Voice WebRTC Access Token using env-var credentials."""
    account_sid   = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token    = os.getenv("TWILIO_AUTH_TOKEN")
    api_key       = os.getenv("TWILIO_API_KEY")
    api_secret    = os.getenv("TWILIO_API_SECRET")
    twiml_app_sid = os.getenv("TWILIO_TWIML_APP_SID")

    if not account_sid or not auth_token:
        return JSONResponse(status_code=500, content={"error": "Missing TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN"})

    try:
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant
        from twilio.rest import Client as TwilioRestClient

        # If API key not set in env, create one dynamically (first-run only)
        if not api_key or not api_secret:
            print("⚠️ TWILIO_API_KEY/SECRET not set — creating a new key (set these as env vars to avoid repeating this)")
            tw = TwilioRestClient(account_sid, auth_token)
            new_key = tw.new_keys.create(friendly_name="KrishiMitraWebRTC")
            api_key    = new_key.sid
            api_secret = new_key.secret
            print(f"🔑 Created API Key: {api_key}  — add TWILIO_API_KEY and TWILIO_API_SECRET to your env vars!")

        # If TwiML App SID not set, create/find it
        if not twiml_app_sid:
            public_host = os.getenv("PUBLIC_HOST") or request.headers.get("host", "")
            public_host = public_host.replace("http://", "").replace("https://", "").rstrip("/")
            voice_url   = f"https://{public_host}/voice/incoming"
            print(f"⚠️ TWILIO_TWIML_APP_SID not set — looking up or creating TwiML App → {voice_url}")
            tw = TwilioRestClient(account_sid, auth_token)
            apps = tw.applications.list(friendly_name="KrishiMitraVoiceApp")
            if apps:
                twiml_app_sid = apps[0].sid
                tw.applications(twiml_app_sid).update(voice_url=voice_url)
            else:
                new_app = tw.applications.create(
                    friendly_name="KrishiMitraVoiceApp",
                    voice_url=voice_url,
                    voice_method="POST"
                )
                twiml_app_sid = new_app.sid
            print(f"📱 TwiML App SID: {twiml_app_sid} — add TWILIO_TWIML_APP_SID to your env vars!")

        # Build the JWT Access Token
        token = AccessToken(account_sid, api_key, api_secret, identity="farmer_browser")
        voice_grant = VoiceGrant(outgoing_application_sid=twiml_app_sid, incoming_allow=True)
        token.add_grant(voice_grant)

        token_jwt = token.to_jwt()
        if isinstance(token_jwt, bytes):
            token_jwt = token_jwt.decode()

        return {"token": token_jwt, "identity": "farmer_browser", "twiml_app_sid": twiml_app_sid}

    except Exception as e:
        print(f"❌ Failed to generate Voice Access Token: {e}")
        return JSONResponse(status_code=500, content={"error": f"Token generation failed: {e}"})


@app.api_route("/voice/fallback", methods=["GET", "POST"])
def voice_fallback(request: Request, text: str):
    """Twilio fallback text-to-speech redirect route using premium Polly.Aditi when Silk has errors."""
    host = request.headers.get("host")
    wss_url = f"wss://{host}/voice/stream"
    
    response = VoiceResponse()
    response.say(text, voice="Polly.Aditi", language="hi-IN")
    connect = response.connect()
    connect.stream(url=wss_url)
    
    return Response(content=str(response), media_type="application/xml")

@app.api_route("/voice/incoming", methods=["GET", "POST"])
def voice_incoming(request: Request):
    """Twilio voice incoming webhook returning TwiML response with Media Stream connect."""
    host = request.headers.get("host")
    wss_url = f"wss://{host}/voice/stream"

    response = VoiceResponse()
    response.say("कृषि मित्र में आपका स्वागत है", voice="Polly.Aditi", language="hi-IN")
    connect = response.connect()
    connect.stream(url=wss_url)

    return Response(content=str(response), media_type="application/xml")

@app.post("/api/call/outbound")
def call_outbound(request: Request):
    """Dial the user's hardcoded phone number from the Twilio number and connect them to the Kisan Sahayak voice agent."""
    from twilio.rest import Client as TwilioRestClient

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    to_number   = "+918168323826"

    if not (account_sid and auth_token and from_number):
        return JSONResponse(
            status_code=500,
            content={"error": "Missing TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, or TWILIO_PHONE_NUMBER"},
        )

    public_host = os.getenv("PUBLIC_HOST") or request.headers.get("host")
    twiml_url = f"https://{public_host}/voice/incoming"

    try:
        client = TwilioRestClient(account_sid, auth_token)
        call = client.calls.create(
            to=to_number,
            from_=from_number,
            url=twiml_url,
            machine_detection="Enable",
            machine_detection_timeout=10,
        )
        print(f"📲 Outbound call initiated (AMD on): SID={call.sid}  →  {to_number}  (TwiML: {twiml_url})")
        return {"ok": True, "call_sid": call.sid, "to": to_number, "from": from_number}
    except Exception as e:
        print(f"❌ Outbound call failed: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/call/{call_sid}/status")
def call_status(call_sid: str):
    """Return Twilio call status including answered_by (AMD result) for diagnostics."""
    from twilio.rest import Client as TwilioRestClient
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
    if not (account_sid and auth_token):
        return JSONResponse(status_code=500, content={"error": "Missing Twilio creds"})
    try:
        call = TwilioRestClient(account_sid, auth_token).calls(call_sid).fetch()
        return {
            "sid": call.sid,
            "status": call.status,
            "duration": call.duration,
            "answered_by": call.answered_by,
            "forwarded_from": call.forwarded_from,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

call_sessions = {}
async_dg = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY)

async def reconnect_deepgram(call_sid: str):
    """Reconnect to Deepgram once on unexpected websocket disconnect to safeguard session continuity."""
    state = call_sessions.get(call_sid)
    if not state or state.dg_reconnected:
        return
    state.dg_reconnected = True
    print(f"🔄 Attempting to reconnect Deepgram for CallSid={call_sid[:8]}...")
    try:
        # Clean up old connection
        if hasattr(state, "dg_context"):
            try:
                await state.dg_context.__aexit__(None, None, None)
            except Exception:
                pass
                
        # Re-establish Deepgram connection
        ctx = async_dg.listen.v1.connect(
            model="nova-3",
            language="multi",
            encoding="mulaw",
            sample_rate=8000,
            channels=1,
            smart_format=True,
            interim_results=True,
            utterance_end_ms="1000",
            vad_events=True,
            endpointing=300
        )
        dg_conn = await ctx.__aenter__()
        state.dg_conn = dg_conn
        state.dg_context = ctx
        
        # Register standard handlers on new connection
        dg_conn.on("message", lambda msg: state.buffer.append(msg) if hasattr(msg, "channel") else None)
        dg_conn.on("error", lambda err: print(f"❌ Reconnected Deepgram error: {err}"))
        asyncio.create_task(dg_conn.start_listening())
        print("✅ Deepgram successfully reconnected!")
    except Exception as e:
        print(f"❌ Failed to reconnect Deepgram: {e}. Hanging up call.")
        await state.ws.send_json({"event": "stop"})

@app.websocket("/voice/stream")
async def voice_stream(ws: WebSocket):
    await ws.accept()
    print("🔌 WebSocket connection accepted from Twilio")
    stream_sid = None
    call_sid = None
    in_media_count = 0
    
    try:
        while True:
            message = await ws.receive_text()
            data = json.loads(message)
            event = data.get("event")
            
            if event == "connected":
                print("🟢 Twilio Media Stream connected")
                
            elif event == "start":
                stream_sid = data.get("streamSid")
                call_sid = data.get("start", {}).get("callSid")
                print(f"🚀 Twilio Stream started: StreamSid={stream_sid}, CallSid={call_sid}")
                
                if call_sid:
                    try:
                        # Connect to Deepgram async WebSocket
                        ctx = async_dg.listen.v1.connect(
                            model="nova-3",
                            language="multi",
                            encoding="mulaw",
                            sample_rate=8000,
                            channels=1,
                            smart_format=True,
                            interim_results=True,
                            utterance_end_ms="1000",
                            vad_events=True,
                            endpointing=300
                        )
                        dg_conn = await ctx.__aenter__()
                        
                        # Initialize CallState and store in call_sessions
                        chat_session = get_chat(call_sid)
                        state = CallState(
                            stream_sid=stream_sid,
                            ws=ws,
                            dg_conn=dg_conn,
                            chat=chat_session,
                            buffer=[],
                            start_time=asyncio.get_event_loop().time(),
                            last_speech_time=asyncio.get_event_loop().time(),
                            host=ws.headers.get("host") or "",
                            call_sid=call_sid
                        )
                        call_sessions[call_sid] = state
                        
                        # Start inactivity timeout watchdog
                        reset_watchdog(state)
                        
                        # Register transcript handler (defensive: handle dict/list/object shapes)
                        _dbg_dumped = {"done": False}
                        def _as_dict(obj):
                            if isinstance(obj, dict):
                                return obj
                            for attr in ("model_dump", "dict", "to_dict"):
                                fn = getattr(obj, attr, None)
                                if callable(fn):
                                    try:
                                        return fn()
                                    except Exception:
                                        pass
                            return None

                        def handle_message(message_data, call_id=call_sid):
                            d = _as_dict(message_data) or {}
                            if not _dbg_dumped["done"]:
                                _dbg_dumped["done"] = True
                                try:
                                    print(f"🔎 First Deepgram message shape: {json.dumps(d, default=str)[:400]}")
                                except Exception:
                                    print(f"🔎 First Deepgram message (raw): {message_data!r}")

                            msg_type = d.get("type") or getattr(message_data, "type", None)
                            if msg_type == "SpeechStarted":
                                call_state = call_sessions.get(call_id)
                                if call_state and call_state.agent_speaking:
                                    call_state.agent_speaking = False
                                    if call_state.current_tts_task and not call_state.current_tts_task.done():
                                        call_state.current_tts_task.cancel()
                                    asyncio.create_task(call_state.ws.send_json({
                                        "event": "clear",
                                        "streamSid": call_state.stream_sid
                                    }))
                                    print("🚫 Interrupted active speech (SpeechStarted VAD Barge-in)")
                                return

                            # Pull transcript out, regardless of channel being dict/list/model
                            transcript = ""
                            try:
                                ch = d.get("channel")
                                alts = None
                                if isinstance(ch, dict):
                                    alts = ch.get("alternatives")
                                elif isinstance(ch, list):
                                    # Could be a list of channels, or already a list of alternatives
                                    if ch and isinstance(ch[0], dict) and "alternatives" in ch[0]:
                                        alts = ch[0].get("alternatives")
                                    else:
                                        alts = ch
                                if alts and isinstance(alts, list) and alts:
                                    first = alts[0]
                                    transcript = first.get("transcript", "") if isinstance(first, dict) else getattr(first, "transcript", "")
                            except Exception as e:
                                print(f"⚠️ transcript parse failed: {type(e).__name__}: {e}")
                                return

                            if transcript and transcript.strip():
                                is_final = d.get("is_final", False)
                                speech_final = d.get("speech_final", False)

                                if is_final:
                                    call_state = call_sessions.get(call_id)
                                    if call_state:
                                        # Barge-in cleanup if speaking
                                        if call_state.current_tts_task and not call_state.current_tts_task.done():
                                            call_state.agent_speaking = False
                                            call_state.current_tts_task.cancel()
                                            asyncio.create_task(call_state.ws.send_json({
                                                "event": "clear",
                                                "streamSid": call_state.stream_sid
                                            }))
                                            print("🚫 Interrupted active speech (Barge-in on transcription)")

                                        # Reset inactivity watchdog since farmer spoke
                                        call_state.last_speech_time = asyncio.get_event_loop().time()
                                        reset_watchdog(call_state)

                                        call_state.buffer.append(transcript)
                                        print(f"📝 partial: {transcript}")

                                        if speech_final:
                                            utterance = " ".join(call_state.buffer).strip()
                                            call_state.buffer = []

                                            if utterance:
                                                print(f"🎤 {call_id[:8]}: {utterance}")
                                                call_state.num_turns += 1

                                                async def fetch_and_speak():
                                                    response_done = asyncio.Event()

                                                    async def thinking_sound_timer():
                                                        try:
                                                            await asyncio.sleep(1.5)
                                                            if not response_done.is_set() and cached_thinking_frames:
                                                                frames = random.choice(cached_thinking_frames)
                                                                print("💭 Tool/inference taking > 1.5s, playing cached thinking filler...")
                                                                for frame in frames:
                                                                    if response_done.is_set() or not call_sessions.get(call_id):
                                                                        break
                                                                    payload = base64.b64encode(frame).decode("utf-8")
                                                                    await call_state.ws.send_json({
                                                                        "event": "media",
                                                                        "streamSid": call_state.stream_sid,
                                                                        "media": {"payload": payload}
                                                                    })
                                                                    await asyncio.sleep(0.02)
                                                        except asyncio.CancelledError:
                                                            pass

                                                    timer_task = asyncio.create_task(thinking_sound_timer())

                                                    try:
                                                        def run_gemini():
                                                            return call_state.chat.send_message(utterance)

                                                        gemini_response = await asyncio.to_thread(run_gemini)
                                                        response_done.set()
                                                        timer_task.cancel()

                                                        reply = gemini_response.text
                                                        print(f"🤖 {call_id[:8]}: {reply}")

                                                        async def speak_task():
                                                            call_state.agent_speaking = True
                                                            await speak_to_call(call_state, reply)
                                                            call_state.agent_speaking = False

                                                        call_state.current_tts_task = asyncio.create_task(speak_task())

                                                    except Exception as gemini_err:
                                                        response_done.set()
                                                        timer_task.cancel()
                                                        print(f"❌ Gemini processing error: {gemini_err}")
                                                        error_prompt = "माफ कीजिए भाई, मुझे समझने में कुछ त्रुटि हुई। कृपया दोबारा पूछिए।"

                                                        async def speak_error():
                                                            call_state.agent_speaking = True
                                                            await speak_to_call(call_state, error_prompt)
                                                            call_state.agent_speaking = False

                                                        call_state.current_tts_task = asyncio.create_task(speak_error())

                                                asyncio.create_task(fetch_and_speak())

                        dg_conn.on("message", handle_message)
                        dg_conn.on("open", lambda _e: print(f"🟢 Deepgram WS opened for CallSid={call_sid[:8]}"))
                        dg_conn.on("close", lambda _e: print(f"🔴 Deepgram WS closed for CallSid={call_sid[:8]}"))
                        dg_conn.on("error", lambda err: print(f"❌ Deepgram error for CallSid={call_sid[:8]}: {err!r}"))

                        # Start background listening task with explicit error logging
                        async def _dg_listen():
                            try:
                                await dg_conn.start_listening()
                            except Exception as e:
                                import traceback
                                print(f"❌ Deepgram listener crashed for CallSid={call_sid[:8]}: {e}")
                                traceback.print_exc()
                        asyncio.create_task(_dg_listen())

                        # Keep Deepgram WS alive across pauses in farmer speech
                        async def _dg_keepalive():
                            try:
                                while call_sid in call_sessions:
                                    await asyncio.sleep(5)
                                    cs = call_sessions.get(call_sid)
                                    if cs:
                                        await cs.dg_conn.send_keep_alive()
                            except Exception as e:
                                print(f"⚠️ Deepgram keepalive ended for CallSid={call_sid[:8]}: {e}")
                        asyncio.create_task(_dg_keepalive())
                        
                        # Store context manager to allow exit during cleanup
                        state.dg_context = ctx
                        
                        print(f"🔗 Bound CallState session for CallSid={call_sid}")
                    except Exception as e:
                        print(f"❌ Failed to start Deepgram stream: {e}")
                        
            elif event == "media":
                in_media_count += 1
                if in_media_count % 50 == 0:
                    print(f"📥 Received {in_media_count} media frames from Twilio")
                
                payload = data.get("media", {}).get("payload")
                if payload:
                    raw_bytes = base64.b64decode(payload)
                    if call_sid and call_sid in call_sessions:
                        cs = call_sessions[call_sid]
                        # Skip forwarding while the agent is speaking — prevents the
                        # bot's own TTS audio (looping back via speaker→mic) from being
                        # detected as a user utterance and cutting itself off.
                        if cs.agent_speaking:
                            continue
                        try:
                            await cs.dg_conn.send_media(raw_bytes)
                        except Exception as e:
                            if in_media_count % 50 == 1:
                                print(f"⚠️ send_media failed (frame {in_media_count}): {type(e).__name__}: {e}")

            elif event == "stop":
                print(f"⏹️ Twilio Stream stopped: StreamSid={stream_sid}")
                break

    except WebSocketDisconnect:
        print(f"🔌 WebSocket disconnected: StreamSid={stream_sid}")
    except Exception as e:
        import traceback
        print(f"❌ Error in voice stream: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        # Clean up Deepgram and CallState session
        if call_sid:
            state = call_sessions.pop(call_sid, None)
            if state:
                try:
                    # Cancel active watchdog
                    if state.watchdog_task and not state.watchdog_task.done():
                        state.watchdog_task.cancel()
                        
                    # Cancel active TTS task if running
                    if state.current_tts_task and not state.current_tts_task.done():
                        state.current_tts_task.cancel()
                    
                    # Log call summary dynamically
                    log_call_summary(call_sid, state)
                    
                    # Clean up Deepgram
                    await state.dg_conn.send_close_stream()
                    if hasattr(state, "dg_context"):
                        await state.dg_context.__aexit__(None, None, None)
                        
                    print(f"🔌 Cleaned up CallState session for CallSid={call_sid}")
                except Exception as e:
                    print(f"⚠️ Error cleaning up CallState session: {e}")
