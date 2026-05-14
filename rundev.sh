#!/bin/bash
# Processor Assistant — Development Runner
# Starts langgraph dev, archives previous log, tees output to local/logs/

set -e

mkdir -p local/logs local/threads

# Archive previous log
if [ -f local/logs/langgraph_dev.log ]; then
    ts=$(date +"%Y%m%d_%H%M%S")
    mv local/logs/langgraph_dev.log "local/logs/langgraph_dev_${ts}.log"
    echo "Archived previous log to local/logs/langgraph_dev_${ts}.log"
fi

# Kill any existing process on port 2024
if lsof -i :2024 >/dev/null 2>&1; then
    echo "Killing existing process on port 2024..."
    lsof -ti :2024 | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# Activate venv if not already active
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f venv/bin/activate ]; then
        source venv/bin/activate
        echo "Activated venv"
    fi
fi

echo "Starting langgraph dev..."
echo "Logging to local/logs/langgraph_dev.log"
echo "Save thread outputs:  python3 local/scripts/save_thread.py <thread_id>"
echo ""

langgraph dev 2>&1 | tee local/logs/langgraph_dev.log
