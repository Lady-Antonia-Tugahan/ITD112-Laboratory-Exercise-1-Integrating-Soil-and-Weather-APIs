# ITD112 Laboratory Exercise 1 — Integrating Soil and Weather APIs


- **FastAPI backend**: request construction, timeout/retry/backoff, disk cache, SoilGrids fair-use pause, JSON parsing, `d_factor` conversion, joining, water balance, summaries, provenance.
- **Next.js frontend**: vibrant green dashboard, responsive controls, charts, audit log, data table, CSV export and loading/error states.


## Quick start

### 1. Requirements
Install **Python 3.10+** and **Node.js 20+**.

### 2. Start the backend
Open Terminal 1:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
# Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Backend: http://localhost:8000  
Interactive API docs: http://localhost:8000/docs

### 3. Start the frontend
Open Terminal 2 from the project root:

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:3000

### 4. Use the app
1. Select sites.
2. Choose start/end dates.
3. Click **Run integration**.
4. First uncached SoilGrids runs may take time because the backend deliberately pauses about 12 seconds between calls.
5. Repeated identical requests use `backend/cache/` and are much faster.
6. Review charts, response logs, summary table, provenance, and CSV exports.

## API endpoints
- `GET /api/health`
- `GET /api/sites`
- `POST /api/integrate`

Example request:
```json
{"sites":["Iligan City","Malaybalay"],"start_date":"2025-01-01","end_date":"2025-12-31"}
```

## Extensions

1. **Depth dimension** — SoilGrids is queried for 0–5 cm, 5–15 cm and 15–30 cm. The dashboard includes site-selectable sand, silt and clay profile charts.
2. **Forecast + archive** — Open-Meteo Historical Archive remains the source for the selected historical period, while the Forecast API adds the coming 16 days for the selected sites.
3. **Soil uncertainty** — SoilGrids `mean`, `Q0.05` and `Q0.95` values are requested. The profile charts show the mean with Q0.05–Q0.95 error bars.
4. **Split frontend** — Next.js 16.3.6 calls a FastAPI backend; browser UI and data-integration logic are separated.

The original Q1–Q6 analyses remain in the dashboard. Extension data can also be exported as `forecast_16_day.csv` and `soil_profile_uncertainty.csv`.

### API response additions

`POST /api/integrate` now returns `forecast` and `soil_profile` arrays in addition to the original `daily`, `summary`, `logs`, `soil_audit`, `requests` and `meta` fields.

Forecast cache entries expire after one hour. SoilGrids still uses one request per site containing all requested depths/properties/quantiles, followed by the lab's fair-use pause between uncached site calls.
