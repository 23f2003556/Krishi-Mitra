# Krishi Mitra

A FastAPI voice agent designed to assist Indian farmers with agricultural queries.

## Run locally

To run the application locally, follow these steps:

1. **Clone the repository and navigate to the project directory:**
   ```bash
   cd "Krishi Mitra"
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables:**
   Copy the example environment file and fill in your API keys:
   ```bash
   cp .env.example .env
   ```

5. **Start the development server:**
   ```bash
   uvicorn app:app --reload
   ```

The application will be accessible at [http://localhost:8000](http://localhost:8000).
