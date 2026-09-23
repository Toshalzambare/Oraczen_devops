@echo off
REM HPA load test: sends rapid POST requests to trigger CPU-based autoscaling
REM Usage: load_test.bat [HOST] [DURATION_SECONDS]

SET HOST=%1
IF "%HOST%"=="" SET HOST=http://localhost:8000

SET DURATION=%2
IF "%DURATION%"=="" SET DURATION=120

ECHO Sending load to %HOST% for %DURATION% iterations...

FOR /L %%i IN (1,1,%DURATION%) DO (
    curl -s -X POST "%HOST%/notes" ^
        -H "content-type: application/json" ^
        -d "{\"title\":\"load-test\",\"content\":\"testing HPA scaling\"}" ^
        -o NUL
)

ECHO Done. Check HPA status with:
ECHO   kubectl get hpa -n ^<namespace^>
ECHO   kubectl get pods -n ^<namespace^>
