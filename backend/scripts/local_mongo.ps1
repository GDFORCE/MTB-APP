# Start a single-node MongoDB replica set on 127.0.0.1 for UCTSM tests/dev.
#
#   $env:UCTSM_LOCAL_MONGO_HOME = "D:\path\to\local-mongo"   # contains server\...\bin\mongod.exe
#   .\scripts\local_mongo.ps1
#
# Transactions need a replica set, so a plain mongod is not enough. Bound to
# localhost only. NEVER point tests at a shared cluster (Atlas).
param(
    [string]$Home_ = $env:UCTSM_LOCAL_MONGO_HOME,
    [int]$Port = 27017,
    [string]$ReplSet = "rs0"
)
if (-not $Home_) { throw "Set UCTSM_LOCAL_MONGO_HOME to the folder holding the MongoDB server download." }
$mongod = Get-ChildItem -Path $Home_ -Recurse -Filter mongod.exe | Select-Object -First 1 -ExpandProperty FullName
if (-not $mongod) { throw "mongod.exe not found under $Home_" }
$data = Join-Path $Home_ "data\$ReplSet"
$logs = Join-Path $Home_ "logs"
New-Item -ItemType Directory -Force -Path $data, $logs | Out-Null
Start-Process -FilePath $mongod -WindowStyle Hidden -ArgumentList @(
    "--replSet", $ReplSet, "--bind_ip", "127.0.0.1", "--port", $Port,
    "--dbpath", $data, "--logpath", (Join-Path $logs "mongod.log"))
Write-Host "mongod starting on 127.0.0.1:$Port (replica set $ReplSet)."
Write-Host "First run only - initiate it once:"
Write-Host "  python -c `"from pymongo import MongoClient as C; c=C('mongodb://127.0.0.1:$Port/?directConnection=true'); print(c.admin.command('replSetInitiate', {'_id':'$ReplSet','members':[{'_id':0,'host':'localhost:$Port'}]}))`""
