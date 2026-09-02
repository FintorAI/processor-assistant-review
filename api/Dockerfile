# Lambda + Fargate container for processor-assistant-review.
# Build context is the repo root.
FROM public.ecr.aws/docker/library/python:3.11-slim

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /var/task

ENV PORT=8080
ENV AWS_LWA_INVOKE_MODE=response_stream
ENV AWS_LWA_READINESS_CHECK_PATH=/health
ENV REVIEW_LOG_DIR=/tmp/review-logs
# Python path: output/ is the agent package root for tools/plans imports
ENV PYTHONPATH=/var/task:/var/task/output

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
COPY api/requirements.txt ./api-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r api-requirements.txt

# Agent code — layout mirrors langgraph.json entrypoints under output/
COPY output/ ./output/
COPY shared/ ./shared/
COPY encompass_client.py ./encompass_client.py
COPY api/ ./api/

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
