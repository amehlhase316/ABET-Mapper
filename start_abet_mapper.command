#!/bin/zsh

cd "${0:A:h}" || exit 1

if ! python3 -c "import requests" 2>/dev/null; then
  echo "The Python requests package is required."
  echo "Run: python3 -m pip install -r requirements.txt"
  exit 1
fi

echo -n "Paste your Canvas API token (input will be hidden): "
read -rs ABET_TOKEN
echo

if [[ -z "$ABET_TOKEN" ]]; then
  echo "No token entered."
  exit 1
fi

ABET_MAPPER_CANVAS_TOKEN="$ABET_TOKEN" python3 server.py
