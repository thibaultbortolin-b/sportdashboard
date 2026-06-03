#!/bin/bash
set -e

echo "🏃 Strava Dashboard Setup"
echo "========================="

# Create virtual env if needed
if [ ! -d ".venv" ]; then
  echo "→ Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

# Copy .env if not exists
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo ""
  echo "⚠️  Created .env — fill in your Strava credentials:"
  echo "   STRAVA_CLIENT_ID=..."
  echo "   STRAVA_CLIENT_SECRET=..."
  echo "   SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  echo ""
  echo "   Get credentials at: https://www.strava.com/settings/api"
  echo "   Set 'Authorization Callback Domain' to: localhost"
  echo ""
else
  echo "→ .env already exists, skipping"
fi

echo ""
echo "✅ Ready! Run: source .venv/bin/activate && uvicorn main:app --reload"
