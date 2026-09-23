#!/bin/bash
# HPA load test: sends rapid POST requests to trigger CPU-based autoscaling
# Usage: ./load_test.sh [HOST] [DURATION_SECONDS]

HOST="${1:-http://localhost:8000}"
DURATION="${2:-120}"

echo "Sending load to $HOST for ${DURATION}s..."
END=$((SECONDS + DURATION))

while [ $SECONDS -lt $END ]; do
  curl -s -X POST "$HOST/notes" \
    -H "content-type: application/json" \
    -d '{"title":"load-test","content":"testing HPA scaling"}' \
    -o /dev/null
done

echo "Done. Check HPA status with:"
echo "  kubectl get hpa -n <namespace>"
echo "  kubectl get pods -n <namespace>"
