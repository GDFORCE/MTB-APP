param([int]$Port = 8502)
$ErrorActionPreference = 'Stop'
$labApp = Join-Path $PSScriptRoot 'app.py'
python -m streamlit run $labApp --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false
