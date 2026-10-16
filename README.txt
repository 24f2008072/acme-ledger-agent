ACME APPLIANCES LEDGER Q&A

Files:
- main.py
- requirements.txt

Run locally (PowerShell):
1. cd to the folder containing these files
2. python -m venv venv
3. .\venv\Scripts\Activate.ps1
4. pip install -r requirements.txt
5. uvicorn main:app --host 0.0.0.0 --port 8000

Check:
Open http://127.0.0.1:8000/
Expected: JSON with status=ok.

Test in PowerShell:
Invoke-RestMethod -Uri http://127.0.0.1:8000/ -Method Post -ContentType 'application/json' -Body '{"question":"What was the total revenue in USD from the North region in March 2026?"}'

Render:
Build command: pip install -r requirements.txt
Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
Public endpoint: https://YOUR-SERVICE.onrender.com/

Note:
The app fetches the export endpoint first and falls back to paging through /orders.
It caches loaded data in memory for speed.
